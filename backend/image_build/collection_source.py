"""Read image build runs, jobs and publication reports from GitHub Actions."""

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

import httpx

from image_build.workflow_sync import WORKFLOW_PATH
from infrastructure.clients.github_http import GitHubAsyncClient
from infrastructure.core.github_credentials import GitHubCredential

WORKFLOW_NAME = "Nightly Image Build (with base)"
RESULT_LABELS = {
    "queued": "排队中", "running": "执行中", "success": "成功", "failure": "失败",
    "cancelled": "已取消", "skipped": "已跳过", "unavailable": "未获取",
}
_MATRIX_JOB = re.compile(r"^Build nightly-([A-Za-z0-9_-]+) \(([^)]+)\)$", re.I)
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-fA-F]{64}")
_PAGE_SIZE = 100
_MAX_JOB_PAGES = 50
_MAX_ARCHIVE_SIZE = 4 * 1024 * 1024
_MAX_REPORT_SIZE = 1024 * 1024
_OS_LABELS = {"ubuntu": "Ubuntu", "openeuler": "openEuler"}
_STAGE_PRIORITY = ("failure", "running", "queued", "cancelled", "success", "skipped")
_CHIP_ORDER = {"A2": 0, "A3": 1, "310P": 2, "A5": 3}
_FAILURES = {"failure", "timed_out", "startup_failure", "action_required", "stale"}
_WAITING = {"requested", "queued", "pending", "waiting"}


def github_client(credential: GitHubCredential) -> httpx.AsyncClient:
    return GitHubAsyncClient(
        audit_source="image_build_collection", credential=credential,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=httpx.Timeout(12, connect=5),
    )


def utc_time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def result_key(status: str, conclusion: str | None) -> str:
    if status in _WAITING:
        return "queued"
    if status != "completed":
        return "running"
    if conclusion in _FAILURES:
        return "failure"
    if conclusion in ("skipped", "neutral"):
        return "skipped"
    return conclusion if conclusion in ("success", "cancelled") else "unavailable"


async def get_run(client: httpx.AsyncClient, repository: str, run_id: int) -> dict[str, Any]:
    response = await client.get(f"/repos/{repository}/actions/runs/{run_id}")
    response.raise_for_status()
    run = response.json()
    if (
        not isinstance(run, dict) or run.get("id") != run_id or run.get("path") != WORKFLOW_PATH
        or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0
        or not isinstance(run.get("status"), str) or run["status"] not in _WAITING | {"in_progress", "completed"}
        or (run.get("name") is not None and not isinstance(run["name"], str))
        or (run.get("head_sha") is not None and (
            not isinstance(run["head_sha"], str) or not _SHA.fullmatch(run["head_sha"])
        ))
        or (run.get("conclusion") is not None and not isinstance(run["conclusion"], str))
    ):
        raise ValueError("GitHub 返回的构建运行记录无效")
    return run


async def list_jobs(client: httpx.AsyncClient, repository: str, run_id: int) -> list[dict[str, Any]]:
    # A failed-job rerun can omit successful jobs from earlier attempts.
    jobs = {}
    for page in range(1, _MAX_JOB_PAGES + 1):
        response = await client.get(
            f"/repos/{repository}/actions/runs/{run_id}/jobs",
            params={"filter": "all", "per_page": _PAGE_SIZE, "page": page},
        )
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise ValueError("GitHub 返回的构建 Job 列表无效")
        for job in entries:
            if (
                not isinstance(job, dict) or not isinstance(job.get("name"), str)
                or type(job.get("id")) is not int or job["id"] <= 0
                or not isinstance(job.get("status"), str)
                or (job.get("conclusion") is not None and not isinstance(job["conclusion"], str))
            ):
                raise ValueError("GitHub 返回的构建 Job 无效")
            key = job["id"]
            jobs[key] = job
        if "next" not in response.links and len(entries) < _PAGE_SIZE:
            return list(jobs.values())
    raise ValueError("GitHub 构建 Job 列表未完成分页")


def _job_order(job: dict[str, Any]) -> tuple[int, int]:
    attempt = job.get("run_attempt")
    return attempt if type(attempt) is int else 0, job["id"]


def _stage(jobs: list[dict[str, Any]], job_url: str) -> dict[str, Any]:
    if not jobs:
        return {"succeeded": 0, "total": 0, "result": RESULT_LABELS["unavailable"], "jobUrl": None}
    results = [result_key(job.get("status"), job.get("conclusion")) for job in jobs]
    conclusion = next((value for value in _STAGE_PRIORITY if value in results), "unavailable")
    representative = next(
        (job for job, result in zip(jobs, results, strict=True) if result == "failure"), None,
    )
    if representative is None:
        representative = next((
            job for job in jobs
            if job["name"].endswith(" / merge-image") or " / Merge multi-arch " in job["name"]
        ), jobs[0])
    return {
        "succeeded": results.count("success"), "total": len(jobs),
        "result": RESULT_LABELS[conclusion], "jobUrl": f"{job_url}/{representative['id']}",
    }


def build_matrix(jobs: list[dict[str, Any]], run_url: str, attempt: int) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for job in jobs:
        if _job_order(job)[0] > attempt:
            continue
        previous = latest.get(job["name"])
        if previous is None or _job_order(job) > _job_order(previous):
            latest[job["name"]] = job

    groups = defaultdict(lambda: {"base": [], "nightly": []})
    for name, job in latest.items():
        parts = name.split(" / ")
        target = _MATRIX_JOB.fullmatch(parts[0])
        if not target or len(parts) < 2:
            continue
        chip, os_name = target.groups()
        os_name = _OS_LABELS.get(os_name.lower(), os_name)
        stage = "base" if parts[1].lower().startswith("build base image") else "nightly"
        groups[chip.upper(), os_name][stage].append(job)

    matrix = []
    for (chip, os_name), stages in sorted(
        groups.items(), key=lambda item: (_CHIP_ORDER.get(item[0][0], len(_CHIP_ORDER)), item[0][0], item[0][1].lower() != "ubuntu", item[0][1]),
    ):
        # A skipped parent is superseded when its dependent jobs actually run on a later attempt.
        for stage, items in stages.items():
            if any(len(item["name"].split(" / ")) > 2 for item in items):
                stages[stage] = [
                    item for item in items
                    if not (len(item["name"].split(" / ")) == 2 and item.get("conclusion") == "skipped")
                ]
        members = stages["base"] + stages["nightly"]
        timed_members = [job for job in members if _job_order(job)[0] == attempt] or members
        starts = [when for job in timed_members if (when := utc_time(job.get("started_at"))) is not None]
        ends = [when for job in timed_members if (when := utc_time(job.get("completed_at"))) is not None]
        started = min(starts, default=None)
        ended = max(ends, default=None) if all(job.get("status") == "completed" for job in members) else None
        matrix.append({
            "key": f"{chip}/{os_name}", "chip": chip, "os": os_name,
            "base": _stage(stages["base"], f"{run_url}/job"), "nightly": _stage(stages["nightly"], f"{run_url}/job"),
            "startedAt": iso_time(started), "endedAt": iso_time(ended),
        })
    return matrix


def publication_skipped(jobs: list[dict[str, Any]], attempt: int) -> bool:
    current = [job for job in jobs if _job_order(job)[0] in (0, attempt)]
    collect_jobs = [job for job in current if job["name"].split(" / ", 1)[0] == "collect-build-info"]
    if collect_jobs:
        return all(job.get("conclusion") == "skipped" for job in collect_jobs)
    return attempt == 1 and bool(current)


def parse_publication(archive: bytes, run_id: int, report_url: str) -> dict[str, Any]:
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            info = zipped.getinfo("build-info.json")
            if info.file_size > _MAX_REPORT_SIZE:
                raise ValueError("镜像发布汇总超出大小限制")
            with zipped.open(info) as stream:
                report = json.loads(stream.read(_MAX_REPORT_SIZE + 1))
    except (BadZipFile, KeyError, OSError, RuntimeError, UnicodeError, NotImplementedError) as exc:
        raise ValueError("GitHub 镜像发布压缩包无效") from exc
    if (
        not isinstance(report, dict) or report.get("run_id") != run_id
        or not isinstance(report.get("branch"), str) or not report["branch"]
        or not isinstance(report.get("build_type"), list)
    ):
        raise ValueError("build-info.json 与当前 Run 不匹配")

    images = []
    for kind, label in (("base", "Base"), ("nightly", "Nightly")):
        if kind not in report["build_type"]:
            continue
        group = report.get(kind)
        if not isinstance(group, dict) or not isinstance(group.get("images"), list):
            raise ValueError("build-info.json 缺少镜像列表")
        for image in group["images"]:
            if not isinstance(image, dict):
                raise ValueError("build-info.json 包含无效的镜像记录")
            chip, os_name, tag, digest, repositories, commits = (
                image.get(key) for key in ("hardware", "os", "tag", "digest", "repositories", "commits")
            )
            if (
                not all(isinstance(value, str) and value for value in (chip, os_name, tag, digest))
                or not _DIGEST.fullmatch(digest)
                or not isinstance(repositories, list) or not repositories
                or not all(isinstance(repository, str) and repository for repository in repositories)
                or not isinstance(commits, dict)
            ):
                raise ValueError("build-info.json 包含无效的发布信息")
            source_revisions = {}
            for arch in ("arm64", "amd64"):
                value = commits.get(arch)
                if value is not None and (
                    not isinstance(value, str) or (value and not _SHA.fullmatch(value))
                ):
                    raise ValueError("build-info.json 包含无效的源码 SHA")
                source_revisions[arch] = value or None
            images.append({
                "chip": chip.upper(), "os": _OS_LABELS.get(os_name.lower(), os_name), "layer": label,
                "repositories": repositories, "tag": tag, "digest": digest, "commits": source_revisions,
            })
    if not images:
        raise ValueError("build-info.json 未包含可展示的镜像")
    return {"sourceBranch": report["branch"], "reportUrl": report_url, "images": images}


async def read_publication(
    client: httpx.AsyncClient, repository: str, run_id: int, attempt: int, run_url: str,
) -> dict[str, Any] | None:
    name = f"build-info-run-{run_id}-{attempt}"
    response = await client.get(
        f"/repos/{repository}/actions/runs/{run_id}/artifacts",
        params={"name": name, "per_page": _PAGE_SIZE},
    )
    response.raise_for_status()
    payload = response.json()
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        raise ValueError("GitHub 返回的产物列表无效")
    matching = [
        item for item in artifacts
        if isinstance(item, dict) and item.get("name") == name and not item.get("expired")
        and type(item.get("id")) is int and item["id"] > 0
    ]
    if not matching:
        return None
    artifact_id = max(item["id"] for item in matching)
    # httpx removes Authorization when GitHub redirects to the artifact storage host.
    async with client.stream(
        "GET", f"/repos/{repository}/actions/artifacts/{artifact_id}/zip", follow_redirects=True,
    ) as archive:
        if archive.status_code in (404, 410):
            return None
        archive.raise_for_status()
        content = bytearray()
        async for chunk in archive.aiter_bytes(chunk_size=64 * 1024):
            if len(content) + len(chunk) > _MAX_ARCHIVE_SIZE:
                raise ValueError("镜像发布压缩包超出大小限制")
            content.extend(chunk)
    return parse_publication(bytes(content), run_id, f"{run_url}/artifacts/{artifact_id}")
