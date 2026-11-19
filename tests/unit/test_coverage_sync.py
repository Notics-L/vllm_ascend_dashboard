import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from coverage import CoverageData

from test_board import coverage_sync


def _write_covdata(path: Path, source_path: str, lines: set[int]) -> None:
    data = CoverageData(basename=str(path), suffix=False)
    data.add_lines({source_path: lines})
    data.write()


def _make_tar(tmp_path: Path) -> Path:
    tar_path = tmp_path / "coverage.tar"
    members = [
        (
            "VLLM-ASCEND@task-a/tests__ut__sample--test_a/covdata/"
            "coverage.linux-aarch64-a3-1-runner",
            {1, 2},
        ),
        (
            "VLLM-ASCEND@task-b/tests__ut__sample--test_b/covdata/"
            "coverage.linux-aarch64-a2-1-runner",
            {2, 3},
        ),
        (
            "VLLM-ASCEND@task-c/tests__e2e__sample--test_c/covdata/"
            "coverage.linux-aarch64-a3-1-runner",
            {4},
        ),
    ]
    source_path = "/__w/vllm-ascend/vllm-ascend/vllm_ascend/sample.py"
    with tarfile.open(tar_path, "w") as archive:
        for index, (member_name, lines) in enumerate(members):
            data_path = tmp_path / f"coverage-{index}.data"
            _write_covdata(data_path, source_path, lines)
            archive.add(data_path, arcname=member_name)
    return tar_path


def test_read_covdata_uses_coverage_public_api(tmp_path: Path) -> None:
    data_path = tmp_path / ".coverage"
    source_path = "/__w/vllm-ascend/vllm-ascend/vllm_ascend/sample.py"
    _write_covdata(data_path, source_path, {1, 3})

    result = coverage_sync.read_covdata(data_path)

    assert result is not None
    assert result["files"] == ["vllm_ascend/sample.py"]
    assert result["lines"]["vllm_ascend/sample.py"] == [1, 3]


def test_decode_job_dir_recognizes_cpu_ut() -> None:
    """上游 UT 任务目录名是 cpu-ut（不是 tests__ut__ 前缀），必须归为 ut。"""
    assert coverage_sync.decode_job_dir("cpu-ut")["test_type"] == "ut"
    assert coverage_sync.decode_job_dir("cpu-ut/sub")["test_type"] == "ut"
    assert coverage_sync.decode_job_dir("tests__ut__sample")["test_type"] == "ut"
    assert coverage_sync.decode_job_dir("cpu-uts")["test_type"] == "other"


def test_breadth_classifies_cpu_ut_directory_as_ut(tmp_path: Path) -> None:
    tar_path = tmp_path / "coverage-cpu-ut.tar"
    source_path = "/__w/vllm-ascend/vllm-ascend/vllm_ascend/sample.py"
    with tarfile.open(tar_path, "w") as archive:
        data_path = tmp_path / "coverage-cpu.data"
        _write_covdata(data_path, source_path, {5, 6})
        archive.add(
            data_path,
            arcname="VLLM-ASCEND@task-cpu/cpu-ut/covdata/coverage.linux-amd64-cpu-8-runner",
        )

    result = coverage_sync._process_tar_breadth(tar_path, "test-signature")

    assert result["summary"]["total_jobs"] == 1
    assert result["summary"]["by_test_type"] == {"ut": 1}


def test_breadth_preserves_job_to_file_matrix(tmp_path: Path) -> None:
    result = coverage_sync._process_tar_breadth(_make_tar(tmp_path), "test-signature")

    assert result["summary"]["total_jobs"] == 3
    assert result["summary"]["by_test_type"] == {"ut": 2, "e2e": 1}
    assert result["summary"]["by_hardware"] == {"A3": 2, "A2": 1}
    assert result["file_matrix"] == [
        {
            "source_path": "vllm_ascend/sample.py",
            "module": "vllm_ascend",
            "covered_by_jobs": 3,
            "covered_by_hardware": ["A2", "A3"],
        }
    ]


def test_line_coverage_filters_to_ut_and_calculates_source_denominator(
    tmp_path: Path, monkeypatch
) -> None:
    source_dir = tmp_path / "vllm_ascend"
    source_dir.mkdir()
    (source_dir / "sample.py").write_text(
        "def sample(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        coverage_sync,
        "get_github_cache",
        lambda: SimpleNamespace(
            cache_dir=tmp_path,
            get_latest_commit=lambda: {"sha": "test-commit"},
        ),
    )

    result = coverage_sync._process_line_coverage(_make_tar(tmp_path), "test-signature")

    assert result["test_type"] == "ut"
    assert result["status"] == "ok"
    assert result["analysis_version"] == coverage_sync.LINE_ANALYSIS_VERSION
    assert result["source_commit"] == "test-commit"
    assert result["totals"]["num_statements"] > result["totals"]["covered_lines"]
    assert result["totals"]["missing_lines"] > 0
    assert result["files"][0]["path"] == "vllm_ascend/sample.py"
    assert result["details"]["vllm_ascend/sample.py"]["executed_lines"] == [1, 2, 3]


def test_source_analysis_excludes_complete_triton_jit_functions(tmp_path: Path) -> None:
    source_dir = tmp_path / "vllm_ascend"
    source_dir.mkdir()
    (source_dir / "kernels.py").write_text(
        "import triton\n"
        "\n"
        "@triton.jit\n"
        "def plain_kernel(value):\n"
        "    if value:\n"
        "        return value\n"
        "    return 0\n"
        "\n"
        "@triton.jit(do_not_specialize=['value'])\n"
        "def configured_kernel(value):\n"
        "    return value + 1\n"
        "\n"
        "@triton.jit\n"
        "def jit_helper(value):\n"
        "    return value + 2\n"
        "\n"
        "def host_function(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )

    statements, excluded, arcs, analyzed = coverage_sync._source_analysis(
        "vllm_ascend/kernels.py", tmp_path
    )

    assert analyzed is True
    assert set(range(3, 8)) <= excluded
    assert set(range(9, 12)) <= excluded
    assert set(range(13, 16)) <= excluded
    assert statements.isdisjoint(set(range(3, 16)))
    assert {17, 18, 19, 20} <= statements
    assert all(abs(start) not in excluded and abs(end) not in excluded for start, end in arcs)


def test_embedded_coverage_source_is_available_without_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    tar_path = tmp_path / "coverage-with-source.tar"
    source_path = tmp_path / "sample.py"
    source_path.write_text("def sample():\n    return 1\n", encoding="utf-8")
    with tarfile.open(tar_path, "w") as archive:
        archive.add(
            source_path,
            arcname="vllm-ascend/covstub/vllm_ascend/sample.py",
        )

    checkout_calls: list[tuple[tuple, dict]] = []

    def unexpected_checkout(*args, **kwargs):
        checkout_calls.append((args, kwargs))
        raise AssertionError("embedded coverage source should not require checkout")

    monkeypatch.setattr(
        coverage_sync,
        "get_github_cache",
        lambda: SimpleNamespace(
            cache_dir=tmp_path / "missing-cache",
        get_worktree=unexpected_checkout,
        ),
    )

    with coverage_sync._coverage_source_tree(tar_path) as (root, commit, origin):
        assert root.joinpath("vllm_ascend/sample.py").read_text(encoding="utf-8") == source_path.read_text(encoding="utf-8")
        assert commit is None
        assert origin == "archive_covstub"
    assert checkout_calls == []


@pytest.mark.asyncio
async def test_download_failure_removes_partial_temp_tar(tmp_path: Path, monkeypatch) -> None:
    temp_path = tmp_path / "coverage-download.tar"
    fd = os.open(temp_path, os.O_CREAT | os.O_RDWR)
    monkeypatch.setattr(
        coverage_sync.tempfile,
        "mkstemp",
        lambda **_: (fd, str(temp_path)),
    )

    async def fail_signature(_client):
        raise RuntimeError("signature request failed")

    monkeypatch.setattr(coverage_sync, "_head_signature", fail_signature)

    with pytest.raises(RuntimeError, match="signature request failed"):
        await coverage_sync._download_with_signature()

    assert not temp_path.exists()


@pytest.mark.asyncio
async def test_download_with_destination_keeps_temp_on_same_filesystem(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: 带 destination 时临时文件必须建在目标目录（同文件系统）。

    生产容器 /tmp 是 overlayfs、/app/data 是独立卷，跨设备 os.replace 会抛
    Invalid cross-device link (EXDEV)，导致 coverage 同步每小时必挂。
    """
    destination = tmp_path / "2026-09-08" / "updates" / "sig123" / "coverage.tar"
    captured_kwargs: dict = {}
    real_mkstemp = coverage_sync.tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(coverage_sync.tempfile, "mkstemp", spy_mkstemp)

    async def ok_head(_client):
        return "x-obs-version-id:version-1;etag:etag-1;content-length:3;last-modified:today"

    async def fake_download(_client, path: Path):
        path.write_bytes(b"tar")

    monkeypatch.setattr(coverage_sync, "_head_signature", ok_head)
    monkeypatch.setattr(coverage_sync, "_download_tar", fake_download)

    result_path, signature = await coverage_sync._download_with_signature(destination=destination)

    assert captured_kwargs.get("dir") == destination.parent
    assert result_path == destination
    assert destination.read_bytes() == b"tar"
    assert signature


@pytest.mark.asyncio
async def test_hourly_sync_skips_only_a_usable_matching_snapshot(monkeypatch) -> None:
    signature = "x-obs-version-id:version-1;etag:etag-1;content-length:10;last-modified:today"

    async def matching_head(_client):
        return signature

    async def usable_snapshot(_db, _key):
        return {
            "tar_signature": signature,
            "analysis_version": coverage_sync.LINE_ANALYSIS_VERSION,
            "status": "ok",
            "files": [{"path": "vllm_ascend/example.py"}],
        }

    async def download_must_not_run(**_kwargs):
        raise AssertionError("unchanged usable hourly snapshot must not download")

    monkeypatch.setattr(coverage_sync, "_head_signature", matching_head)
    monkeypatch.setattr(coverage_sync, "_load_config", usable_snapshot)
    monkeypatch.setattr(coverage_sync, "_download_with_signature", download_must_not_run)

    result = await coverage_sync.sync_pr_lines(SimpleNamespace())

    assert result["success"] is True
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_hourly_sync_retries_an_empty_failed_snapshot(tmp_path: Path, monkeypatch) -> None:
    signature = "x-obs-version-id:version-1;etag:etag-1;content-length:10;last-modified:today"
    tar_path = _make_tar(tmp_path)
    downloaded = False

    async def matching_head(_client):
        return signature

    async def failed_snapshot(_db, _key):
        return {
            "tar_signature": signature,
            "analysis_version": coverage_sync.LINE_ANALYSIS_VERSION,
            "status": "failed",
            "files": [],
        }

    async def download(**_kwargs):
        nonlocal downloaded
        downloaded = True
        return tar_path, signature

    monkeypatch.setattr(coverage_sync, "_head_signature", matching_head)
    monkeypatch.setattr(coverage_sync, "_load_config", failed_snapshot)
    monkeypatch.setattr(coverage_sync, "_download_with_signature", download)
    monkeypatch.setattr(
        coverage_sync,
        "_process_line_coverage",
        lambda *_args, **_kwargs: {"status": "failed", "warning": "no UT covdata"},
    )

    result = await coverage_sync.sync_pr_lines(SimpleNamespace())

    assert downloaded is True
    assert result == {
        "success": False,
        "status": "failed",
        "error": "no UT covdata",
        "tar_signature": signature,
    }
