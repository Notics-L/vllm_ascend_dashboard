"""
Agent 安全工具集

为 Smolagents ToolCallingAgent 提供工具函数。
所有工具通过 smolagents @tool 装饰器注册，LLM 通过 function calling 调用。

安全原则：
  - 路径限制在 data/ 目录内
  - 网络请求仅允许 GitHub API 域名
  - 没有 shell / exec / write / delete
  - 所有返回值有长度上限，防止 token 爆炸
"""
import asyncio
import contextvars
import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
from smolagents import tool

from agent.memory_manager import MemoryManager
from infrastructure.clients.github_http import GitHubAsyncClient
from infrastructure.core.config import settings

logger = logging.getLogger(__name__)

# ContextVar：每个 agent run 拥有独立的上下文，并发安全
_ctx_memory_manager: contextvars.ContextVar[MemoryManager | None] = (
    contextvars.ContextVar("memory_manager", default=None)
)
_ctx_github_token: contextvars.ContextVar[str] = (
    contextvars.ContextVar("github_token", default="")
)
_ctx_event_loop: contextvars.ContextVar[asyncio.AbstractEventLoop | None] = (
    contextvars.ContextVar("agent_event_loop", default=None)
)
_ctx_memory_filters: contextvars.ContextVar[dict | None] = (
    contextvars.ContextVar("memory_filters", default=None)
)


def set_tool_context(
    memory_manager: MemoryManager,
    github_token: str = "",
    memory_filters: dict | None = None,
) -> tuple[contextvars.Token, contextvars.Token, contextvars.Token, contextvars.Token]:
    """在 Agent 运行前注入运行时上下文（并发安全）"""
    return (
        _ctx_memory_manager.set(memory_manager),
        _ctx_github_token.set(github_token or settings.GITHUB_TOKEN),
        _ctx_event_loop.set(asyncio.get_running_loop()),
        _ctx_memory_filters.set(memory_filters),
    )


def reset_tool_context(tokens: tuple[contextvars.Token, contextvars.Token, contextvars.Token, contextvars.Token]) -> None:
    """Restore the previous context after an agent run."""
    memory_token, github_token, loop_token, filters_token = tokens
    _ctx_memory_manager.reset(memory_token)
    _ctx_github_token.reset(github_token)
    _ctx_event_loop.reset(loop_token)
    _ctx_memory_filters.reset(filters_token)


def _get_memory_manager() -> MemoryManager | None:
    return _ctx_memory_manager.get(None)


def _get_github_token() -> str:
    return _ctx_github_token.get("") or settings.GITHUB_TOKEN


def _safe_data_path(path: str) -> Path:
    """
    安全地将用户提供的路径解析到 data/ 目录下。

    拒绝包含路径穿越的输入。
    """
    if not isinstance(path, str) or "\x00" in path:
        raise ValueError("invalid path")
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError(f"不允许的路径: {path}")
    data_dir = Path(settings.DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    data_dir = data_dir.resolve()
    resolved = (data_dir / candidate).resolve()
    try:
        resolved.relative_to(data_dir)
    except ValueError:
        raise ValueError(f"路径穿越被阻止: {path}") from None
    return resolved


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def read_log_file(path: str) -> str:
    """读取从 GitHub Actions 下载到 data/ 的日志、artifact 或其他数据文件。

    Args:
        path: 文件相对于 data/ 目录的路径，例如 "claude_logs/2026-07-01/analysis.log"
    """
    try:
        full_path = _safe_data_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not full_path.exists():
        return f"Error: 文件不存在: {path}"
    if not full_path.is_file():
        return f"Error: 不是文件: {path}"

    try:
        if full_path.stat().st_size > 5 * 1024 * 1024:
            return "Error: file is too large to read (limit: 5 MiB)"
        content = full_path.read_text(encoding="utf-8", errors="replace")
        max_len = 20000
        if len(content) > max_len:
            content = content[:max_len] + f"\n\n... (截断，共 {len(content)} 字符)"
        return content
    except Exception as e:
        return f"Error: 读取文件失败: {e}"


@tool
def read_log_excerpt(path: str, start_line: int, end_line: int) -> str:
    """Read a bounded line range from a downloaded GitHub Actions log/artifact.

    Use this after grep_content returns line numbers. It lets the agent inspect
    the exact failure neighborhood without repeatedly loading a large full log.

    Args:
        path: File path relative to the data/ directory.
        start_line: First line to return, using one-based line numbers.
        end_line: Last line to return, using one-based line numbers.
    """
    try:
        full_path = _safe_data_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not full_path.exists():
        return f"Error: file does not exist: {path}"
    if not full_path.is_file():
        return f"Error: not a file: {path}"

    try:
        start = max(1, int(start_line))
        end = max(start, int(end_line))
        if end - start > 800:
            end = start + 800
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if start > len(lines):
            return f"Error: start_line {start} exceeds file length {len(lines)}"
        selected = lines[start - 1:min(end, len(lines))]
        header = f"# {path} lines {start}-{start + len(selected) - 1} / {len(lines)}"
        return "\n".join([header, *(
            f"{line_no}: {line[:1200]}"
            for line_no, line in enumerate(selected, start)
        )])
    except Exception as e:
        return f"Error: failed to read log excerpt: {e}"


@tool
def grep_content(pattern: str, path: str) -> str:
    """在指定文件中搜索匹配 pattern 的行。类似 grep 命令。

    Args:
        pattern: 搜索模式（支持正则表达式）
        path: 文件相对于 data/ 目录的路径
    """
    if not pattern or len(pattern) > 600:
        return "Error: search pattern must contain 1-600 characters; split broad searches into several focused grep_content calls"
    try:
        full_path = _safe_data_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not full_path.exists():
        return f"Error: 文件不存在: {path}"

    try:
        if full_path.stat().st_size > 5 * 1024 * 1024:
            return "Error: file is too large to search (limit: 5 MiB)"
        content = full_path.read_text(encoding="utf-8", errors="replace")
        # Agent prompts commonly use ``foo|bar`` to mean alternatives. Keep
        # each alternative literal (safe from regex backtracking) while still
        # honoring that useful convention.
        needles = [
            item.replace(r"\[", "[").replace(r"\]", "]").replace(r"\.", ".").lower()
            for item in pattern.split("|")
            if item
        ]
        lines = content.splitlines()
        matched = []
        for i, line in enumerate(lines, 1):
            searchable = line[:4000].lower()
            if any(needle in searchable for needle in needles):
                matched.append(f"{i}: {line[:200]}")

        if not matched:
            return f"未找到匹配 '{pattern}' 的行"
        if len(matched) > 100:
            return "\n".join(matched[:100]) + f"\n\n... (共 {len(matched)} 行匹配，仅显示前 100 行)"
        return "\n".join(matched)
    except Exception as e:
        return f"Error: 搜索失败: {e}"


@tool
def search_memory(query: str, memory_type: str = "failure_analysis") -> str:
    """搜索历史分析记忆。

    Args:
        query: 搜索关键词或描述
        memory_type: 记忆类型，可选 failure_analysis / daily_summary / commit_analysis
    """
    mm = _get_memory_manager()
    if mm is None:
        return "Error: 记忆管理器未初始化"

    try:
        main_loop = _ctx_event_loop.get(None)
        if main_loop is None or not main_loop.is_running():
            return "Error: memory event loop is unavailable"
        future = asyncio.run_coroutine_threadsafe(
            mm.recall(
                query=query,
                memory_type=memory_type,
                filters=_ctx_memory_filters.get(None),
                limit=5,
            ),
            main_loop,
        )
        results = future.result(timeout=30)
    except Exception as e:
        return f"Error: 搜索记忆失败: {e}"

    if not results:
        return f"未找到与 '{query}' 相关的历史记录"

    formatted = MemoryManager.format_memories_for_prompt(results)
    return formatted


@tool
def fetch_github_api(url: str) -> str:
    """调用 GitHub REST API。

    Args:
        url: GitHub API URL，例如 https://api.github.com/repos/vllm-project/vllm-ascend/issues/42
    """
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return "Error: invalid GitHub API port"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        return f"Error: 仅允许 api.github.com 域名，不支持: {parsed.netloc}"

    token = _get_github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        import asyncio

        async def _fetch():
            async with GitHubAsyncClient(audit_source="agent", credential_source="global", timeout=30) as client:
                resp = await client.get(url, headers=headers)
                return resp

        response = asyncio.run(_fetch())
        if response.status_code == 200:
            data = response.json()
            # 截断过大的响应
            text = json.dumps(data, ensure_ascii=False, indent=2)
            if len(text) > 10000:
                text = text[:10000] + "\n\n... (截断)"
            return text
        return f"GitHub API 返回 {response.status_code}: {response.text[:500]}"
    except Exception as e:
        return f"Error: GitHub API 调用失败: {e}"


@tool
def list_files(directory: str = "") -> str:
    """列出 data/ 目录下的文件和子目录。

    Args:
        directory: data/ 下的子目录路径，留空列出根目录
    """
    try:
        target = _safe_data_path(directory or ".")
    except ValueError as e:
        return f"Error: {e}"

    if not target.exists():
        return f"Error: 目录不存在: {directory}"
    if not target.is_dir():
        return f"Error: 不是目录: {directory}"

    try:
        items = []
        for item in sorted(target.iterdir()):
            suffix = "/" if item.is_dir() else ""
            items.append(f"  {item.name}{suffix}")
        if not items:
            return f"目录为空: {directory or 'data/'}"
        if len(items) > 200:
            return f"data/{directory}/\n" + "\n".join(items[:200]) + f"\n\n... (共 {len(items)} 项，仅显示前 200 项)"
        return f"data/{directory}/\n" + "\n".join(items)
    except Exception as e:
        return f"Error: 列出文件失败: {e}"


def _agent_repo_path(repository: str = "vllm_ascend") -> Path:
    """Resolve one of the two read-only repositories available to the agent."""
    if repository not in {"vllm_ascend", "vllm"}:
        raise ValueError("repository must be 'vllm_ascend' or 'vllm'")
    if repository == "vllm_ascend":
        configured = os.environ.get("AGENT_REPO_PATH", "").strip()
        if configured:
            return Path(configured).resolve()
        from infrastructure.clients.github_cache import get_github_cache

        return get_github_cache().cache_dir.resolve()
    else:
        configured = os.environ.get("AGENT_VLLM_REPO_PATH", "").strip()
        if configured:
            return Path(configured).resolve()
        from infrastructure.clients.github_cache import get_vllm_cache

        return get_vllm_cache().cache_dir.resolve()


def _valid_git_ref(value: str) -> bool:
    return bool(value and len(value) <= 200 and re.fullmatch(r"[A-Za-z0-9._/@{}^~:+-]+", value))


def _valid_repo_relative_path(value: str, *, allow_empty: bool = False) -> bool:
    if not value:
        return allow_empty
    path = Path(value.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts and "\x00" not in value


def _run_git(args: list[str], max_chars: int = 30000, repository: str = "vllm_ascend") -> str:
    """Run a read-only git command without shell syntax, cross-platform."""
    try:
        repo = _agent_repo_path(repository)
    except ValueError as exc:
        return f"Error: {exc}"
    if not repo.is_dir():
        return f"Error: Agent repository does not exist: {repo}"
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "PAGER": "cat"},
        )
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        output = output.strip() or "(无输出)"
        if len(output) > max_chars:
            output = output[:max_chars] + f"\n\n... (truncated at {max_chars} characters)"
        return output
    except subprocess.TimeoutExpired:
        return "Error: git command timed out after 60 seconds"
    except Exception as exc:
        return f"Error: git command failed: {exc}"


@tool
def git_commit_range(base_ref: str, head_ref: str, max_commits: int = 100) -> str:
    """列出 last-good 到 bad/head 之间的全部提交，建立回归分析区间。

    Args:
        base_ref: 同名 Job 最近成功运行的 last-good SHA
        head_ref: 当前失败运行的 bad/head SHA
        max_commits: 最多返回的提交数，范围 1-300
    """
    if not _valid_git_ref(base_ref) or not _valid_git_ref(head_ref):
        return "Error: invalid git ref"
    max_commits = max(1, min(int(max_commits), 300))
    return _run_git([
        "log", "--reverse", f"--max-count={max_commits}",
        "--format=%H%x09%ad%x09%s", "--date=iso-strict",
        f"{base_ref}..{head_ref}",
    ])


@tool
def git_show_commit(commit_ref: str, path: str = "") -> str:
    """读取候选提交的真实 commit message 和完整 diff，可限定到一个文件。

    Args:
        commit_ref: 候选 commit SHA
        path: 可选的仓库相对文件路径
    """
    if not _valid_git_ref(commit_ref) or not _valid_repo_relative_path(path, allow_empty=True):
        return "Error: invalid ref or path"
    args = ["show", "--format=fuller", "--find-renames", commit_ref]
    if path:
        args.extend(["--", path.replace("\\", "/")])
    return _run_git(args, max_chars=40000)


@tool
def git_read_file(commit_ref: str, path: str, start_line: int = 1, end_line: int = 400) -> str:
    """随时读取任意指定 commit/ref 的完整源码片段，不 checkout、不修改工作树。

    Args:
        commit_ref: 要读取的 commit SHA（通常为 last-good 或 bad/head）
        path: 仓库相对文件路径
        start_line: 起始行号，从 1 开始
        end_line: 结束行号，最多返回 1200 行
    """
    if not _valid_git_ref(commit_ref) or not _valid_repo_relative_path(path):
        return "Error: invalid ref or path"
    start_line = max(1, int(start_line))
    end_line = max(start_line, min(int(end_line), start_line + 1199))
    normalized_path = path.replace("\\", "/")
    content = _run_git(["show", f"{commit_ref}:{normalized_path}"], max_chars=200000)
    if content.startswith("Error:") or "[exit code:" in content:
        return content
    lines = content.splitlines()
    selected = lines[start_line - 1:end_line]
    return "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start_line)) or "(无输出)"


@tool
def git_search_symbol(commit_ref: str, query: str, path: str = "") -> str:
    """在指定 commit 的整个代码仓中搜索符号、配置项或调用点。

    Args:
        commit_ref: 要搜索的 commit SHA
        query: 字面搜索内容，例如 maybe_compute_actual_seq_lengths
        path: 可选的仓库相对目录或文件
    """
    if not _valid_git_ref(commit_ref) or not query or len(query) > 200:
        return "Error: invalid ref or query"
    if not _valid_repo_relative_path(path, allow_empty=True):
        return "Error: invalid path"
    args = ["grep", "-n", "-F", "--max-count=200", query, commit_ref, "--"]
    if path:
        args.append(path.replace("\\", "/"))
    return _run_git(args)


@tool
def git_compare_file(base_ref: str, head_ref: str, path: str) -> str:
    """对比 last-good 与 bad/head 下某个完整文件的上下文差异。

    Args:
        base_ref: last-good SHA
        head_ref: bad/head SHA
        path: 仓库相对文件路径
    """
    if (
        not _valid_git_ref(base_ref)
        or not _valid_git_ref(head_ref)
        or not _valid_repo_relative_path(path)
    ):
        return "Error: invalid ref or path"
    return _run_git([
        "diff", "--find-renames", "--unified=80",
        base_ref, head_ref, "--", path.replace("\\", "/"),
    ], max_chars=40000)


@tool
def git_ref_contains(commit_ref: str, target_ref: str) -> str:
    """检查候选 commit 是否属于目标 ref/branch 的历史，避免跨分支误判。

    Args:
        commit_ref: 候选提交 SHA 或 ref
        target_ref: 当前 Job 实际被测的 branch/ref/commit
    """
    if not _valid_git_ref(commit_ref) or not _valid_git_ref(target_ref):
        return "Error: invalid git ref"
    output = _run_git(["merge-base", "--is-ancestor", commit_ref, target_ref], max_chars=4000)
    if "[exit code: 0]" in output or output == "(无输出)":
        return f"YES: `{commit_ref}` is reachable from `{target_ref}`"
    if "[exit code: 1]" in output:
        return f"NO: `{commit_ref}` is not reachable from `{target_ref}`"
    return output


@tool
def vllm_git(operation: str, ref: str, path: str = "", other_ref: str = "", query: str = "") -> str:
    """Read upstream vLLM history and source without changing its worktree.

    Args:
        operation: One of show, read_file, search, diff, or commit_range
        ref: Primary commit SHA, tag, or branch
        path: Optional repository-relative file or directory path
        other_ref: Second ref for diff or commit_range
        query: Literal search text for search
    """
    if operation not in {"show", "read_file", "search", "diff", "commit_range"}:
        return "Error: unsupported operation"
    if not _valid_git_ref(ref) or not _valid_repo_relative_path(path, allow_empty=True):
        return "Error: invalid ref or path"
    normalized_path = path.replace("\\", "/")
    if operation == "show":
        args = ["show", "--format=fuller", "--find-renames", ref]
        if normalized_path:
            args.extend(["--", normalized_path])
        return _run_git(args, max_chars=40000, repository="vllm")
    if operation == "read_file":
        if not normalized_path:
            return "Error: path is required for read_file"
        return _run_git(["show", f"{ref}:{normalized_path}"], max_chars=200000, repository="vllm")
    if operation == "search":
        if not query or len(query) > 200:
            return "Error: query is required for search"
        args = ["grep", "-n", "-F", "--max-count=200", query, ref, "--"]
        if normalized_path:
            args.append(normalized_path)
        return _run_git(args, repository="vllm")
    if not _valid_git_ref(other_ref):
        return "Error: other_ref is required for diff or commit_range"
    if operation == "diff":
        args = ["diff", "--find-renames", "--unified=80", ref, other_ref]
        if normalized_path:
            args.extend(["--", normalized_path])
        return _run_git(args, max_chars=40000, repository="vllm")
    return _run_git([
        "log", "--reverse", "--max-count=300",
        "--format=%H%x09%ad%x09%s", "--date=iso-strict",
        f"{ref}..{other_ref}",
    ], repository="vllm")


@tool
def run_bash(command: str) -> str:
    """在分析宿主中执行只读 shell 命令；宿主不等同于 CI Job 的 Runner。

    Args:
        command: 要执行的 bash 命令，自动在仓库目录下执行
    """
    repo_path = str(_agent_repo_path())
    timeout = 60  # 单次命令最长 60 秒

    # 安全检查：禁止危险命令
    dangerous = ["rm ", "sudo", "chmod", "chown", ">", "mkfs", "dd "]
    for d in dangerous:
        if d in command.lower():
            return f"Error: 禁止执行包含 '{d}' 的危险命令"

    # Parse a single executable invocation. Never pass model-controlled text
    # through a shell: blacklist checks do not stop substitutions or chaining.
    if re.search(r"[;&|<>`$()]", command):
        return "Error: shell operators and substitutions are not allowed"
    try:
        args = shlex.split(command, posix=True)
    except ValueError as exc:
        return f"Error: invalid command syntax: {exc}"
    if not args:
        return "Error: command is required"
    allowed = {
        "awk", "cat", "cloc", "diff", "find", "git", "grep", "head",
        "ls", "npm", "pnpm", "pytest", "rg", "sed", "sort", "tail", "wc",
    }
    executable = Path(args[0]).name.lower()
    if executable not in allowed:
        return f"Error: executable '{executable}' is not allowed"

    if not Path(repo_path).is_dir():
        return f"Error: Agent repository does not exist: {repo_path}"

    try:
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=repo_path,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "PAGER": "cat"},
        )
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        output = output.strip() or "(无输出)"
        if len(output) > 20000:
            output = output[:20000] + "\n\n... (output truncated at 20000 characters)"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: 命令超时 ({timeout}s): {command}"
    except Exception as e:
        return f"Error: 命令执行失败: {e}"


# ---------------------------------------------------------------------------
# 工具集
# ---------------------------------------------------------------------------

# CI 失败分析工具集
FAILURE_ANALYSIS_TOOLS = [
    read_log_file,
    read_log_excerpt,
    grep_content,
    fetch_github_api,
    list_files,
    git_commit_range,
    git_show_commit,
    git_read_file,
    git_search_symbol,
    git_compare_file,
    git_ref_contains,
    vllm_git,
]

# 每日总结 / Commit 分析工具集（数据通常在 prompt 内，工具较少）
SUMMARY_TOOLS = [
    search_memory,
    fetch_github_api,
]
