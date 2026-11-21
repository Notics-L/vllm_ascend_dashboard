from infrastructure.storage.artifact_lifecycle import remove_consumed_artifacts


def test_concurrent_analysis_keeps_artifacts_until_last_reader_exits(tmp_path, monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace

    from infrastructure.storage import artifact_lifecycle as lifecycle

    # Model flock's shared/exclusive conflict on Windows as well as Linux.
    readers = set()

    def flock(fd, mode):
        if mode == 1:
            readers.add(fd.fileno())
        elif mode == 8:
            readers.discard(fd.fileno())
        elif readers:
            raise BlockingIOError

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        flock=flock, LOCK_SH=1, LOCK_UN=8, LOCK_EX=2, LOCK_NB=4,
    ))
    monkeypatch.setattr(lifecycle.settings, "DATA_DIR", str(tmp_path))
    run = tmp_path / "ci-evidence" / "runs" / "123"
    (run / "extracted").mkdir(parents=True)
    (run / "extracted" / "log.txt").write_text("evidence")

    async def scenario():
        started = asyncio.Event()
        finish = asyncio.Event()

        @lifecycle.release_artifacts_after_analysis
        async def reader():
            lifecycle.track_artifact_run(run)
            started.set()
            await finish.wait()
            assert (run / "extracted" / "log.txt").exists()
            return SimpleNamespace(analysis_status="failed")

        @lifecycle.release_artifacts_after_analysis
        async def completed():
            lifecycle.track_artifact_run(run)
            return SimpleNamespace(analysis_status="completed")

        task = asyncio.create_task(reader())
        await started.wait()
        await completed()
        assert (run / "extracted").exists()
        finish.set()
        await task
        assert not (run / "extracted").exists()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(scenario())


def test_only_consumed_artifacts_removed_reports_and_unused_runs_preserved(tmp_path):
    for name in ("123", "456"):
        run = tmp_path / name
        for child in ("artifacts", "extracted"):
            (run / child).mkdir(parents=True)
            (run / child / "log.txt").write_text("evidence")
        (run / "manifest.json").write_text("{}")
    (tmp_path / "123" / ".consumed").touch()
    remove_consumed_artifacts(tmp_path)
    assert not (tmp_path / "123" / "artifacts").exists()
    assert not (tmp_path / "123" / "extracted").exists()
    assert (tmp_path / "123" / "manifest.json").exists()
    assert (tmp_path / "456" / "extracted" / "log.txt").exists()


def test_symlink_run_never_followed(tmp_path):
    # Symlinks require special privileges on Windows; coverage runs on Linux too.
    import os

    import pytest
    if os.name == "nt":
        pytest.skip("Windows symlink privileges unavailable")
    outside = tmp_path / "outside"
    (outside / "artifacts").mkdir(parents=True)
    (outside / ".consumed").touch()
    (tmp_path / "123").symlink_to(outside, target_is_directory=True)
    remove_consumed_artifacts(tmp_path)
    assert (outside / "artifacts").exists()
