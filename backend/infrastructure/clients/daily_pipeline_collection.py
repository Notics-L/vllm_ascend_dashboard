"""Read GitHub Actions runs and their existing test output for Daily collection."""

import base64
import io
import zipfile
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

import httpx

from infrastructure.clients.github_http import GitHubAsyncClient
from infrastructure.core.github_credentials import GitHubCredential, daily_github_credential

API_VERSION = "2026-03-10"
PAGE_SIZE = 100
MAX_PAGES = 100
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_FILES = 10000


class CollectionSourceError(Exception):
    """A safe error message that never contains credentials or response headers."""


class DailyCollectionClient:
    audit_source = "daily_collection"

    def __init__(self, repository: str, credential: GitHubCredential | str, *, audit_source: str | None = None):
        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise CollectionSourceError("Daily 目标仓库配置无效")
        self.repository = repository
        self.prefix = "/repos/" + "/".join(quote(part, safe="") for part in parts)
        self._credential = (
            daily_github_credential({}, candidate=credential) if isinstance(credential, str) else credential
        )
        self.client = GitHubAsyncClient(
            audit_source=audit_source or self.audit_source,
            credential=self._credential,
            timeout=httpx.Timeout(60, connect=15),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def request(self, path: str, params=None, missing_ok: bool = False):
        try:
            response = await self.client.get(path, params=params)
            if missing_ok and response.status_code in (404, 410):
                return None
            if not response.is_redirect:
                response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise CollectionSourceError(
                f"GitHub 读取失败（HTTP {exc.response.status_code}）"
            ) from None
        except httpx.HTTPError:
            raise CollectionSourceError("无法连接 GitHub，请稍后重试") from None

    async def json(self, path: str, params=None):
        response = await self.request(path, params)
        try:
            return response.json()
        except ValueError:
            raise CollectionSourceError("GitHub 返回了无效数据") from None

    async def pages(self, path: str, key: str | None = None, params=None, *, stop_after=None):
        result = []
        params = {"per_page": PAGE_SIZE, **(params or {})}
        for _ in range(MAX_PAGES):
            response = await self.request(path, params)
            try:
                payload = response.json()
                items = payload[key] if key else payload
            except (ValueError, KeyError, TypeError):
                raise CollectionSourceError("GitHub 分页数据格式无效") from None
            if not isinstance(items, list):
                raise CollectionSourceError("GitHub 分页数据格式无效")
            result.extend(items)
            following = response.links.get("next", {}).get("url")
            if not following or (stop_after is not None and any(stop_after(item) for item in items)):
                return result
            if urlparse(following).netloc != "api.github.com":
                raise CollectionSourceError("GitHub 返回了无效分页链接")
            path, params = following, None
        raise CollectionSourceError("GitHub 返回的数据过多，分页未完成")

    async def merged_pull_requests(self, branch: str, start: datetime, end: datetime):
        # The endpoint is ordered by update time. A PR cannot merge after its last update.
        start_utc, end_utc = start.replace(tzinfo=UTC), end.replace(tzinfo=UTC)
        try:
            pulls = await self.pages(
                f"{self.prefix}/pulls",
                params={"state": "closed", "base": branch, "sort": "updated", "direction": "desc"},
                stop_after=lambda item: datetime.fromisoformat(item["updated_at"]) <= start_utc,
            )
            merged = {}
            for item in pulls:
                if not item["merged_at"]:
                    continue
                point = datetime.fromisoformat(item["merged_at"])
                if start_utc < point <= end_utc:
                    merged[item["number"]] = {
                        "pr_number": item["number"],
                        "title": item["title"],
                        "merged_at": point.astimezone(UTC).replace(tzinfo=None),
                    }
            for number, pull in merged.items():
                pull.update(await self.pull_request_metadata(number))
            return list(merged.values())
        except (KeyError, TypeError, ValueError):
            raise CollectionSourceError("GitHub 返回了无效的 PR 合入信息") from None

    async def pull_request_metadata(self, number: int):
        detail = await self.json(f"{self.prefix}/pulls/{number}")
        try:
            url = detail["html_url"]
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "github.com"
                or any(character.isspace() or character in "<>()" for character in url)
            ):
                raise ValueError("Invalid GitHub PR URL")
            return {
                "author": (detail.get("user") or {}).get("login"),
                "merged_by": (detail.get("merged_by") or {}).get("login"),
                "url": url,
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            raise CollectionSourceError("GitHub 返回了无效的 PR 详情") from None

    async def pull_request(self, number: int, *, missing_ok: bool = False):
        response = await self.request(f"{self.prefix}/pulls/{number}", missing_ok=missing_ok)
        if response is None:
            return None
        try:
            data = response.json()
        except ValueError:
            raise CollectionSourceError("GitHub 返回了无效的 PR 响应") from None
        if not isinstance(data, dict) or type(data.get("merged")) is not bool:
            raise CollectionSourceError("GitHub 返回了无效的 PR 合入状态")
        return data

    async def run(self, run_id: int):
        return await self.json(f"{self.prefix}/actions/runs/{run_id}")

    async def jobs(self, run_id: int, attempt: int):
        return await self.pages(
            f"{self.prefix}/actions/runs/{run_id}/attempts/{attempt}/jobs",
            "jobs",
        )

    async def download(self, path: str):
        response = await self.request(path, missing_ok=True)
        if response is None:
            return None
        if response.is_redirect:
            location = response.headers.get("location", "")
            if urlparse(location).scheme != "https":
                raise CollectionSourceError("GitHub 返回了无效下载链接")
            try:
                # Signed storage URLs receive no GitHub token.
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as download:
                    async with download.stream("GET", location) as stream:
                        if stream.status_code in (404, 410):
                            return None
                        stream.raise_for_status()
                        data = bytearray()
                        async for chunk in stream.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_DOWNLOAD_BYTES:
                                raise CollectionSourceError("测试日志超过单次采集大小限制")
                        return bytes(data)
            except httpx.HTTPError:
                raise CollectionSourceError("无法下载 GitHub 测试日志") from None
        if len(response.content) > MAX_DOWNLOAD_BYTES:
            raise CollectionSourceError("测试日志超过单次采集大小限制")
        return response.content

    async def job_logs(self, job_id: int):
        data = await self.download(f"{self.prefix}/actions/jobs/{job_id}/logs")
        return data.decode("utf-8", errors="replace") if data is not None else None

    async def artifacts(self, run_id: int):
        return await self.pages(f"{self.prefix}/actions/runs/{run_id}/artifacts", "artifacts")

    async def artifact_logs(self, artifact_id: int):
        data = await self.download(f"{self.prefix}/actions/artifacts/{artifact_id}/zip")
        if data is None:
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > MAX_ARCHIVE_FILES
                    or sum(e.file_size for e in entries) > MAX_DOWNLOAD_BYTES
                ):
                    raise CollectionSourceError("测试产物超过单次采集大小限制")
                return [
                    archive.read(entry).decode("utf-8", errors="replace")
                    for entry in entries
                    if entry.filename.endswith(".log") and not entry.is_dir()
                ]
        except (zipfile.BadZipFile, RuntimeError):
            raise CollectionSourceError("GitHub 测试产物无法解析") from None

    async def content(self, path: str, ref: str):
        data = await self.json(f"{self.prefix}/contents/{quote(path, safe='/')}", {"ref": ref})
        try:
            return base64.b64decode(data["content"]).decode("utf-8")
        except (KeyError, ValueError, UnicodeDecodeError):
            raise CollectionSourceError("GitHub 配置文件无法读取") from None

    async def workflow_runs(self, workflow: str, branch: str, created: str):
        return await self.pages(
            f"{self.prefix}/actions/workflows/{quote(workflow, safe='')}/runs",
            "workflow_runs",
            {"branch": branch, "event": "workflow_dispatch", "created": f">={created}"},
        )

    async def concurrency_groups(self, run_id: int):
        return await self.pages(
            f"{self.prefix}/actions/runs/{run_id}/concurrency_groups", "concurrency_groups"
        )

    async def find_issue(self, run_id: int, since: str):
        marker = f"<!-- vllm-ascend-daily:{run_id} -->"
        issues = await self.pages(
            f"{self.prefix}/issues",
            params={"state": "all", "sort": "created", "direction": "desc", "since": since},
        )
        return next(
            (
                issue
                for issue in issues
                if not issue.get("pull_request") and marker in (issue.get("body") or "")
            ),
            None,
        )

    async def issue(self, number: int):
        data = await self.json(f"{self.prefix}/issues/{number}")
        if data.get("pull_request"):
            raise CollectionSourceError("关联记录不是 Issue")
        return data

    async def issue_comments(self, number: int):
        return await self.pages(f"{self.prefix}/issues/{number}/comments")

    async def issue_detail(self, number: int):
        issue = await self.issue(number)
        comments = await self.issue_comments(number)
        return {
            "number": issue["number"],
            "url": issue["html_url"],
            "title": issue["title"],
            "state": issue["state"],
            "body": issue.get("body") or "",
            "comments": [
                {
                    "id": str(comment["id"]),
                    "author": (comment.get("user") or {}).get("login", ""),
                    "createdAt": comment.get("created_at"),
                    "body": comment.get("body") or "",
                }
                for comment in comments
            ],
        }

    async def update_issue_body(self, number: int, body: str) -> None:
        try:
            response = await self.client.patch(f"{self.prefix}/issues/{number}", json={"body": body})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CollectionSourceError(f"GitHub Issue 正文更新失败（HTTP {exc.response.status_code}）") from None
        except httpx.HTTPError:
            raise CollectionSourceError("GitHub Issue 正文更新请求失败") from None

    async def close_issue(self, number: int):
        try:
            response = await self.client.patch(f"{self.prefix}/issues/{number}", json={"state": "closed"})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise CollectionSourceError("GitHub Issue 关闭失败") from None
