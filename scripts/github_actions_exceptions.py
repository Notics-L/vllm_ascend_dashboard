#!/usr/bin/env python3
"""Print the daily workflow summary and exceptional GitHub Actions jobs.

Usage:
    python scripts/github_actions_exceptions.py <run-url>

Optional authentication:
    PowerShell: $env:GITHUB_ACTIONS_REPORT_TOKEN = "<read-only-token>"

The report includes failed, queued, cancelled, and skipped jobs. The two
housekeeping jobs ``clear-pre-logs`` and
``single-node-accuracy-tests-pr-only`` are excluded.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FO_MAP_PATH = REPO_ROOT / "data" / "model_fo_map.json"
DEFAULT_VLLM_ASCEND_ROOT = REPO_ROOT.parent / "vllm-ascend"
EXCLUDED_JOB_NAMES = {"clear-pre-logs", "single-node-accuracy-tests-pr-only"}
BUILD_JOB_PATTERN = re.compile(
    r"^Build nightly-(?:a[235]|310p) image(?:\b|\s|\()", re.IGNORECASE
)


def parse_run_url(run_url: str) -> tuple[str, str, str]:
    match = re.fullmatch(
        r"https?://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)/?",
        run_url.strip(),
    )
    if not match:
        raise ValueError(
            "链接必须是 https://github.com/<owner>/<repo>/actions/runs/<run_id>"
        )
    owner_repo, run_id = match.groups()
    return owner_repo, run_id, run_url.rstrip("/")


def _is_github_api(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc.lower() == "api.github.com"


class _GitHubReportRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise URLError("GitHub redirect requires HTTPS")
        redirect = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirect is not None and not _is_github_api(newurl):
            redirect.remove_header("Authorization")
        return redirect


_GITHUB_OPENER = build_opener(_GitHubReportRedirect())


def _github_request(url: str) -> Request:
    if not _is_github_api(url):
        raise ValueError("Expected the GitHub API HTTPS origin")
    token = os.environ.get("GITHUB_ACTIONS_REPORT_TOKEN", "").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "vllm-ascend-dashboard-actions-report",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(url, headers=headers)


def github_get(url: str) -> dict | list:
    request = _github_request(url)
    try:
        with _GITHUB_OPENER.open(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError(
                "GitHub API 请求受限，请稍后重试或配置 GITHUB_ACTIONS_REPORT_TOKEN 环境变量"
            ) from exc
        raise RuntimeError(f"GitHub API 请求失败：HTTP {exc.code}") from exc
    except URLError:
        raise RuntimeError("无法访问 GitHub API") from None


def github_get_bytes(url: str) -> bytes:
    request = _github_request(url)
    try:
        with _GITHUB_OPENER.open(request, timeout=60) as response:
            return response.read()
    except HTTPError as exc:
        raise RuntimeError(f"GitHub 日志请求失败：HTTP {exc.code}") from exc
    except URLError:
        raise RuntimeError("无法访问 GitHub 日志") from None


def fetch_run(owner_repo: str, run_id: str) -> dict:
    payload = github_get(f"https://api.github.com/repos/{owner_repo}/actions/runs/{run_id}")
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub Actions run 返回格式无效")
    return payload


def fetch_job_logs(owner_repo: str, job_id: int) -> str:
    payload = github_get_bytes(
        f"https://api.github.com/repos/{owner_repo}/actions/jobs/{job_id}/logs"
    )
    archive = io.BytesIO(payload)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zipped:
            return "\n".join(
                zipped.read(name).decode("utf-8", errors="replace")
                for name in zipped.namelist()
            )
    return payload.decode("utf-8", errors="replace")


def extract_version_info(log_text: str) -> dict[str, str]:
    """Extract vLLM/CANN information from the standard nightly log steps."""
    unknown = "\u672a\u83b7\u53d6"
    versions = {"vllm": unknown, "vllm_commit": unknown, "cann": unknown}
    version_pattern = r"v?(\d+\.\d+(?:\.\d+){0,2}(?:[-+._A-Za-z0-9]*)?)"
    section = ""
    cann_context = False

    def normalize_vllm(value: str) -> str:
        return value if value.lower().startswith("v") else f"v{value}"

    def normalize_cann(value: str) -> str:
        return value.lstrip("vV")

    for line in log_text.splitlines():
        lower = line.lower().strip()

        if "vllm-ascend git information" in lower:
            section = "vllm-ascend"
        elif "vllm git information" in lower and "ascend" not in lower:
            section = "vllm"
        elif "installed vllm-related python packages" in lower:
            section = "packages"

        sha_match = re.search(r"\b[0-9a-f]{40}\b", line, re.IGNORECASE)
        if sha_match and section == "vllm" and "commit" in lower:
            versions["vllm_commit"] = sha_match.group(0)
        elif sha_match and "vllm" in lower and "ascend" not in lower:
            if "commit" in lower or "sha" in lower:
                versions["vllm_commit"] = sha_match.group(0)

        package_match = re.search(
            rf"\bvllm(?!-ascend)(?:\s+|==?|:)\s*({version_pattern})",
            line,
            re.IGNORECASE,
        )
        if package_match and versions["vllm"] == unknown:
            versions["vllm"] = normalize_vllm(package_match.group(1))

        if "vllm.__version__" in lower or "vllm version" in lower:
            match = re.search(version_pattern, line, re.IGNORECASE)
            if match and versions["vllm"] == unknown:
                versions["vllm"] = normalize_vllm(match.group(1))

        if "cann" in lower or "ascend_toolkit_install.info" in lower:
            cann_context = True

        cann_match = re.search(
            rf"\bcann(?:[_ -]?version)?\s*[:=]\s*({version_pattern})",
            line,
            re.IGNORECASE,
        )
        if cann_match is None:
            cann_match = re.search(
                rf"\bcann[:/]({version_pattern})",
                line,
                re.IGNORECASE,
            )
        if cann_match is None and cann_context and "version" in lower:
            cann_match = re.search(
                rf"\bversion\s*[:=]\s*({version_pattern})",
                line,
                re.IGNORECASE,
            )
        if cann_match and versions["cann"] == unknown:
            versions["cann"] = normalize_cann(cann_match.group(1))

    return versions


def build_run_summary(run: dict, jobs: list[dict], owner_repo: str) -> str:
    unknown = "\u672a\u83b7\u53d6"
    created_at = run.get("created_at") or run.get("run_started_at")
    if created_at:
        timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        local_time = timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
        date_label = f"{local_time.month}/{local_time.day}"
    else:
        date_label = unknown

    branch = str(run.get("head_branch") or unknown)
    branch_label = branch.replace("/", "-")
    head_sha = str(run.get("head_sha") or unknown)
    info = {"vllm": unknown, "vllm_commit": unknown, "cann": unknown}

    candidates = [
        job
        for job in jobs
        if any(
            "vllm" in str(step.get("name", "")).lower()
            or "cann" in str(step.get("name", "")).lower()
            for step in (job.get("steps") or [])
        )
    ]
    for job in candidates[:10]:
        try:
            extracted = extract_version_info(fetch_job_logs(owner_repo, int(job["id"])))
        except (HTTPError, RuntimeError, OSError, ValueError, zipfile.BadZipFile):
            continue
        for key, value in extracted.items():
            if value != unknown:
                info[key] = value
        if all(value != unknown for value in info.values()):
            break

    run_link = str(
        run.get("html_url")
        or f"https://github.com/{owner_repo}/actions/runs/{run.get('id', '')}"
    )
    return (
        f"【{date_label} vllm_ascend 社区 {branch_label}分支 nightly 冒烟】\n"
        f"vllm-ascend: {branch_label} 【{head_sha}】\n"
        f"vllm: {info['vllm']}【{info['vllm_commit']}】\n"
        f"cann: {info['cann']}\n"
        f"{run_link}"
    )


def load_fo_map() -> dict[str, str]:
    with FO_MAP_PATH.open(encoding="utf-8") as file:
        data = json.load(file)
    return {str(key): str(value) for key, value in data.items() if not key.startswith("_")}


def config_name(job_name: str) -> str:
    """Get the config/model token from GitHub's matrix job display name."""
    config_files = re.findall(r"[^\s/(),]+\.yaml", job_name)
    if config_files:
        return config_files[-1]
    value = job_name.rsplit(" / ", 1)[-1].strip()
    return value if value.endswith(".yaml") else f"{value}.yaml"


def model_name(job_name: str) -> str:
    value = job_name.rsplit(" / ", 1)[-1].strip()
    return value.removesuffix(".yaml")


def load_nightly_machine_map(vllm_ascend_root: Path) -> dict[str, str]:
    """Load generation/node-count mapping from vllm-ascend nightly_config.yaml."""
    config_path = vllm_ascend_root / ".github" / "workflows" / "configs" / "nightly_config.yaml"
    if not config_path.exists():
        return {}

    with config_path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    node_labels = {1: "单机", 2: "双机", 3: "三机", 4: "四机"}
    result: dict[str, str] = {}
    for generation_key in ("a2", "a3"):
        generation = generation_key.upper()
        generation_config = config.get(generation_key) or {}
        for _section_name, section in generation_config.items():
            if not isinstance(section, dict):
                continue
            for case in section.get("test_config", []) or []:
                if not isinstance(case, dict) or not case.get("name"):
                    continue
                size = case.get("size", 1)
                try:
                    node_count = int(size)
                except (TypeError, ValueError):
                    node_count = 1
                result[str(case["name"])] = f"{generation}{node_labels.get(node_count, f'{node_count}机')}"
    return result


def machine_type(run: dict, job_name: str, nightly_machine_map: dict[str, str]) -> str:
    """Return the generation/node count from nightly_config.yaml."""
    for case_name in sorted(nightly_machine_map, key=len, reverse=True):
        if case_name in job_name:
            return nightly_machine_map[case_name]

    # Fallback for housekeeping jobs or jobs introduced before the config was updated.
    workflow_name = str(run.get("name") or run.get("workflow_name") or "")
    generation_match = re.search(r"\b(A[23])\b", workflow_name, re.IGNORECASE)
    if generation_match is None:
        generation_match = re.search(r"(?<![A-Za-z])(A[23])(?:[-_]|$)", job_name, re.IGNORECASE)
    generation = generation_match.group(1).upper() if generation_match else ""

    if re.search(r"\bdouble-node\b", job_name, re.IGNORECASE):
        nodes = "双机"
    elif re.search(r"\bsingle-node(?:\b|-)", job_name, re.IGNORECASE):
        nodes = "单机"
    elif re.search(r"\bmulti-node\b", job_name, re.IGNORECASE):
        matrix_nodes = re.search(r",\s*(\d+)\)\s*/", job_name)
        nodes = f"{matrix_nodes.group(1)}机" if matrix_nodes else "四机"
    else:
        return "未配置"

    return f"{generation}{nodes}" if generation else nodes


def fo_for_job(job_name: str, fo_map: dict[str, str]) -> str:
    config = config_name(job_name)
    if config in fo_map:
        return fo_map[config]

    lowered = config.casefold()
    for key, value in fo_map.items():
        if key.casefold() == lowered:
            return value
    return "\u672a\u914d\u7f6e"


def conclusion_label(job: dict) -> str | None:
    status = job.get("status")
    conclusion = job.get("conclusion")
    if status in {"queued", "waiting", "pending"}:
        return "\u6392\u961f\u4e2d"
    if conclusion == "failure":
        return "\u5931\u8d25"
    if conclusion == "cancelled":
        return "\u5df2\u53d6\u6d88"
    if conclusion == "skipped":
        return "\u672a\u6267\u884c（Skipped）"
    return None


def fetch_jobs(owner_repo: str, run_id: str) -> list[dict]:
    jobs: list[dict] = []
    page = 1
    while True:
        payload = github_get(
            f"https://api.github.com/repos/{owner_repo}/actions/runs/{run_id}/jobs"
            f"?per_page=100&page={page}"
        )
        page_jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        jobs.extend(page_jobs)
        if len(page_jobs) < 100:
            return jobs
        page += 1


def is_excluded(job_name: str) -> bool:
    short_name = job_name.rsplit(" / ", 1)[-1].strip()
    return (
        short_name in EXCLUDED_JOB_NAMES
        or job_name in EXCLUDED_JOB_NAMES
        or bool(BUILD_JOB_PATTERN.match(job_name))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="输出 GitHub Actions 异常 Job 报告")
    parser.add_argument("run_url", help="GitHub Actions workflow run 链接")
    parser.add_argument(
        "--vllm-ascend-root",
        default=os.environ.get("VLLM_ASCEND_ROOT", str(DEFAULT_VLLM_ASCEND_ROOT)),
        help="vllm-ascend 代码仓根目录，用于读取 nightly_config.yaml",
    )
    args = parser.parse_args()

    try:
        owner_repo, run_id, _ = parse_run_url(args.run_url)
        fo_map = load_fo_map()
        nightly_machine_map = load_nightly_machine_map(Path(args.vllm_ascend_root))
        run = fetch_run(owner_repo, run_id)
        jobs = fetch_jobs(owner_repo, run_id)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    selected = []
    for job in jobs:
        name = str(job.get("name", "")).strip()
        status = conclusion_label(job)
        if not name or not status or is_excluded(name):
            continue
        selected.append((job, status))

    order = {"失败": 0, "已取消": 1, "排队中": 2, "未执行（Skipped）": 3}
    selected.sort(key=lambda item: (order[item[1]], item[0].get("name", "")))

    print(build_run_summary(run, jobs, owner_repo))
    print()

    for job, status in selected:
        name = job["name"].strip()
        model = model_name(name)
        machine = machine_type(run, name, nightly_machine_map)
        fo = fo_for_job(name, fo_map)
        url = job.get("html_url") or f"https://github.com/{owner_repo}/actions/runs/{run_id}/job/{job['id']}"
        print(f"状态：{status}")
        print(f"job名称: {name}")
        print(f"模型: {model}")
        print(f"机型: {machine}")
        print(f"模型FO: {fo}")
        print(f"日志链接: {url}")
        print()

    if not selected:
        print("未发现符合条件的 Job。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
