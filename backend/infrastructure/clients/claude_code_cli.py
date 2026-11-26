"""
Claude Code CLI 调用封装层

将原有的 LLMClient.generate() (直接 API SDK 调用) 替换为
subprocess 调用 claude CLI，以利用其多步推理、工具调用、
文件读取等能力。

ccswitch 多 provider 支持：
  - anthropic provider → 直接设置 ANTHROPIC_BASE_URL/API_KEY 环境变量
  - 其他 provider → 启动本地 FormatProxy，翻译 Anthropic ↔ OpenAI 格式，
    实现与 cc-switch (github.com/farion1231/cc-switch) 同等的代理翻译能力
"""
import asyncio
import json
import logging
import os
import shlex
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from infrastructure.core.github_credentials import environment_without_github_credentials
from tooling.parsers.format_proxy import FormatProxy

# Claude Code CLI 日志持久化目录
_CLI_LOG_DIR = Path(__file__).parent.parent.parent / "data" / "claude_logs"

logger = logging.getLogger(__name__)


def _cli_environment() -> dict[str, str]:
    return environment_without_github_credentials()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ClaudeCodeResult:
    """Claude Code CLI 调用结果"""
    content: str
    turns: int = 0
    duration_seconds: float = 0.0
    model_used: str = ""
    tool_calls: list[str] = field(default_factory=list)
    raw_json: dict | None = None
    exit_code: int = 0
    stderr: str = ""


class ClaudeCLINotAvailable(Exception):
    """Claude Code CLI 不可用异常（触发降级）"""
    pass


class ClaudeCLITimeout(Exception):
    """Claude Code CLI 执行超时"""
    pass


def _save_cli_log(
    provider: str, model: str, prompt: str, system_prompt: str,
    stdout: str, stderr: str, duration: float, exit_code: int, route: str,
    tool_calls: list[str] | None = None,
    raw_json: dict | None = None,
) -> str:
    """持久化保存 Claude Code CLI 调用日志"""
    try:
        now = datetime.now(UTC)
        date_dir = _CLI_LOG_DIR / now.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{now.strftime('%H%M%S')}_{provider}_{model}.log"
        filepath = date_dir / filename

        filepath.write_text(f"""\
{'='*60}
Claude Code CLI Call Log
{'='*60}
Time:      {now.isoformat()}
Provider:  {provider}
Model:     {model}
Route:     {route}
Duration:  {duration:.1f}s
Exit Code: {exit_code}

--- SYSTEM PROMPT ---
{system_prompt or '(none)'}

--- USER PROMPT ---
{prompt}

--- STDOUT ---
{stdout or '(empty)'}

--- STDERR ---
{stderr or '(empty)'}

--- TOOL CALLS ---
{chr(10).join(tool_calls) if tool_calls else '(none)'}

--- RAW JSON (full CLI interaction) ---
{json.dumps(raw_json, indent=2, ensure_ascii=False) if raw_json else '(not available)'}

{'='*60}
""", encoding="utf-8")
        return str(filepath)
    except Exception as e:
        logger.warning("Failed to save CLI log: %s", e)
        return ""


# ---------------------------------------------------------------------------
# ClaudeCodeCLI
# ---------------------------------------------------------------------------

class ClaudeCodeCLI:
    """Claude Code CLI 封装 — 替代 LLMClient.generate()"""

    # 可配置的默认值
    DEFAULT_MAX_TURNS: int = 10
    DEFAULT_TIMEOUT_SECONDS: int = 1800
    DEFAULT_MODEL: str = "claude-sonnet-4-20250514"

    def __init__(
        self,
        cli_path: str | None = None,
        max_turns: int | None = None,
        timeout_seconds: int | None = None,
    ):
        """
        Args:
            cli_path: claude CLI 路径（None 则自动探测）
            max_turns: 默认最大推理轮数
            timeout_seconds: 默认超时（秒）
        """
        self._cli_path = cli_path
        self._max_turns = max_turns or self.DEFAULT_MAX_TURNS
        self._timeout = timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        provider_config: dict,
        work_dir: str | None = None,
        max_turns: int | None = None,
        system_prompt: str = "",
        output_format: str = "text",
    ) -> ClaudeCodeResult:
        """
        执行一次 Claude Code CLI 调用。

        路由策略（按优先级）：
        1. CLAUDE_PROXY_URL 环境变量 → LiteLLM 网关（生产环境）
        2. provider == "anthropic" → 直连 Anthropic API
        3. 其他 → FormatProxy 本地代理（开发环境 fallback）

        Args:
            prompt: 用户提示词
            provider_config: {provider, api_key, api_base_url, default_model}
            work_dir: 工作目录
            max_turns: 最大推理轮数
            system_prompt: 系统提示词
            output_format: 输出格式
        """
        cli_path = self._resolve_cli_path()
        provider = provider_config.get("provider", "unknown").lower()
        model = provider_config.get("default_model", self.DEFAULT_MODEL)
        api_key = provider_config.get("api_key", "")
        api_base = provider_config.get("api_base_url", "")

        turns = max_turns or self._max_turns
        # 生成 debug 日志路径（轮次级交互记录）
        now = datetime.now(UTC)
        debug_dir = _CLI_LOG_DIR / now.strftime("%Y-%m-%d")
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_file = debug_dir / f"{now.strftime('%H%M%S')}_{provider}_{model}_debug.log"
        args = self._build_args(prompt, system_prompt, turns, output_format, str(debug_file))

        proxy: FormatProxy | None = None
        litellm_url = os.environ.get("LITELLM_PROXY_URL", "")
        proxy_only = os.environ.get("AGENT_PROXY_ONLY", "").lower() in ("1", "true", "yes")
        litellm_master_key = os.environ.get("LITELLM_MASTER_KEY", "")

        if proxy_only and (not litellm_url or not litellm_master_key):
            return ClaudeCodeResult(
                content="",
                turns=0,
                duration_seconds=0,
                model_used=model,
                exit_code=2,
                stderr="Proxy-only mode requires LITELLM_PROXY_URL and LITELLM_MASTER_KEY",
            )

        # ── 路由决策 ──
        if litellm_url:
            # FormatProxy (Anthropic→OpenAI) → LiteLLM → upstream
            proxy = FormatProxy(
                upstream_base_url=litellm_url,
                upstream_api_key=litellm_master_key,
                upstream_model=model,
            )
            await proxy.start()
            proxy.set_log_file(str(debug_file).replace("_debug.log", "_conversation.json"))

            env = self._build_env_direct(
                api_key=api_key,
                api_base=proxy.listen_url,
                model=model,
            )
            logger.info(
                "ClaudeCodeCLI (FormatProxy→LiteLLM): %s provider=%s model=%s",
                litellm_url, provider, model,
            )
        elif provider == "anthropic":
            env = self._build_env_direct(api_key, api_base, model)
            logger.info(
                "ClaudeCodeCLI (direct): provider=%s model=%s max_turns=%d",
                provider, model, turns,
            )
        else:
            # 开发环境：FormatProxy 本地代理（Anthropic ↔ OpenAI 翻译）
            proxy = FormatProxy(
                upstream_base_url=api_base or "https://api.openai.com/v1",
                upstream_api_key=api_key,
                upstream_model=model,
            )
            await proxy.start()
            proxy.set_log_file(str(debug_file).replace("_debug.log", "_conversation.json"))

            env = self._build_env_direct(
                api_key=api_key,
                api_base=proxy.listen_url,
                model=model,
            )
            logger.info(
                "ClaudeCodeCLI (via FormatProxy :%d): provider=%s → model=%s",
                proxy.port, provider, model,
            )

        logger.info(
            "CLI env: credentials_configured=%s, ANTHROPIC_BASE_URL=%s, model=%s, args=%s",
            bool(env.get("ANTHROPIC_API_KEY")),
            env.get("ANTHROPIC_BASE_URL"),
            model,
            " ".join(args),
        )

        # ── 执行 CLI ──
        # root 用户不允许 --dangerously-skip-permissions，通过 su 切到 appuser
        is_root = False
        try:
            is_root = os.geteuid() == 0  # Unix only; Windows raises AttributeError
        except AttributeError:
            pass  # Windows: not root, run directly

        if is_root:
            cmd_str = " ".join(
                [shlex.quote(cli_path)] + [shlex.quote(a) for a in args]
            )
            # su -m: preserve environment + 显式设置 HOME，避免 CLI 找 /.agents/skills
            cmd = ["su", "-m", "appuser", "-c", f"export HOME=/home/appuser; {cmd_str}"]
        else:
            cmd = [cli_path] + args

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=work_dir or os.getcwd(),
            )

            try:
                prompt_bytes = (getattr(self, "_stdin_prompt", prompt)).encode("utf-8")
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=prompt_bytes),
                    timeout=self._timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise ClaudeCLITimeout(
                    f"Claude Code CLI timed out after {self._timeout}s"
                )
            except asyncio.CancelledError:
                if proc.returncode is None:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                raise

            duration = time.monotonic() - start
            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

            result = None
            if proc.returncode != 0:
                # CLI 失败，但仍然保存日志并尝试解析已产出的内容
                route_name = "litellm" if litellm_url else ("direct" if provider == "anthropic" else "formatproxy")
                # 尝试从 stdout 提取内容（如 hit max_turns 但已写了报告）
                partial_result = None
                try:
                    partial_result = self._parse_output(
                        stdout, stderr, duration, output_format, model, proc.returncode,
                    )
                except Exception:
                    pass
                _save_cli_log(
                    provider=provider, model=model,
                    prompt=prompt, system_prompt=system_prompt,
                    stdout=stdout, stderr=stderr,
                    duration=duration, exit_code=proc.returncode,
                    route=route_name,
                    tool_calls=partial_result.tool_calls if partial_result and partial_result.tool_calls else None,
                    raw_json=partial_result.raw_json if partial_result else None,
                )
                envelope_error = self._error_from_payload(
                    partial_result.raw_json if partial_result else None
                )
                if envelope_error:
                    # ``--output-format json`` returns an error envelope when
                    # the agent exhausts its tool budget. It is not partial
                    # report content, even though the JSON itself is non-empty.
                    # Returning it used to make the report renderer complain
                    # about missing fields and hid the real failure reason.
                    raise ClaudeCLINotAvailable(envelope_error)
                # 如果能解析出非空内容，返回部分结果而不是抛异常
                if partial_result and partial_result.content and len(partial_result.content.strip()) > 100:
                    logger.warning(
                        "CLI exited with code %d but produced partial output (%d chars), using it",
                        proc.returncode, len(partial_result.content),
                    )
                    return partial_result
                raise ClaudeCLINotAvailable(
                    f"Claude CLI exited with code {proc.returncode}: {stderr[:200]}"
                )

            result = self._parse_output(
                stdout, stderr, duration, output_format, model, proc.returncode,
            )

            logger.info(
                "ClaudeCodeCLI finished: duration=%.1fs turns=%d content_len=%d",
                duration, result.turns, len(result.content),
            )

            # 持久化日志
            route_name = "litellm" if litellm_url else ("direct" if provider == "anthropic" else "formatproxy")
            _save_cli_log(
                provider=provider, model=model,
                prompt=prompt, system_prompt=system_prompt,
                stdout=stdout, stderr=stderr,
                duration=duration, exit_code=proc.returncode,
                route=route_name,
                tool_calls=result.tool_calls if result.tool_calls else None,
                raw_json=result.raw_json,
            )

            return result

        finally:
            # 清理临时文件
            for attr in ("_sys_prompt_file",):
                path = getattr(self, attr, None)
                if path:
                    try:
                        import os as _os
                        _os.unlink(path)
                    except Exception:
                        pass
            if proxy:
                await proxy.stop()

    async def check_available(self) -> bool:
        """检查 Claude Code CLI 是否可用"""
        try:
            cli_path = self._resolve_cli_path()
            proc = await asyncio.create_subprocess_exec(
                cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_cli_environment(),
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return proc.returncode == 0
        except Exception:
            return False

    async def ensure_initialized(self, provider_config: dict) -> bool:
        """
        确保 Claude Code CLI 已初始化（首次使用时可能被 login 阻塞）。

        用最小 prompt 做一次热启动，验证 API key 有效且 CLI 可以正常响应。
        """
        try:
            result = await self.run(
                prompt="reply 'ok'",
                provider_config=provider_config,
                max_turns=1,
                output_format="text",
            )
            return result.exit_code == 0 and len(result.content) > 0
        except Exception as e:
            logger.warning("Claude Code CLI initialization check failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_cli_path(self) -> str:
        """解析 claude CLI 可执行文件路径"""
        if self._cli_path:
            if os.path.isfile(self._cli_path):
                return self._cli_path
            raise ClaudeCLINotAvailable(
                f"Specified claude CLI not found: {self._cli_path}"
            )

        # auto-detect
        resolved = shutil.which("claude")
        if resolved and os.path.isfile(resolved):
            # 如果解析到 .CMD 文件，找对应的 VSCode 扩展 exe
            if resolved.lower().endswith(".cmd"):
                # 尝试 VSCode 扩展目录下的原生 exe
                import glob as _glob
                vscode_pattern = os.path.expanduser(
                    "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"
                )
                matches = sorted(_glob.glob(vscode_pattern), reverse=True)
                if matches:
                    exe_path = os.path.normpath(matches[0])
                    logger.info("Using VSCode claude.exe: %s", exe_path)
                    return exe_path
                # fallback: 通过 cmd.exe 启动
                return resolved  # CLI 会在 subprocess 中自然调用 cmd
            return resolved

        # 尝试常见的全局 npm 安装路径
        candidates = [
            "/usr/local/bin/claude",
            "/usr/bin/claude",
            os.path.expanduser("~/.npm-global/bin/claude"),
            os.path.expanduser("~/node_modules/.bin/claude"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c

        raise ClaudeCLINotAvailable(
            "Claude Code CLI ('claude') not found in PATH. "
            "Install with: npm install -g @anthropic-ai/claude-code"
        )

    def _build_env_direct(self, api_key: str, api_base: str, model: str) -> dict:
        """
        构建 Claude Code CLI 的环境变量。

        等效于 cc-switch 的设置：
        - ANTHROPIC_BASE_URL → CLI 的 API 请求目标
        - ANTHROPIC_API_KEY → 鉴权 token
        - ANTHROPIC_DEFAULT_*_MODEL → 模型别名映射

        当 api_base 指向 FormatProxy 时，即为代理模式（非 Anthropic provider）；
        当 api_base 指向官方 API 时，即为直连模式。
        """
        env = _cli_environment()

        # ── 清除宿主机泄漏的用户凭据，防止 CLI 使用个人账号 ──
        for key in (
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_MODEL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
        ):
            env.pop(key, None)

        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
            env["ANTHROPIC_AUTH_TOKEN"] = api_key  # CLI 要求此字段存在，否则报 Not logged in
        if api_base:
            env["ANTHROPIC_BASE_URL"] = api_base
        logger.info(
            "_build_env_direct: credentials_configured=%s, api_base=%s, model=%s",
            bool(api_key),
            api_base,
            model,
        )

        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model

        env["CLAUDE_CODE_TELEMETRY_DISABLED"] = "1"
        env["CLAUDE_CODE_NO_INTERACTIVE"] = "1"
        env["CLAUDE_CODE_HEADLESS"] = "1"

        # 不隔离 HOME/USERPROFILE —— 改用 --setting-sources 排除用户配置

        return env

    def _build_args(
        self,
        prompt: str,
        system_prompt: str,
        max_turns: int,
        output_format: str,
        debug_file: str = "",
    ) -> list[str]:
        """构建 claude CLI 命令行参数（prompt 较大时通过 stdin 传入）"""
        import tempfile
        from pathlib import Path

        self._stdin_prompt = prompt  # 通过 stdin 传入
        args = ["--print", "--max-turns", str(max_turns)]

        if debug_file:
            args.extend(["--debug", "--debug-file", debug_file])

        if output_format == "json":
            args.extend(["--output-format", "json"])

        if system_prompt:
            sp_dir = Path(tempfile.gettempdir()) / "claude_tmp"
            sp_dir.mkdir(parents=True, exist_ok=True)
            sys_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8", dir=str(sp_dir)
            )
            sys_file.write(system_prompt)
            sys_file.close()
            # 裸机 Linux root 部署：su appuser 需要能读取此文件
            try:
                os.chown(sys_file.name, uid=1000, gid=1000)  # appuser typical uid/gid
            except (AttributeError, PermissionError, OSError):
                pass  # Windows / non-root / Docker: no-op
            args.extend(["--system-prompt-file", sys_file.name])
            self._sys_prompt_file = sys_file.name

        # 跳过权限确认（仅非 root 用户可用）
        args.append("--dangerously-skip-permissions")

        return args

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        duration: float,
        output_format: str,
        model: str,
        exit_code: int,
    ) -> ClaudeCodeResult:
        """解析 CLI 输出"""
        content = stdout
        turns = 0
        tool_calls: list[str] = []
        raw_json = None

        if output_format == "json":
            try:
                raw_json = json.loads(stdout)
                # 尝试提取常见字段。不同 CLI/网关版本可能把最终文本放
                # 在 result、content 或 reasoning_content 中，也可能再包
                # 一层对象；统一展开后再交给报告解析器。
                if isinstance(raw_json, dict):
                    content = self._content_from_payload(raw_json) or stdout
                    turns = raw_json.get("turns", raw_json.get("num_turns", 0))
                    if "tool_calls" in raw_json:
                        tool_calls = raw_json["tool_calls"]
            except json.JSONDecodeError:
                logger.warning("Failed to parse CLI JSON output, using raw stdout")

        return ClaudeCodeResult(
            content=content,
            turns=turns,
            duration_seconds=duration,
            model_used=model,
            tool_calls=tool_calls,
            raw_json=raw_json,
            exit_code=exit_code,
            stderr=stderr,
        )

    @staticmethod
    def _error_from_payload(payload: object) -> str:
        """Return a safe, user-actionable CLI error from a JSON envelope.

        Claude Code uses a non-zero process exit and a JSON object such as
        ``{\"is_error\": true, \"subtype\": \"error_max_turns\"}`` when it
        reaches its tool-call limit. That object contains no final answer and
        must never be treated as a report merely because it has non-zero text
        length.
        """
        if not isinstance(payload, dict) or not payload.get("is_error"):
            return ""

        subtype = str(payload.get("subtype") or "")
        terminal_reason = str(payload.get("terminal_reason") or "")
        num_turns = payload.get("num_turns") or payload.get("turns")
        if subtype == "error_max_turns" or terminal_reason == "max_turns":
            turn_suffix = f"（已执行 {num_turns} 轮）" if num_turns else ""
            return (
                "Claude Code CLI 在生成最终报告前达到最大分析轮次"
                f"{turn_suffix}；请缩小分析范围或提高轮次上限后重试"
            )

        # Avoid exposing a provider error body, which can contain request
        # details. The typed envelope is enough to distinguish it from an
        # incomplete report in the UI and application log.
        label = subtype or terminal_reason or "unknown"
        return f"Claude Code CLI 返回执行错误（{label}），未生成最终分析报告"

    @classmethod
    def _content_from_payload(cls, payload: object) -> str:
        """Extract textual output from a CLI/LLM JSON envelope.

        GLM and other reasoning models may expose the answer as
        ``reasoning_content`` when ``content`` is empty.  The Claude CLI also
        has emitted nested result/content envelopes across versions.  This
        helper accepts all of those shapes without serializing dictionaries
        into text that can never satisfy the failure-report JSON contract.
        """
        # A reasoning gateway may return a short ``result`` summary while the
        # actual final answer (including the required report JSON) is nested
        # under ``content`` or ``reasoning_content``.  Returning the first
        # non-empty value silently discarded that answer and made the report
        # renderer see only ``problem_category``.  Collect all textual
        # candidates and prefer the one that contains the most report fields;
        # preserve the CLI's result-first order as the tie-breaker for normal
        # responses.
        candidates = cls._content_candidates(payload)
        if not candidates:
            return ""

        report_markers = (
            "problem_category",
            "root_cause_summary",
            "improvement_measures_summary",
            "问题分类",
            "根因摘要",
            "改进措施摘要",
        )

        def score(item: tuple[int, str]) -> tuple[int, int, int]:
            index, text = item
            normalized = text.strip()
            field_count = sum(marker in normalized for marker in report_markers)
            structured = int("{" in normalized and "}" in normalized)
            return field_count, structured, -index

        return max(enumerate(candidates), key=score)[1]

    @classmethod
    def _content_candidates(cls, payload: object) -> list[str]:
        """Collect textual answer candidates from nested CLI payloads."""
        if isinstance(payload, str):
            return [payload] if payload.strip() else []
        if isinstance(payload, list):
            candidates: list[str] = []
            for item in payload:
                candidates.extend(cls._content_candidates(item))
            return candidates
        if not isinstance(payload, dict):
            return []

        candidates = []
        # Keep the established priority, while still inspecting every answer
        # field before falling back to unrelated metadata.
        answer_keys = ("result", "content", "reasoning_content", "reasoning", "text")
        for key in answer_keys:
            candidates.extend(cls._content_candidates(payload.get(key)))
        if candidates:
            return candidates

        for value in payload.values():
            candidates.extend(cls._content_candidates(value))
        return candidates


async def run_with_fallback(
    prompt: str,
    provider_config: dict,
    system_prompt: str = "",
    work_dir: str | None = None,
    max_turns: int = 10,
    timeout_seconds: int = 1800,
    output_format: str = "text",
) -> ClaudeCodeResult:
    """
    通过 Claude Code CLI 执行分析（仅 CLI，无降级）。
    """
    cli = ClaudeCodeCLI(timeout_seconds=timeout_seconds)

    try:
        return await cli.run(
            prompt=prompt,
            provider_config=provider_config,
            work_dir=work_dir,
            max_turns=max_turns,
            system_prompt=system_prompt,
            output_format=output_format,
        )
    except (ClaudeCLINotAvailable, ClaudeCLITimeout):
        raise  # 直接抛出，由上层标记分析失败，不降级到 direct API
