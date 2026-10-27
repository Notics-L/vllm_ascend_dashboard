"""Synchronize external coverage.py artifacts for the test quality board.

The upstream CI publishes ``coverage.tar`` containing coverage.py data files.
The dashboard reads each external artifact through coverage.py's public API and
persists the normalized result in MySQL through ``ProjectDashboardConfig``.
"""

from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import coverage
import httpx
from coverage import CoverageData
from coverage.parser import PythonParser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_cache import get_github_cache
from infrastructure.core.config import settings
from infrastructure.persistence.models import ProjectDashboardConfig

logger = logging.getLogger(__name__)

E2E_KEY = "e2e_feature_coverage"
PR_BREADTH_KEY = "pr_pipeline_coverage_breadth"
PR_LINES_KEY = "pr_pipeline_coverage_lines"
SYNC_STATUS_KEY = "coverage_sync_status"
GITHUB_ACTIONS_PREFIX = "/__w/vllm-ascend/vllm-ascend/"
LINE_ANALYSIS_VERSION = "triton-jit-exclusion-v6"
COVERAGE_SOURCE_PREFIX = "vllm-ascend/covstub/"

_coverage_sync_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _coverage_sync_lock
    if _coverage_sync_lock is None:
        _coverage_sync_lock = asyncio.Lock()
    return _coverage_sync_lock


def is_coverage_syncing() -> bool:
    return _coverage_sync_lock is not None and _coverage_sync_lock.locked()


def _coverage_archive_path(signature: str, kind: str) -> Path:
    """Store a downloaded source package by day and immutable source version."""
    root = Path(settings.DATA_DIR)
    if not root.is_absolute():
        root = Path.cwd() / root
    version = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return root.resolve() / "coverage" / datetime.now(UTC).strftime("%Y-%m-%d") / kind / version / "coverage.tar"


def _cleanup_coverage_archives() -> None:
    root = _coverage_archive_path("placeholder", "placeholder").parents[3]
    cutoff = datetime.now(UTC).date().toordinal() - max(1, settings.COVERAGE_ARCHIVE_RETENTION_DAYS)
    if not root.exists():
        return
    for child in root.iterdir():
        try:
            if datetime.strptime(child.name, "%Y-%m-%d").date().toordinal() < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except ValueError:
            continue


async def _load_config(db: AsyncSession, key: str) -> dict | None:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(ProjectDashboardConfig.config_key == key)
        )
    ).scalar_one_or_none()
    return dict(row.config_value) if row and row.config_value else None


async def _save_config(db: AsyncSession, key: str, value: dict, description: str) -> None:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(ProjectDashboardConfig.config_key == key)
        )
    ).scalar_one_or_none()
    if row:
        row.config_value = value
        row.description = description
    else:
        db.add(ProjectDashboardConfig(config_key=key, config_value=value, description=description))


def _find_literal_end(content: str, start: int) -> int:
    pairs = {"[": "]", "{": "}", "(": ")"}
    stack: list[str] = []
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in "'\"`":
            in_string = True
            quote = char
        elif char in pairs:
            stack.append(pairs[char])
        elif char in "]})":
            if not stack or stack.pop() != char:
                return -1
            if not stack:
                return index
    return -1


def extract_js_literal(content: str, variable: str, fallback: Any = None) -> Any:
    match = re.search(r"(?:const|let|var)\s+" + re.escape(variable) + r"\s*=\s*([\[{])", content)
    if not match:
        return fallback
    start = match.start(1)
    end = _find_literal_end(content, start)
    if end < 0:
        return fallback
    literal = content[start : end + 1]
    try:
        return json.loads(literal)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", literal))
        except json.JSONDecodeError:
            return fallback


class E2ECoverageParser:
    """Parse the checked-out E2E coverage snapshot when it is available."""

    HTML_REL_PATH = "tests/e2e/coverage.html"
    DIM_LABELS = {
        "arch": "Architecture",
        "feature": "Feature",
        "parallel": "Parallel",
        "deploy": "Deploy",
        "hardware": "Hardware",
        "quantization": "Quantization",
        "graph_mode": "Graph Mode",
    }

    def parse(self) -> dict:
        cache = get_github_cache()
        get_worktree = getattr(cache, "get_worktree", None)
        worktree = (
            get_worktree("origin/main", purpose="coverage")
            if callable(get_worktree)
            else cache.cache_dir
        )
        if worktree is None:
            raise FileNotFoundError("origin/main source worktree is unavailable")
        path = worktree / self.HTML_REL_PATH
        if not path.exists():
            raise FileNotFoundError(f"coverage.html not found: {path}")
        content = path.read_text(encoding="utf-8")
        raw_tests = extract_js_literal(content, "DATA", [])
        if not isinstance(raw_tests, list) or not raw_tests:
            raise ValueError("coverage.html DATA is empty or invalid")
        tests = []
        for item in raw_tests:
            coverage_map = item.get("coverage") or {}
            tests.append(
                {
                    "filepath": item.get("filepath", ""),
                    "test_name": item.get("test_name", ""),
                    "card_count": item.get("card_count", 1),
                    "models": item.get("models") or [],
                    "coverage": coverage_map,
                    "is_marked": any(isinstance(value, list) and value for value in coverage_map.values()),
                }
            )
        marked = sum(1 for item in tests if item["is_marked"])
        by_card: dict[str, int] = defaultdict(int)
        for item in tests:
            by_card[str(item["card_count"])] += 1
        return {
            "summary": {
                "total_tests": len(tests),
                "marked_tests": marked,
                "marked_ratio": round(marked / len(tests), 4),
                "by_card": dict(by_card),
            },
            "taxonomy": extract_js_literal(content, "ALLOWED", {}) or {},
            "dim_labels": extract_js_literal(content, "DIM_LABELS_JS", self.DIM_LABELS)
            or self.DIM_LABELS,
            "tests": tests,
            "source_file_hash": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            "repo_commit": _safe_latest_commit(cache),
            "updated_at": datetime.now(UTC).isoformat(),
        }


def decode_job_dir(job_dir: str) -> dict[str, Any]:
    sentinel = "\x00"
    decoded = job_dir.replace("___", sentinel).replace("__", "/").replace(sentinel, "/_")
    test_func = None
    if "--" in decoded:
        decoded, test_func = decoded.split("--", 1)
    # 上游 UT 任务目录为 cpu-ut（无 tests__ut__ 前缀），需单独识别
    is_ut = decoded.startswith("tests/ut/") or decoded == "cpu-ut" or decoded.startswith("cpu-ut/")
    test_type = "ut" if is_ut else (
        "e2e" if decoded.startswith("tests/e2e/") else "other"
    )
    return {"job_dir": job_dir, "test_path": decoded, "test_type": test_type, "test_func": test_func}


def parse_hw_from_filename(filename: str) -> tuple[str, int]:
    match = re.search(r"(?:linux-aarch64|linux-arm64)-([^-]+)-(\d+)-", filename)
    if not match:
        return "unknown", 0
    token = match.group(1).lower()
    hardware = {"a2b3": "A2", "a2": "A2", "a3": "A3", "310p": "310P", "a5": "A5"}.get(
        token, token.upper()
    )
    return hardware, int(match.group(2))


def clean_source_path(path: str) -> str:
    if path.startswith(GITHUB_ACTIONS_PREFIX):
        return path[len(GITHUB_ACTIONS_PREFIX) :]
    for prefix in ("vllm_ascend/", "csrc/", "tests/"):
        index = path.find(prefix)
        if index >= 0:
            return path[index:]
    return path


def module_of(path: str) -> str:
    parts = path.split("/")
    dirs = parts[:-1] if len(parts) > 1 and "." in parts[-1] else parts
    return "/".join(dirs[:2]) if len(dirs) >= 2 else (dirs[0] if dirs else path)


def _is_covdata_member(member: tarfile.TarInfo) -> bool:
    return (
        member.isfile()
        and not member.issym()
        and not member.islnk()
        and "/covdata/coverage." in member.name
    )


def _coverage_data_payload(data: CoverageData) -> dict[str, Any]:
    files = sorted(data.measured_files())
    return {
        "files": [clean_source_path(item) for item in files],
        "lines": {clean_source_path(item): sorted(data.lines(item) or []) for item in files},
        "arcs": {clean_source_path(item): sorted(data.arcs(item) or []) for item in files},
    }


def _read_covdata_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> dict[str, Any] | None:
    source = tar.extractfile(member)
    if source is None:
        return None
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="coverage_", suffix=".data", delete=False) as temp:
            temp_path = Path(temp.name)
            shutil.copyfileobj(source, temp)
        data = CoverageData(basename=str(temp_path), suffix=False)
        data.read()
        return _coverage_data_payload(data)
    except Exception as exc:  # coverage.py raises DataError for corrupt artifacts.
        logger.warning("Skipping unreadable coverage member %s: %s", member.name, exc)
        return None
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


def _iter_covdata(tar_path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    with tarfile.open(tar_path, "r") as tar:
        for member in tar:
            if not _is_covdata_member(member):
                continue
            data = _read_covdata_member(tar, member)
            if data is not None:
                yield member.name, data


def read_covdata(path: Path) -> dict[str, Any] | None:
    """Read one external coverage.py data file through its public API."""
    try:
        data = CoverageData(basename=str(path), suffix=False)
        data.read()
    except Exception as exc:
        logger.warning("Skipping unreadable coverage data %s: %s", path, exc)
        return None
    return _coverage_data_payload(data)


def _member_context(member_name: str) -> tuple[str, str] | None:
    parts = member_name.split("/")
    try:
        index = next(i for i, item in enumerate(parts) if item.startswith("VLLM-ASCEND@"))
        return parts[index].split("@", 1)[1], parts[index + 1]
    except (StopIteration, IndexError):
        return None


def _aggregate_raw_coverage(
    tar_path: Path, test_type: str | None = None
) -> tuple[dict[str, dict[str, set]], list[dict[str, Any]]]:
    lines: dict[str, set[int]] = defaultdict(set)
    arcs: dict[str, set[tuple[int, int]]] = defaultdict(set)
    jobs: dict[str, dict[str, Any]] = {}
    job_files: dict[str, set[str]] = defaultdict(set)
    for member_name, data in _iter_covdata(tar_path):
        context = _member_context(member_name)
        if context is None:
            continue
        task_id, job_dir = context
        decoded = decode_job_dir(job_dir)
        if test_type and test_type != "all" and decoded["test_type"] != test_type:
            continue
        hardware, cards = parse_hw_from_filename(member_name)
        job = jobs.setdefault(
            job_dir,
            {
                **decoded,
                "task_id": task_id,
                "hardware": hardware,
                "card_count": cards,
                "covdata_count": 0,
                "source_files_covered": 0,
                "arcs": 0,
                "latest_when": None,
                "_covered_paths": set(),
            },
        )
        job["covdata_count"] += 1
        job["arcs"] += sum(len(value) for value in data["arcs"].values())
        for path, values in data["lines"].items():
            lines[path].update(values)
            job_files[job_dir].add(path)
            job["_covered_paths"].add(path)
        for path, values in data["arcs"].items():
            arcs[path].update(tuple(item) for item in values if len(item) == 2)
        job["source_files_covered"] = len(job_files[job_dir])
    return {"lines": lines, "arcs": arcs}, list(jobs.values())


def _process_tar_breadth(tar_path: Path, signature: str) -> dict[str, Any]:
    raw, jobs = _aggregate_raw_coverage(tar_path)
    file_jobs: dict[str, set[str]] = defaultdict(set)
    file_hardware: dict[str, set[str]] = defaultdict(set)
    module_files: dict[str, set[str]] = defaultdict(set)
    for job in jobs:
        for path in job.pop("_covered_paths", set()):
            file_jobs[path].add(job["job_dir"])
            file_hardware[path].add(job["hardware"])
            module_files[module_of(path)].add(path)
    all_files = sorted(raw["lines"])
    by_type: dict[str, int] = defaultdict(int)
    by_hardware: dict[str, int] = defaultdict(int)
    for job in jobs:
        by_type[job["test_type"]] += 1
        by_hardware[job["hardware"]] += 1
    file_matrix = [
        {
            "source_path": path,
            "module": module_of(path),
            "covered_by_jobs": len(file_jobs[path]),
            "covered_by_hardware": sorted(file_hardware[path]),
        }
        for path in all_files
    ]
    return {
        "summary": {
            "total_jobs": len(jobs),
            "total_covdata_files": sum(int(job["covdata_count"]) for job in jobs),
            "total_source_files_covered": len(all_files),
            "total_arcs": sum(len(values) for values in raw["arcs"].values()),
            "by_test_type": dict(by_type),
            "by_hardware": dict(by_hardware),
        },
        "jobs": jobs,
        "file_matrix": file_matrix,
        "by_module": [
            {
                "module": module,
                "files": len(paths),
                "jobs_touching": len({job for path in paths for job in file_jobs[path]}),
            }
            for module, paths in sorted(module_files.items())
        ],
        "tar_signature": signature,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _is_triton_jit_decorator(node: ast.expr) -> bool:
    """Return whether *node* is ``@triton.jit`` (with or without arguments)."""
    target = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "jit"
        and isinstance(target.value, ast.Name)
        and target.value.id == "triton"
    )


def _triton_jit_excluded_lines(source: str, filename: str) -> set[int]:
    """Find complete source ranges for functions compiled by Triton.

    ``coverage.py`` traces Python execution.  A ``@triton.jit`` function is
    compiled and executed outside the Python interpreter, so including its
    Python body in the denominator makes line coverage misleading.  The
    function name is intentionally irrelevant: helpers and kernels are both
    excluded when they carry the decorator.
    """
    tree = ast.parse(source, filename=filename)
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_triton_jit_decorator(item) for item in node.decorator_list):
            continue
        start = min((item.lineno for item in node.decorator_list), default=node.lineno)
        end = node.end_lineno or node.lineno
        excluded.update(range(start, end + 1))
    return excluded


def _source_analysis(path: str, cache_dir: Path) -> tuple[set[int], set[int], set[tuple[int, int]], bool]:
    source_path = cache_dir / path
    if not source_path.is_file() or source_path.suffix != ".py":
        return set(), set(), set(), False
    try:
        source = source_path.read_text(encoding="utf-8", errors="replace")
        parser = PythonParser(
            text=source,
            filename=str(source_path),
        )
        parser.parse_source()
        excluded = set(getattr(parser, "excluded", set()))
        excluded.update(_triton_jit_excluded_lines(source, str(source_path)))
        statements = set(parser.statements) - excluded
        possible_arcs = {
            arc
            for arc in parser.arcs()
            if abs(arc[0]) not in excluded and abs(arc[1]) not in excluded
        }
        return statements, excluded, possible_arcs, True
    except Exception as exc:
        logger.warning("Cannot analyze source file %s: %s", path, exc)
        return set(), set(), set(), False


@contextmanager
def _embedded_coverage_source(tar_path: Path) -> Iterator[Path | None]:
    """Expose the exact source snapshot packaged alongside coverage data."""
    temp_dir = tempfile.TemporaryDirectory(prefix="coverage-source-")
    root = Path(temp_dir.name)
    found = False
    try:
        with tarfile.open(tar_path, "r") as archive:
            for member in archive:
                if not member.isfile() or not member.name.startswith(COVERAGE_SOURCE_PREFIX):
                    continue
                relative = member.name[len(COVERAGE_SOURCE_PREFIX):]
                parts = PurePosixPath(relative).parts
                if not relative or PurePosixPath(relative).is_absolute() or ".." in parts:
                    continue
                source = archive.extractfile(member)
                if source is None:
                    continue
                destination = root.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                found = True
        yield root if found else None
    finally:
        temp_dir.cleanup()


@contextmanager
def _coverage_source_tree(tar_path: Path) -> Iterator[tuple[Path, str | None, str]]:
    """Select source that is available with the coverage archive.

    The embedded ``covstub`` snapshot is the authoritative source because it
    travels with the coverage data and does not require any CI-side metadata.
    ``origin/main`` is used only when an archive has no embedded snapshot and
    may produce an approximate denominator.
    """
    cache = get_github_cache()
    get_worktree = getattr(cache, "get_worktree", None)

    with _embedded_coverage_source(tar_path) as embedded_tree:
        if embedded_tree:
            yield embedded_tree, None, "archive_covstub"
            return

    source_tree = (
        get_worktree("origin/main", purpose="coverage")
        if callable(get_worktree)
        else cache.cache_dir
    ) or cache.cache_dir
    yield source_tree, _safe_latest_commit(cache), "origin_main_fallback"


def _process_line_coverage(
    tar_path: Path, signature: str, test_type: str | None = "ut"
) -> dict[str, Any]:
    raw, _ = _aggregate_raw_coverage(tar_path, test_type=test_type)
    with _coverage_source_tree(tar_path) as (source_tree, source_commit, source_origin):
        return _process_line_coverage_with_source(
            raw, source_tree, source_commit, source_origin, signature, test_type
        )


def _process_line_coverage_with_source(
    raw: dict[str, dict[str, set]],
    source_tree: Path,
    source_commit: str | None,
    source_origin: str,
    signature: str,
    test_type: str | None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    module_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"statements": 0, "covered": 0, "branches": 0, "covered_branches": 0, "files": 0}
    )
    fallback_paths: list[str] = []
    for path, executed in sorted(raw["lines"].items()):
        statements, excluded, possible_arcs, analyzed = _source_analysis(path, source_tree)
        if not analyzed:
            # Keep non-Python/temporarily unavailable sources visible, but mark
            # the aggregate partial instead of pretending to know the denominator.
            statements = set(executed)
            fallback_paths.append(path)
        if not statements:
            continue
        effective_executed = executed - excluded
        covered = statements & effective_executed
        executed_arcs = raw["arcs"].get(path, set())
        branch_total = len(possible_arcs)
        branch_covered = len(possible_arcs & executed_arcs)
        module = module_of(path)
        totals = module_totals[module]
        totals["statements"] += len(statements)
        totals["covered"] += len(covered)
        totals["branches"] += branch_total
        totals["covered_branches"] += branch_covered
        totals["files"] += 1
        missing = sorted(statements - covered)
        files.append(
            {
                "path": path,
                "module": module,
                "statements": len(statements),
                "missing": len(missing),
                "covered": len(covered),
                "percent_covered": round(len(covered) / len(statements) * 100, 2),
                "has_branches": branch_total > 0,
            }
        )
        details[path] = {
            "executed_lines": sorted(effective_executed),
            "missing_lines": missing,
            "excluded_lines": sorted(excluded),
            "executed_branches": sorted(executed_arcs),
            "missing_branches": sorted(possible_arcs - executed_arcs),
            "summary": files[-1],
        }
    statement_count = sum(item["statements"] for item in files)
    covered_count = sum(item["covered"] for item in files)
    branch_count = sum(item["branches"] for item in module_totals.values())
    covered_branch_count = sum(item["covered_branches"] for item in module_totals.values())
    by_module = []
    for module, values in sorted(module_totals.items()):
        by_module.append(
            {
                "module": module,
                **values,
                "percent": round(values["covered"] / values["statements"] * 100, 2)
                if values["statements"]
                else 0.0,
            }
        )
    status = "partial" if fallback_paths else "ok"
    warning = None
    if fallback_paths:
        warning = f"{len(fallback_paths)} 个文件未能从本地源码计算分母，覆盖率仅供参考"
    return {
        "totals": {
            "num_statements": statement_count,
            "covered_lines": covered_count,
            "missing_lines": statement_count - covered_count,
            "percent_covered": round(covered_count / statement_count * 100, 2)
            if statement_count
            else 0.0,
            "percent_statements_covered": round(covered_count / statement_count * 100, 2)
            if statement_count
            else 0.0,
            "num_branches": branch_count,
            "covered_branches": covered_branch_count,
            "missing_branches": branch_count - covered_branch_count,
            "percent_branches_covered": round(covered_branch_count / branch_count * 100, 2)
            if branch_count
            else 0.0,
            "num_files": len(files),
        },
        "by_module": by_module,
        "files": files,
        "details": details,
        "analysis_version": LINE_ANALYSIS_VERSION,
        "tar_signature": signature,
        "source_commit": source_commit,
        "source_origin": source_origin,
        "covdata_commit": None,
        "covdata_when": None,
        "version_gap_commits": None,
        "coverage_tool_version": None,
        "installed_coverage_version": coverage.__version__,
        "test_type": test_type or "all",
        "status": status if files else "failed",
        "status_reason": "path_mapping" if fallback_paths else None,
        "warning": warning or ("没有可分析的覆盖率文件" if not files else None),
        "updated_at": datetime.now(UTC).isoformat(),
    }


async def _head_signature(client: httpx.AsyncClient) -> str:
    response = await client.head(settings.PR_COVERAGE_TAR_URL, timeout=30)
    response.raise_for_status()
    # OBS version-id is an object version.  ETag is only a fallback and must
    # not be treated as a cryptographic or business-version guarantee.
    return ";".join(
        f"{key}:{response.headers.get(key, '')}"
        for key in ("x-obs-version-id", "etag", "content-length", "last-modified")
    )


async def _download_tar(client: httpx.AsyncClient, destination: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(max(1, settings.PR_COVERAGE_DOWNLOAD_RETRIES)):
        try:
            timeout = httpx.Timeout(settings.PR_COVERAGE_DOWNLOAD_TIMEOUT_SECONDS, connect=30)
            async with client.stream("GET", settings.PR_COVERAGE_TAR_URL, timeout=timeout) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("coverage.tar download attempt %d failed: %s", attempt + 1, exc)
            if attempt + 1 < settings.PR_COVERAGE_DOWNLOAD_RETRIES:
                await asyncio.sleep(min(30 * (2**attempt), 300))
    raise RuntimeError(f"coverage.tar download failed: {last_error}")


async def _download_with_signature(
    *, signature: str | None = None, destination: Path | None = None
) -> tuple[Path, str]:
    if destination is not None:
        # 临时文件必须与目标同文件系统：os.replace 不能跨设备重命名
        # （生产容器 /tmp 是 overlayfs，/app/data 是独立卷，跨设备会 EXDEV）
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix="coverage_", suffix=".tar", dir=destination.parent)
    else:
        fd, filename = tempfile.mkstemp(prefix="coverage_", suffix=".tar")
    os.close(fd)
    temp = Path(filename)
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            signature = signature or await _head_signature(client)
            await _download_tar(client, temp)
        if destination:
            os.replace(temp, destination)
            temp = destination
        return temp, signature
    except BaseException:
        # The caller can only clean up a path it receives.  If HEAD or the
        # download fails before returning, remove the empty/partial artifact
        # here, including on task cancellation.
        temp.unlink(missing_ok=True)
        raise


async def sync_e2e(db: AsyncSession) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(E2ECoverageParser().parse)
        existing = await _load_config(db, E2E_KEY)
        if existing and existing.get("source_file_hash") == result["source_file_hash"]:
            return {"success": True, "skipped": True, "updated_at": existing.get("updated_at")}
        await _save_config(db, E2E_KEY, result, "E2E 特性覆盖率数据")
        await db.commit()
        return {"success": True, "updated_at": result["updated_at"]}
    except Exception as exc:
        logger.exception("E2E coverage sync failed")
        return {"success": False, "error": str(exc)}


async def sync_pr_breadth(
    db: AsyncSession, *, force_download: bool = False, archive_kind: str | None = None
) -> dict[str, Any]:
    tar_path: Path | None = None
    handed_off = False
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            signature = await _head_signature(client)
        existing = await _load_config(db, PR_BREADTH_KEY)
        if existing and existing.get("tar_signature") == signature and not force_download:
            return {"success": True, "skipped": True, "tar_signature": signature}
        destination = _coverage_archive_path(signature, archive_kind) if archive_kind else None
        if destination and destination.exists() and tarfile.is_tarfile(destination):
            tar_path = destination
        else:
            tar_path, signature = await _download_with_signature(
                signature=signature, destination=destination
            )
        result = await asyncio.to_thread(_process_tar_breadth, tar_path, signature)
        await _save_config(db, PR_BREADTH_KEY, result, "PR 流水线覆盖广度矩阵")
        await db.commit()
        handed_off = True
        return {"success": True, "tar_signature": signature, "tar_path": str(tar_path)}
    except Exception as exc:
        logger.exception("PR coverage breadth sync failed")
        return {"success": False, "error": str(exc)}
    finally:
        # The caller receives this path only after a successful parse. Failed
        # downloads/parses must not leave a large artifact in the temp dir.
        if tar_path and not handed_off:
            tar_path.unlink(missing_ok=True)


async def sync_pr_lines(
    db: AsyncSession,
    tar_path: str | None = None,
    signature: str | None = None,
    force_download: bool = False,
    archive_kind: str | None = None,
) -> dict[str, Any]:
    if not settings.PR_COVERAGE_LINE_ENABLED:
        return {"success": True, "skipped": True, "reason": "disabled"}
    own_tar = tar_path is None and archive_kind is None
    path = Path(tar_path) if tar_path else None
    try:
        if signature is None:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                signature = await _head_signature(client)
        assert signature is not None
        existing = await _load_config(db, PR_LINES_KEY)
        if (
            existing
            and existing.get("tar_signature") == signature
            and existing.get("analysis_version") == LINE_ANALYSIS_VERSION
            and existing.get("status") == "ok"
            and existing.get("files")
            and not force_download
        ):
            return {"success": True, "skipped": True, "tar_signature": signature}
        if path is None or not path.exists():
            destination = _coverage_archive_path(signature, archive_kind) if archive_kind else None
            if destination and destination.exists() and tarfile.is_tarfile(destination):
                path = destination
            else:
                path, signature = await _download_with_signature(
                    signature=signature, destination=destination
                )
        # Aggregate line coverage from ALL test types (ut, e2e, other) to match
        # what other internal reports show (80%+ vs 70%+ UT-only).
        # Passing test_type=None tells _aggregate_raw_coverage to include every job.
        result = await asyncio.to_thread(_process_line_coverage, path, signature, test_type=None)
        if result["status"] == "failed":
            # Keep the last usable snapshot in MySQL. The durable task and
            # sync status expose the current failure without blanking the UI.
            return {"success": False, "status": "failed", "error": result["warning"], "tar_signature": signature}
        await _save_config(db, PR_LINES_KEY, result, "PR/UT 流水线行覆盖率")
        await db.commit()
        return {"success": result["status"] != "failed", "status": result["status"], "tar_signature": signature}
    except Exception as exc:
        logger.exception("PR coverage line sync failed")
        return {"success": False, "status": "failed", "error": str(exc)}
    finally:
        if own_tar and path:
            path.unlink(missing_ok=True)


async def sync_all_coverage(
    db: AsyncSession, source: str = "all", strategy: str = "hourly"
) -> dict[str, Any]:
    lock = _get_lock()
    if lock.locked():
        raise RuntimeError("coverage sync in progress")
    async with lock:
        status: dict[str, Any] = {"last_check_at": datetime.now(UTC).isoformat()}
        tar_path: str | None = None
        signature: str | None = None
        force_download = strategy == "daily_baseline"
        archive_kind = "baseline" if force_download else "updates"
        try:
            if source in ("all", "e2e"):
                status["e2e"] = await sync_e2e(db)
            if source in ("all", "pr_breadth"):
                breadth = await sync_pr_breadth(
                    db, force_download=force_download, archive_kind=archive_kind
                )
                status["pr_breadth"] = {key: value for key, value in breadth.items() if key != "tar_path"}
                tar_path = breadth.get("tar_path")
                signature = breadth.get("tar_signature")
            if source in ("all", "pr_lines"):
                lines = await sync_pr_lines(
                    db, tar_path=tar_path, signature=signature,
                    force_download=force_download, archive_kind=archive_kind,
                )
                status["pr_lines"] = lines
            status["strategy"] = strategy
            await _save_config(db, SYNC_STATUS_KEY, status, "测试覆盖率同步状态")
            await db.commit()
            if force_download:
                _cleanup_coverage_archives()
            return status
        finally:
            # Daily baselines and changed hourly versions are evidence files,
            # not temporary parsing scratch space.  Keep them for retention
            # cleanup; only ephemeral manual paths are removed here.
            if tar_path and archive_kind is None:
                Path(tar_path).unlink(missing_ok=True)


async def get_sync_status(db: AsyncSession) -> dict[str, Any]:
    return await _load_config(db, SYNC_STATUS_KEY) or {"last_check_at": None}


async def get_e2e_coverage(db: AsyncSession) -> dict[str, Any]:
    return await _load_config(db, E2E_KEY) or {"summary": {}, "taxonomy": {}, "tests": [], "updated_at": None}


def _paginate(items: list[dict[str, Any]], page: int, per_page: int) -> tuple[list[dict[str, Any]], int]:
    total = len(items)
    start = (page - 1) * per_page
    return items[start : start + per_page], total


async def get_pr_breadth(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 50,
    module: str | None = None,
    sort: str | None = None,
    order: str = "desc",
    fmt: str | None = None,
) -> dict[str, Any]:
    data = await _load_config(db, PR_BREADTH_KEY)
    if not data:
        return {"summary": {}, "jobs": [], "file_matrix": [], "by_module": [], "updated_at": None}
    file_matrix = [
        item for item in data.get("file_matrix", []) if not module or item.get("module") == module
    ]
    if sort == "covered_by_jobs":
        file_matrix.sort(key=lambda item: item.get("covered_by_jobs", 0), reverse=order == "desc")
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["source_path", "module", "covered_by_jobs", "covered_by_hardware"])
        for item in file_matrix:
            writer.writerow([
                item.get("source_path", ""),
                item.get("module", ""),
                item.get("covered_by_jobs", 0),
                ";".join(item.get("covered_by_hardware", [])),
            ])
        return {"csv": output.getvalue()}
    paged, total = _paginate(file_matrix, page, per_page)
    return {
        "summary": data.get("summary", {}),
        "jobs": data.get("jobs", []),
        "file_matrix": paged,
        "file_matrix_total": total,
        "by_module": data.get("by_module", []),
        "tar_signature": data.get("tar_signature"),
        "updated_at": data.get("updated_at"),
    }


async def get_pr_lines(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 50,
    sort: str | None = None,
    order: str = "desc",
    fmt: str | None = None,
) -> dict[str, Any]:
    data = await _load_config(db, PR_LINES_KEY)
    if not data:
        return {"totals": {}, "by_module": [], "files": [], "updated_at": None, "status": "unknown"}
    files = list(data.get("files", []))
    if sort == "percent_covered":
        files.sort(key=lambda item: item.get("percent_covered", 0), reverse=order == "desc")
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["path", "module", "statements", "missing", "covered", "percent_covered"])
        for item in files:
            writer.writerow([
                item.get("path", ""), item.get("module", ""), item.get("statements", 0),
                item.get("missing", 0), item.get("covered", 0), item.get("percent_covered", 0),
            ])
        return {"csv": output.getvalue()}
    paged, total = _paginate(files, page, per_page)
    return {
        "totals": data.get("totals", {}),
        "by_module": data.get("by_module", []),
        "files": paged,
        "files_total": total,
        "source_commit": data.get("source_commit"),
        "covdata_commit": data.get("covdata_commit"),
        "covdata_when": data.get("covdata_when"),
        "coverage_tool_version": data.get("coverage_tool_version"),
        "installed_coverage_version": data.get("installed_coverage_version"),
        "analysis_version": data.get("analysis_version"),
        "source_origin": data.get("source_origin"),
        "status": data.get("status", "unknown"),
        "status_reason": data.get("status_reason"),
        "warning": data.get("warning"),
        "tar_signature": data.get("tar_signature"),
        "updated_at": data.get("updated_at"),
    }


def _safe_latest_commit(cache: Any) -> str | None:
    try:
        info = cache.get_latest_commit()
        return info.get("sha") if isinstance(info, dict) else info
    except Exception:
        return None


async def get_pr_source(db: AsyncSession, path: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path or not path.startswith(("vllm_ascend/", "csrc/", "tests/")):
        raise ValueError("invalid source path")
    data = await _load_config(db, PR_LINES_KEY)
    if not data:
        raise FileNotFoundError("PR line coverage not synced yet")
    cache = get_github_cache()
    get_worktree = getattr(cache, "get_worktree", None)
    source_tree = (
        get_worktree("origin/main", purpose="coverage")
        if callable(get_worktree)
        else cache.cache_dir
    ) or cache.cache_dir
    source_path = source_tree / path
    if not source_path.is_file():
        raise FileNotFoundError(f"source not found: {path}")
    detail = (data.get("details") or {}).get(path, {})
    commit = data.get("source_commit")
    return {
        "path": path,
        "commit": commit,
        "source": source_path.read_text(encoding="utf-8", errors="replace"),
        "executed_lines": detail.get("executed_lines", []),
        "missing_lines": detail.get("missing_lines", []),
        "excluded_lines": detail.get("excluded_lines", []),
        "executed_branches": detail.get("executed_branches", []),
        "missing_branches": detail.get("missing_branches", []),
        "summary": detail.get("summary", {}),
        "github_url": f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/blob/{commit}/{path}" if commit else None,
        "source_aligned": True,
    }
