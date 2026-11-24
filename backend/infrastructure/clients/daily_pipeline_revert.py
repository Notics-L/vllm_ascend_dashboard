"""Create Daily revert PRs from branches with DCO-signed commits."""

import asyncio
import base64
import os
import re
import subprocess
import tempfile

import httpx

from infrastructure.clients.daily_pipeline_collection import DailyCollectionClient


class RevertError(Exception):
    """Actionable, credential-free repository-write failure."""


class RetryableRevertError(RevertError):
    """Transport or branch races can be retried after checking for an existing PR."""


class DailyRevertClient(DailyCollectionClient):
    def __init__(self, repository: str, token: str):
        super().__init__(repository, token)
        self._token = token

    async def _json(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise RetryableRevertError("GitHub 连接失败，稍后重试并检查是否已创建回退 PR") from None
        if (
            response.status_code == 429
            or response.status_code >= 500
            or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
            )
        ):
            raise RetryableRevertError("GitHub 暂时不可用或触发限流，稍后重试")
        if response.status_code in (401, 403):
            raise RevertError("GitHub Token 没有目标仓库的回退 PR 创建权限")
        if not response.is_success:
            raise RevertError(f"GitHub 回退请求失败（HTTP {response.status_code}）")
        try:
            return response.json()
        except ValueError:
            raise RetryableRevertError("GitHub 返回无效数据，稍后确认回退结果") from None

    async def _existing(self, branch, base, marker):
        rows = await self._json(
            "GET",
            f"{self.prefix}/pulls",
            params={
                "state": "all",
                "head": f"{self.repository.split('/')[0]}:{branch}",
                "base": base,
                "per_page": 100,
            },
        )
        if not isinstance(rows, list):
            raise RetryableRevertError("GitHub 回退 PR 查询结果无效")
        for pr in rows:
            if marker in (pr.get("body") or ""):
                if pr["state"] == "closed":
                    if not pr.get("merged_at"):
                        raise RevertError("已有回退 PR 已关闭且未合入，请人工处理")
                    return self._result(pr)
                sha = (pr.get("head") or {}).get("sha") or ""
                if not re.fullmatch(r"[0-9a-f]{40}", sha):
                    raise RetryableRevertError("无法确认已有回退 PR 的提交")
                details = (await self._json("GET", f"{self.prefix}/commits/{sha}")).get(
                    "commit"
                ) or {}
                author = details.get("author") or {}
                name, email = author.get("name"), author.get("email")
                footer = (details.get("message") or "").rstrip().rsplit("\n\n", 1)[-1]
                if (
                    not name
                    or not email
                    or (
                        f"Signed-off-by: {name} <{email}>".casefold()
                        not in footer.casefold().splitlines()
                    )
                ):
                    raise RevertError("已有回退 PR 缺少有效 sign-off，请由提交者处理")
                return self._result(pr)
        return None

    @staticmethod
    def _result(pr):
        return {
            "number": pr["number"],
            "url": pr["html_url"],
            "state": pr["state"],
            "merged": bool(pr.get("merged_at")),
        }

    @staticmethod
    def _verify_signed_reverts(git, base, head, targets):
        commits = git("rev-list", "--reverse", "--topo-order", f"{base}..{head}").split()
        if len(commits) != len(targets):
            raise RevertError("回退分支的提交与本次回退范围不一致，请人工检查")
        for commit, target in zip(commits, targets, strict=True):
            message = git("show", "-s", "--format=%B", commit)
            if not re.search(rf"(?m)^This reverts commit {target}[.,]", message):
                raise RevertError("回退分支的提交与本次回退范围不一致，请人工检查")
            author = git("show", "-s", "--format=%an <%ae>", commit).strip()
            trailers = git("interpret-trailers", "--parse", stdin=message)
            if f"Signed-off-by: {author}".casefold() not in trailers.casefold().splitlines():
                raise RevertError("回退提交缺少有效 sign-off")

    @staticmethod
    def _revert_targets(git, url, original):
        merged = original["merge_commit_sha"]
        parents = git("show", "-s", "--format=%P", merged).split()
        if len(parents) > 1:
            return [merged], ["-m", "1"]
        if original.get("commits") == 1:
            return [merged], []

        # A squash and the tail of a rebase both have one parent. Verify the
        # original PR's complete diff before choosing how many commits to undo.
        count = original.get("commits")
        head = (original.get("head") or {}).get("sha") or ""
        base = (original.get("base") or {}).get("sha") or ""
        if (
            not isinstance(count, int)
            or count < 2
            or any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (head, base))
        ):
            raise RevertError("无法确认原 PR 对应的合入提交范围，请人工回退")
        git("fetch", "--no-tags", url, head)

        def patch_id(revision):
            diff = git("diff", "--binary", revision)
            return git("patch-id", "--verbatim", stdin=diff).partition(" ")[0]

        expected = patch_id(f"{base}...{head}")
        if expected and patch_id(f"{merged}^..{merged}") == expected:
            return [merged], []
        if expected:
            revision = f"{merged}~{count}..{merged}"
            if patch_id(revision) == expected:
                return git("rev-list", "--first-parent", revision).split(), []
        raise RevertError("无法确认原 PR 对应的合入提交范围，请人工回退")

    def _prepare_signed_branch(self, original, branch, name, email):
        auth = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}",
                "LC_ALL": "C",
            }
        )
        url = f"https://github.com/{self.repository}.git"
        ref = f"refs/heads/{branch}"
        base_ref = f"refs/heads/{original['base']['ref']}"
        with tempfile.TemporaryDirectory(prefix="daily-revert-") as repo:

            def git(*args, stdin=None):
                return subprocess.run(
                    [
                        "git",
                        "-c",
                        "credential.helper=",
                        "-c",
                        "http.followRedirects=false",
                        "-c",
                        f"user.name={name}",
                        "-c",
                        f"user.email={email}",
                        *args,
                    ],
                    cwd=repo,
                    env=env,
                    input=stdin,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    check=True,
                    timeout=120,
                ).stdout

            try:
                git("init", "--template=")
                git("fetch", "--no-tags", url, base_ref)
                base = git("rev-parse", "FETCH_HEAD").strip()
                git("merge-base", "--is-ancestor", original["merge_commit_sha"], base)
                targets, options = self._revert_targets(git, url, original)
                if git("ls-remote", "--refs", url, ref).strip():
                    git("fetch", "--no-tags", url, ref)
                    signed = git("rev-parse", "FETCH_HEAD").strip()
                    self._verify_signed_reverts(git, base, signed, targets)
                    return signed

                git("checkout", "--detach", base)
                try:
                    git(
                        "-c",
                        "revert.reference=false",
                        "revert",
                        "--signoff",
                        "--no-edit",
                        "--no-gpg-sign",
                        *options,
                        *targets,
                    )
                except subprocess.CalledProcessError:
                    raise RevertError(
                        "无法生成回退提交（Not revertable），请检查回退冲突及分支历史"
                    ) from None
                signed = git("rev-parse", "HEAD").strip()
                self._verify_signed_reverts(git, base, signed, targets)
                git("push", f"--force-with-lease={ref}:", url, f"{signed}:{ref}")
                return signed
            except subprocess.TimeoutExpired:
                raise RetryableRevertError("GitHub 回退分支准备超时，稍后重新检查") from None
            except subprocess.CalledProcessError as exc:
                if "stale info" in (exc.stderr or ""):
                    raise RetryableRevertError("回退分支已创建，稍后重新检查") from None
                raise RevertError(
                    "无法准备 GitHub 回退分支，请检查 Token 写权限及分支历史"
                ) from None
            except OSError:
                raise RevertError("后端无法运行 Git，不能创建带 sign-off 的回退提交") from None

    async def create_or_reuse(self, number, run_url):
        original = await self._json("GET", f"{self.prefix}/pulls/{number}")
        if not original.get("merged_at"):
            raise RevertError("定位的问题 PR 尚未合入，不能创建回退 PR")
        if original.get("changed_files") == 0:
            raise RevertError(
                f"定位的 PR #{number} 没有文件改动（空提交），无法创建回退 PR，请核对定位结果"
            )
        merged = original.get("merge_commit_sha") or ""
        if not re.fullmatch(r"[0-9a-f]{40}", merged):
            raise RevertError("GitHub 未返回有效的 PR 合入提交")
        marker = f"<!-- daily-pipeline-revert:{self.repository}#{number} -->"
        base = original["base"]["ref"]
        branch = f"revert-{number}-{original['head']['ref']}"
        existing = await self._existing(branch, base, marker)
        if existing:
            return existing

        actor = await self._json("GET", "/user")
        login, uid = actor.get("login"), actor.get("id")
        if not login or not isinstance(uid, int) or uid <= 0:
            raise RevertError("无法读取 GitHub Token 的提交者身份")
        name = actor.get("name") or login
        email = actor.get("email") or f"{uid}+{login}@users.noreply.github.com"
        if any(char in name + email for char in "\r\n<>") or not name.strip():
            raise RevertError("GitHub Token 的提交者姓名或邮箱不适用于签署")
        signed = await asyncio.to_thread(self._prepare_signed_branch, original, branch, name, email)
        try:
            pr = await self._json(
                "POST",
                f"{self.prefix}/pulls",
                json={
                    "head": branch,
                    "base": base,
                    "title": f"Revert #{number}: {original['title']}"[:255],
                    "body": f"{marker}\n\nReverts https://github.com/{self.repository}/pull/{number}\n\nDaily run: {run_url}",
                    "draft": False,
                },
            )
        except RetryableRevertError:
            raise
        except RevertError:
            existing = await self._existing(branch, base, marker)
            if existing:
                return existing
            raise
        if not pr.get("number") or not pr.get("html_url"):
            raise RetryableRevertError("GitHub 尚未返回回退 PR，稍后确认创建结果")
        if (pr.get("head") or {}).get("sha") != signed:
            raise RetryableRevertError("回退分支已更新，稍后重新检查签署状态")
        return self._result(pr)
