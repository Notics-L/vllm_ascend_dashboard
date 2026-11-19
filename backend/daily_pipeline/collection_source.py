"""Build the Daily run tree from scheduling jobs and actual test execution jobs."""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath

import yaml

from infrastructure.clients.daily_pipeline_collection import (
    CollectionSourceError,
    DailyCollectionClient,
)

logger = logging.getLogger(__name__)
NIGHTLY_CONFIG_PATH = ".github/workflows/configs/nightly_config.yaml"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")
TEST_STEPS = {
    "Run Pytest (py-driven)",
    "Run Pytest (YAML-driven)",
    "Stream logs",
    "Run vllm-project/vllm-ascend accuracy test",
}
SELECTED_TEST_STEPS = {"Run selected tests with device", "Run selected tests without device"}
DISPATCH_JOB = re.compile(r"^(?:.* / )?Dispatch Nightly-(A3-560T|A2|A3|A5) daily subset$", re.I)
WORKFLOWS = {
    "A2": "schedule_nightly_test_a2.yaml",
    "A3": "schedule_nightly_test_a3.yaml",
    "A3-560T": "schedule_nightly_test_a3_560t.yaml",
    "A5": "schedule_nightly_test_a5.yaml",
}


def utc_datetime(value):
    if not value:
        return None
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


def iso_time(value):
    parsed = utc_datetime(value)
    return parsed.isoformat(timespec="seconds") + "Z" if parsed else None


def execution_result(item):
    if item.get("status") != "completed":
        return "running" if item.get("status") == "in_progress" else "queued"
    conclusion = item.get("conclusion")
    if conclusion in ("failure", "timed_out", "action_required", "stale", "startup_failure"):
        return "failure"
    if conclusion in ("skipped", "neutral"):
        return "skipped"
    return conclusion if conclusion in ("success", "cancelled") else "failure"


def clean_lines(text):
    for line in text.splitlines():
        yield TIMESTAMP.sub("", ANSI.sub("", line)).strip()


def case_definitions(config, hardware):
    definitions = {}

    def visit(value):
        if isinstance(value, dict):
            if value.get("name") and any(
                key in value for key in ("config_file_path", "tests", "model_list")
            ):
                definitions[value["name"]] = value
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit((config or {}).get(hardware.lower(), {}))
    return definitions


def matches_case(job, name, definition):
    tail = job["name"].rsplit(" / ", 1)[-1]
    identities = (name, definition.get("config_file_path"), definition.get("tests"))
    if tail in identities:
        return True
    if tail.endswith(" accuracy test") and definition.get("model_list") is not None:
        try:
            models = json.loads(tail.removesuffix(" accuracy test"))
            expected = definition["model_list"]
            if isinstance(expected, str):
                expected = json.loads(expected)
            return models == expected
        except (ValueError, TypeError):
            pass
    # Skipped reusable jobs can expose an unresolved expression as their tail.
    # Their matrix still contains the exact configured name as its second item.
    matrix = re.match(r"^[^(]+\([^,]+,\s*([^,]+),", job["name"])
    unresolved_test = re.match(r"\$\{\{\s*inputs\.(?:name|config_file_path|tests|model_list)\b", tail)
    return bool(unresolved_test and matrix and matrix.group(1).strip() == name)


def run_record(run, collected_at):
    return {
        "run_id": int(run["id"]),
        "workflow": PurePosixPath(run.get("path") or "").name,
        "result": execution_result(run),
        "started_at": utc_datetime(run.get("run_started_at") or run.get("created_at")),
        "finished_at": utc_datetime(run.get("updated_at"))
        if run.get("status") == "completed"
        else None,
        "last_collected_at": collected_at,
        "issue_number": None,
        "issue_state": None,
    }


def collection_node(ref, parent_ref, kind, source_id, name, result, **metadata):
    return {
        "ref": ref, "parent_ref": parent_ref, "node_type": kind,
        "source_id": source_id, "name": name, "result": result,
        "case_type": metadata.get("case_type"), "hardware": metadata.get("hardware"),
        "vllm_version": metadata.get("vllm_version"),
    }


def aggregate_result(results):
    values = set(results)
    return next(
        (result for result in (
            "failure", "running", "queued", "cancelled", "success", "skipped",
        ) if result in values),
        "queued",
    )


def selected_test_metadata(name):
    match = re.fullmatch(r"run-all-tests(?: \(([^)]+)\))? / (.+)", name)
    if not match:
        return None
    ref, partition = match.groups()
    hardware_match = re.match(r"(.+?)-[0-9]+ card-", partition)
    if hardware_match is None:
        return None
    hardware = hardware_match.group(1).upper()
    return {
        "hardware": hardware,
        # select_tests.py routes UT to CPU and device E2E to NPU partitions.
        "case_type": "UT" if hardware == "CPU" else "E2E",
        "vllm_version": (
            "main" if re.fullmatch(r"[0-9a-f]{7,64}|main", ref or "")
            else "release" if ref else None
        ),
    }


def call_node_type(name):
    """Expanded matrix calls expose their parameter values in parentheses."""
    # GitHub can truncate long expanded names before the closing parenthesis.
    return "matrix" if " (" in name else "workflow_call"


def main_job_nodes(jobs):
    """Keep top-level jobs and only testing leaves of run-all-tests groups."""
    groups = {}
    for job in jobs:
        outer, separator, _ = job["name"].partition(" / ")
        identity = ("group", outer) if separator else ("job", int(job["id"]))
        groups.setdefault(identity, []).append(job)
    nodes = []
    for (kind, identity), members in groups.items():
        if kind == "job":
            job = members[0]
            nodes.append(collection_node(
                f"job:{identity}", None, "job", identity, job["name"], execution_result(job),
            ))
            continue
        parent_ref = f"job-group:{identity}"
        nodes.append(collection_node(
            parent_ref, None, call_node_type(identity), None, identity,
            aggregate_result(execution_result(job) for job in members),
        ))
        if not re.fullmatch(r"run-all-tests(?: \([^)]+\))?", identity):
            continue
        for job in members:
            metadata = selected_test_metadata(job["name"])
            if metadata is None:
                # Queued/skipped jobs can lack steps; their partition name is
                # still sufficient to identify an actual selected-tests job.
                if not any(step.get("name") in SELECTED_TEST_STEPS for step in job.get("steps", [])):
                    continue
                metadata = {"case_type": "UT/E2E"}
            nodes.append(collection_node(
                f"job:{job['id']}", parent_ref, "test_job", int(job["id"]),
                job["name"].split(" / ", 1)[1], execution_result(job), **metadata,
            ))
    return nodes


class DailyCollectionSource:
    def __init__(self, repository: str, token: str):
        self.repository = repository
        self.token = token

    async def collect_snapshot(self, history: dict, known_runs: list[dict]):
        try:
            async with DailyCollectionClient(self.repository, self.token) as client:
                return await self._collect(client, history, known_runs)
        except CollectionSourceError:
            raise
        except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
            logger.error(
                "Could not parse Daily collection source run_id=%s error_type=%s",
                history.get("run_id"),
                type(exc).__name__,
            )
            raise CollectionSourceError("GitHub 流水线结果格式无法解析") from None

    async def _collect(self, client, history, known_runs):
        root_id = int(history["run_id"])
        root = await client.run(root_id)
        root_jobs = await client.jobs(root_id, int(root.get("run_attempt", 1)))
        collected_at = datetime.now(UTC).replace(tzinfo=None)
        children, pending = [], False
        job_cache = {root_id: root_jobs}
        log_cache = {}
        for job in root_jobs:
            match = DISPATCH_JOB.fullmatch(job["name"])
            if not match or job.get("conclusion") == "skipped":
                continue
            hardware = match.group(1).upper()
            dispatch_steps = [
                step
                for step in job.get("steps", [])
                if re.match(r"Dispatch nightly-.* with daily subset", step.get("name", ""), re.I)
            ]
            expected = job.get("conclusion") == "success" or any(
                step.get("conclusion") == "success" for step in dispatch_steps
            )
            if job.get("status") != "completed" and not expected:
                continue
            logs = await client.job_logs(int(job["id"]))
            log_cache[job["id"]] = logs
            pattern = (
                r"https://github\.com/"
                + re.escape(self.repository)
                + r"/actions/runs/([1-9][0-9]*)(?:[/?\s]|$)"
            )
            ids = {int(value) for value in re.findall(pattern, logs or "") if int(value) != root_id}
            child = None
            for child_id in sorted(ids, reverse=True):
                candidate = await client.run(child_id)
                if PurePosixPath(candidate.get("path", "")).name == WORKFLOWS[hardware]:
                    child = candidate
                    break
            if child is None and expected:
                child = await self._find_child(
                    client, root, hardware, history, known_runs, job_cache, log_cache
                )
            if child is None:
                pending = pending or expected
                continue
            children.append((hardware, child))
        record = run_record(root, collected_at)
        nodes = main_job_nodes(root_jobs)
        child_finished_at = []
        seen_children = set()
        for hardware, child in children:
            child_id = int(child["id"])
            if child_id in seen_children:
                continue
            seen_children.add(child_id)
            child_jobs = job_cache.get(child_id)
            if child_jobs is None:
                child_jobs = await client.jobs(child_id, int(child.get("run_attempt", 1)))
            child_ref = f"workflow:{child_id}"
            nodes.append(collection_node(
                child_ref, None, "workflow_run", child_id,
                PurePosixPath(child.get("path") or "").name, execution_result(child),
            ))
            child_record = run_record(child, collected_at)
            if child_record["finished_at"]:
                child_finished_at.append(child_record["finished_at"])
            selected = [
                case for case in history.get("daily_cases", [])
                if case.get("hardware", "").upper() == hardware
            ]
            config = None
            definitions = {}
            matched_jobs = {}
            call_groups = set()
            for case in selected:
                matching = [job for job in child_jobs if matches_case(job, case["name"], {})]
                if not matching:
                    if config is None:
                        config = yaml.safe_load(
                            await client.content(NIGHTLY_CONFIG_PATH, child["head_sha"])
                        ) or {}
                        definitions = case_definitions(config, hardware)
                    matching = [
                        job for job in child_jobs
                        if matches_case(job, case["name"], definitions.get(case["name"], {}))
                    ]
                for job in matching:
                    job_id = int(job["id"])
                    previous = matched_jobs.get(job_id)
                    if previous is not None:
                        if previous != case["name"]:
                            raise CollectionSourceError("同一个测试 Job 对应多个 Daily 用例")
                        continue
                    matched_jobs[job_id] = case["name"]
                    parent_ref = child_ref
                    parts = job["name"].split(" / ")[:-1]
                    for depth, name in enumerate(parts, start=1):
                        prefix = " / ".join(parts[:depth])
                        group_ref = f"job-group:{child_id}:{prefix}"
                        if group_ref not in call_groups:
                            # Omitted auxiliary leaves still affect the actual
                            # calling group's aggregate execution result.
                            members = (
                                execution_result(member) for member in child_jobs
                                if member["name"].startswith(prefix + " / ")
                            )
                            nodes.append(collection_node(
                                group_ref, parent_ref, call_node_type(name), None,
                                name, aggregate_result(members),
                            ))
                            call_groups.add(group_ref)
                        parent_ref = group_ref
                    nodes.append(collection_node(
                        f"job:{job_id}", parent_ref, "test_job", job_id, case["name"],
                        execution_result(job), case_type="Daily", hardware=hardware,
                    ))
        known_root = next((run for run in known_runs if int(run["run_id"]) == root_id), {})
        issue = (
            await client.issue(known_root["issue_number"])
            if known_root.get("issue_number")
            else await client.find_issue(
                root_id, iso_time(history.get("triggered_at") or root.get("created_at"))
            )
        )
        if issue:
            record.update(issue_number=int(issue["number"]), issue_state=issue["state"], issue_title=issue.get("title"), issue_closed_at=utc_datetime(issue.get("closed_at")))
        complete = (
            not pending
            and root.get("status") == "completed"
            and all(child.get("status") == "completed" for _, child in children)
        )
        if complete:
            record["finished_at"] = max(
                [value for value in [record["finished_at"], *child_finished_at] if value],
                default=None,
            )
        else:
            record["finished_at"] = None
        results = [record["result"], *(node["result"] for node in nodes)]
        if "failure" in results:
            record["result"] = "failure"
        elif not complete:
            record["result"] = "queued" if root.get("status") == "queued" else "running"
        else:
            record["result"] = aggregate_result(results)
        record["last_collected_at"] = datetime.now(UTC).replace(tzinfo=None)
        logger.info(
            "Collected GitHub Daily tree run_id=%s workflows=%d nodes=%d tests=%d complete=%s",
            root_id, len(seen_children) + 1, len(nodes),
            sum(node["node_type"] == "test_job" for node in nodes), complete,
        )
        raw_jobs = {int(job["id"]): job for jobs in job_cache.values() for job in jobs}
        for node in nodes:
            if node["node_type"] == "test_job":
                raw = raw_jobs.get(node["source_id"], {})
                node["execution_started_at"] = utc_datetime(raw.get("started_at"))
                node["failed_at"] = utc_datetime(raw.get("completed_at"))
        return {"run": record, "nodes": nodes, "complete": complete}

    async def _find_child(self, client, root, hardware, history, known_runs, job_cache, log_cache):
        workflow = WORKFLOWS[hardware]
        identity = f"daily-{root['id']}-{root.get('run_attempt', 1)}"
        candidates = {
            int(run["run_id"]): run
            for run in known_runs
            if run.get("parent_run_id") == int(root["id"]) and run.get("workflow") == workflow
        }
        remote = await client.workflow_runs(
            workflow,
            history["source_branch"],
            iso_time(root.get("run_started_at") or root["created_at"]),
        )
        candidates.update({int(run["id"]): run for run in remote})
        for candidate_id in sorted(candidates, reverse=True):
            candidate = candidates[candidate_id]
            if "id" not in candidate:
                candidate = await client.run(candidate_id)
            groups = await client.concurrency_groups(candidate_id)
            if any(group.get("group_name", "").endswith("-" + identity) for group in groups):
                return candidate
            candidate_jobs = await client.jobs(candidate_id, int(candidate.get("run_attempt", 1)))
            job_cache[candidate_id] = candidate_jobs
            for job in candidate_jobs:
                if not any(step.get("name") in TEST_STEPS for step in job.get("steps", [])):
                    continue
                logs = await client.job_logs(int(job["id"]))
                log_cache[job["id"]] = logs
                if any(
                    re.fullmatch(
                        r"(?:request_id|REQUEST_ID|request-id):\s*['\"]?"
                        + re.escape(identity)
                        + r"['\"]?",
                        line,
                    )
                    for line in clean_lines(logs or "")
                ):
                    return candidate
                break
        return None
