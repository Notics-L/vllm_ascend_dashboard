"""Verify collection across rerun attempts without calling GitHub or a live database."""

import json
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import httpx

from image_build import collection
from image_build import collection_source as source

RUN_ID = 45670572975
REPOSITORY = "vllm-project/vllm-ascend"
RUN_URL = f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}"
RUN_PATH = f"/repos/{REPOSITORY}/actions/runs/{RUN_ID}"
NOW = datetime(2026, 11, 26, 5)
START = "2026-11-26T03:00:00Z"
END = "2026-11-26T04:00:00Z"


def github_run(attempt=1, conclusion="failure"):
    return {
        "id": RUN_ID, "path": source.WORKFLOW_PATH, "run_attempt": attempt,
        "name": source.WORKFLOW_NAME, "head_sha": "a" * 40,
        "status": "completed", "conclusion": conclusion,
        "run_started_at": START, "updated_at": END,
    }


def job(job_id, title, conclusion, attempt=1):
    return {
        "id": job_id, "name": title, "status": "completed", "conclusion": conclusion,
        "run_attempt": attempt, "started_at": START, "completed_at": END,
    }


def publication_zip(run_id=RUN_ID):
    report = {
        "branch": "main", "run_id": run_id, "build_type": ["nightly"],
        "nightly": {"images": [{
            "hardware": "a5", "os": "openeuler", "tag": "nightly-main",
            "digest": "sha256:" + "a" * 64, "repositories": ["registry.example/ascend/image"],
            "commits": {"arm64": "a" * 40, "amd64": ""},
        }]},
    }
    stream = BytesIO()
    with ZipFile(stream, "w") as zipped:
        zipped.writestr("build-info.json", json.dumps(report))
    return stream.getvalue()


class ImageBuildSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_partial_rerun_preserves_successful_jobs_and_replaces_failed_jobs(self):
        group = "Build nightly-a5 (openeuler)"
        base = f"{group} / Build base image"
        nightly = f"{group} / Build nightly-a5 (openeuler)"
        jobs = [
            job(10, f"{base} / build (arm64)", "success"),
            job(11, f"{base} / build (amd64)", "failure"),
            job(12, f"{base} / merge-image", "skipped"),
            job(13, nightly, "skipped"),
            job(21, f"{base} / build (amd64)", "success", 2),
            job(22, f"{base} / merge-image", "success", 2),
            job(23, f"{nightly} / Merge multi-arch nightly-a5", "success", 2),
        ]
        previous, = source.build_matrix(jobs, RUN_URL, 1)
        current, = source.build_matrix(jobs, RUN_URL, 2)
        self.assertEqual(previous["base"]["jobUrl"], RUN_URL + "/job/11")
        self.assertEqual(previous["base"]["result"], "失败")
        self.assertEqual(current["base"]["succeeded"], 3)
        self.assertEqual(current["base"]["total"], 3)
        self.assertEqual(current["base"]["jobUrl"], RUN_URL + "/job/22")
        self.assertEqual(current["nightly"]["total"], 1)

    async def test_publication_uses_only_the_bundle_for_the_current_attempt(self):
        def respond(request):
            if request.url.path == RUN_PATH + "/artifacts":
                self.assertEqual(request.url.params["name"], f"build-info-run-{RUN_ID}-2")
                return httpx.Response(200, json={"artifacts": [
                    {"id": 1, "name": f"build-info-run-{RUN_ID}-1"},
                    {"id": 2, "name": f"build-info-run-{RUN_ID}-2"},
                ]})
            self.assertEqual(request.url.path, f"/repos/{REPOSITORY}/actions/artifacts/2/zip")
            return httpx.Response(200, content=publication_zip())

        async with httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(respond)) as client:
            publication = await source.read_publication(client, REPOSITORY, RUN_ID, 2, RUN_URL)
        self.assertEqual(publication["reportUrl"], RUN_URL + "/artifacts/2")
        self.assertEqual(publication["images"][0]["commits"], {"arm64": "a" * 40, "amd64": None})
        with self.assertRaisesRegex(ValueError, "当前 Run"):
            source.parse_publication(publication_zip(RUN_ID + 1), RUN_ID, RUN_URL)

    async def test_delayed_github_acknowledgement_does_not_overwrite_pending_attempt(self):
        row = SimpleNamespace(
            run_id=RUN_ID, run_attempt=1, pending_attempt=2,
            rerun_requested_at=NOW, rerun_acknowledged=False,
        )

        def respond(request):
            self.assertEqual(request.url.path, RUN_PATH)
            return httpx.Response(200, json=github_run())

        def client_factory(_):
            return httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(respond))

        with patch.object(source, "github_client", client_factory):
            snapshot = await collection._read_snapshot(row, "test-token", REPOSITORY, NOW + timedelta(seconds=15))
        self.assertEqual(set(snapshot), {"next_collect_at"})
        row.rerun_acknowledged = True
        with patch.object(source, "github_client", client_factory):
            snapshot = await collection._read_snapshot(row, "test-token", REPOSITORY, NOW + timedelta(minutes=10))
        self.assertEqual(set(snapshot), {"next_collect_at"})


class FailedJobRerunTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_post_is_only_the_failed_jobs_endpoint_and_ambiguous_result_is_tracked(self):
        row = SimpleNamespace(
            run_id=RUN_ID, revision=4, run_attempt=1, pending_attempt=None,
            github_status="completed", conclusion="failure", ended_at=NOW,
        )
        trigger = SimpleNamespace(repository=REPOSITORY)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: (row, trigger))),
            rollback=AsyncMock(),
        )
        requests = []

        def respond(request):
            requests.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(200, json=github_run())
            raise httpx.ReadTimeout("request may have reached GitHub", request=request)

        def client_factory(_):
            return httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(respond))

        with (
            patch.object(source, "github_client", client_factory),
            patch.object(collection, "get_github_token", AsyncMock(return_value="test-token")),
            patch.object(collection, "_update_snapshot", AsyncMock(return_value=True)) as claim,
        ):
            self.assertEqual(await collection.rerun_failed_jobs(db, RUN_ID, "test"), "unknown")
            claim.assert_awaited_once()
            self.assertEqual(claim.await_args.args[3]["pending_attempt"], 2)

        self.assertEqual(requests, [("GET", RUN_PATH), ("POST", RUN_PATH + "/rerun-failed-jobs")])
        row.pending_attempt = 2
        with self.assertRaises(collection.RerunConflict):
            await collection.rerun_failed_jobs(db, RUN_ID, "test")

    async def test_successful_or_running_runs_cannot_be_rerun(self):
        for status, conclusion in (("completed", "success"), ("in_progress", "failure")):
            row = SimpleNamespace(
                github_status=status, conclusion=conclusion,
                ended_at=NOW if status == "completed" else None, run_attempt=1, pending_attempt=None,
            )
            db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(one_or_none=lambda row=row: (row, None))))
            with self.subTest(status=status), self.assertRaises(collection.RerunConflict):
                await collection.rerun_failed_jobs(db, RUN_ID, "test")
