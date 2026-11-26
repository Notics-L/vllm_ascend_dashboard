"""
Agent Service — Smolagents ToolCallingAgent 封装层

替换 Claude Code CLI 的 subprocess 调用方式，使用 Smolagents 框架
的 ToolCallingAgent 实现 ReAct agent loop。

架构：
  AgentService.run()
    → MemoryManager.recall() 检索历史记忆
    → SkillRegistry 加载 system prompt
    → LiteLLMModel 对接 LLM
    → ToolCallingAgent 执行 ReAct
    → MemoryManager.memorize() 存储结果
"""
import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from agent.agent_runtime import BoundedToolCallingAgent, CompatibleLiteLLMModel
from agent.agent_tools import (
    FAILURE_ANALYSIS_TOOLS,
    SUMMARY_TOOLS,
    reset_tool_context,
    set_tool_context,
)
from agent.memory_manager import MemoryManager, MemoryRecord
from agent.skill_registry import SkillRegistry, get_skill_registry
from infrastructure.core.config import settings

logger = logging.getLogger(__name__)

# Stable aliases retained for tests and integrations that replace the runtime
# classes, while the production defaults use our compatibility layer.
LiteLLMModel = CompatibleLiteLLMModel
ToolCallingAgent = BoundedToolCallingAgent

# LiteLLM model prefix mapping（与 model_sync/litellm_sync.py 保持一致）
_PROVIDER_PREFIX = {
    "openai": "openai",
    "qwen": "openai",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "zhipu": "openai",
    "glm": "openai",
}


def _detect_prefix(provider: str, api_base: str) -> str:
    """推测 LiteLLM model prefix"""
    base_lower = (api_base or "").lower()
    if "deepseek" in base_lower:
        return "deepseek"
    if any(k in base_lower for k in ("bigmodel", "zhipu", "dashscope", "aliyuncs", "openai")):
        return "openai"
    if "anthropic" in base_lower:
        return "anthropic"
    return _PROVIDER_PREFIX.get(provider, "openai")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AgentTask:
    """一次 Agent 任务的配置"""
    prompt: str
    provider_config: dict  # {provider, api_key, api_base_url, default_model}
    skill_scope: str = ""  # SkillRegistry scope，如 "ci_failure_analysis"
    system_prompt: str = ""  # 如果提供，优先于 skill_scope
    max_steps: int = 20
    timeout_seconds: float = 1800
    memory_type: str = ""  # 记忆类型
    memory_filters: dict | None = None  # 记忆元数据过滤
    source_id: int | None = None  # 关联的业务记录 ID
    work_dir: str | None = None  # 工作目录（暂未使用，预留）
    tools_override: list | None = None
    step_callback: Callable[[dict], None] | None = None
    phase: str = "investigation"


@dataclass
class AgentResult:
    """Agent 任务结果"""
    content: str
    steps: int = 0
    duration_seconds: float = 0.0
    model_used: str = ""
    exit_code: int = 0
    error_message: str = ""
    memory_id: int | None = None
    trace: list[dict] = field(default_factory=list)


class AgentServiceError(Exception):
    """Agent 服务异常"""
    pass


# ---------------------------------------------------------------------------
# AgentService
# ---------------------------------------------------------------------------


class AgentService:
    """
    Agent 服务 — Smolagents 封装层。

    用法:
        agent_svc = AgentService(db)
        result = await agent_svc.run(AgentTask(
            prompt="分析这个 CI 失败...",
            provider_config={...},
            skill_scope="ci_failure_analysis",
            memory_type="failure_analysis",
        ))
    """

    DEFAULT_MAX_STEPS = 20
    DEFAULT_TIMEOUT_SECONDS = 1800

    def __init__(self, db):
        self.db = db
        self.memory = MemoryManager(db)
        self.skill_registry: SkillRegistry = get_skill_registry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        """
        执行一次 Agent 任务。

        Args:
            task: 任务配置

        Returns:
            AgentResult 包含输出内容、步数、耗时等
        """
        validation_error = self._validate_task(task)
        if validation_error:
            return AgentResult(content="", exit_code=2, error_message=validation_error)

        provider = task.provider_config.get("provider", "openai").lower()
        model = task.provider_config.get("default_model", "claude-sonnet-4-20250514")
        api_key = task.provider_config.get("api_key", "")
        api_base = task.provider_config.get("api_base_url", "")
        max_steps = self._resolve_max_steps(task)
        litellm_proxy = os.environ.get(
            "LITELLM_PROXY_URL", settings.LITELLM_PROXY_URL
        ).strip()
        proxy_only = os.environ.get(
            "AGENT_PROXY_ONLY", str(settings.AGENT_PROXY_ONLY)
        ).lower() in ("1", "true", "yes")

        if proxy_only and not litellm_proxy:
            return AgentResult(
                content="",
                model_used=model,
                exit_code=2,
                error_message="Agent proxy-only mode requires LITELLM_PROXY_URL",
            )
        if proxy_only:
            proxy_url = urlparse(litellm_proxy)
            allowed_proxy_hosts = {
                host.strip().lower()
                for host in os.environ.get(
                    "AGENT_PROXY_ALLOWED_HOSTS", settings.AGENT_PROXY_ALLOWED_HOSTS
                ).split(",")
                if host.strip()
            }
            if (
                proxy_url.scheme not in {"http", "https"}
                or not proxy_url.hostname
                or proxy_url.hostname.lower() not in allowed_proxy_hosts
            ):
                return AgentResult(
                    content="",
                    model_used=model,
                    exit_code=2,
                    error_message="Agent proxy-only mode requires a container-network proxy URL",
                )
            proxy_key = os.environ.get("LITELLM_MASTER_KEY", settings.LITELLM_MASTER_KEY)
            if not proxy_key:
                return AgentResult(
                    content="",
                    model_used=model,
                    exit_code=2,
                    error_message="Agent proxy-only mode requires LITELLM_MASTER_KEY",
                )

        start = time.monotonic()

        # 1. 检索历史记忆。失败分析必须从当前 CI/Git 证据重新推导；
        # 旧 AI 报告会放大错误结论，因此不注入也不主动召回。
        memories = []
        if task.memory_type and task.memory_type != "failure_analysis":
            try:
                memories = await self.memory.recall(
                    query=task.prompt,
                    memory_type=task.memory_type,
                    filters=task.memory_filters,
                    limit=5,
                )
                if memories:
                    logger.info(
                        "AgentService: recalled %d memories for type=%s",
                        len(memories), task.memory_type,
                    )
            except Exception as e:
                logger.warning("AgentService: memory recall failed: %s", e)

        # 2. 构建 system prompt
        system_prompt = self._build_system_prompt(
            skill_scope=task.skill_scope,
            fallback_prompt=task.system_prompt,
            memories=memories,
            memory_type=task.memory_type,
        )

        # 3. 选择工具集
        tools = list(task.tools_override) if task.tools_override is not None else self._select_tools(task)

        # 4. 注入运行时上下文（DB session、token 等）
        context_tokens = set_tool_context(
            memory_manager=self.memory,
            memory_filters=task.memory_filters,
        )

        # 5. 创建模型和 Agent
        # A LiteLLM proxy exposes an OpenAI-compatible endpoint regardless of
        # the upstream provider registered behind the model alias.
        prefix = "openai" if litellm_proxy else _detect_prefix(provider, api_base)
        model_id = f"{prefix}/{model}"

        lite_kwargs = {
            "model_id": model_id,
            "api_key": api_key,
        }
        if api_base:
            lite_kwargs["api_base"] = api_base

        logger.info(
            "AgentService: creating LiteLLMModel model_id=%s api_base=%s "
            "credentials_configured=%s",
            model_id,
            api_base,
            bool(api_key),
        )

        # 如果设置了 LITELLM_PROXY_URL，通过它路由
        if litellm_proxy:
            lite_kwargs["api_base"] = litellm_proxy
            proxy_key = os.environ.get("LITELLM_MASTER_KEY", settings.LITELLM_MASTER_KEY)
            lite_kwargs["api_key"] = proxy_key if proxy_only else (proxy_key or api_key)
            logger.info("AgentService: routing via LiteLLM proxy: %s", litellm_proxy)

        # Keep one stalled model call from consuming the complete task budget.
        # The overall timeout remains authoritative around the full agent run.
        # Verification audits operate on a dense evidence ledger; allow a
        # longer single action call than investigation, while still bounding it
        # so interrupt checks and phase fallback can run.
        if task.phase == "verification":
            action_timeout = 240
            final_timeout = 300
        elif task.phase == "reporting":
            action_timeout = 120
            final_timeout = 300
        else:
            action_timeout = 120
            final_timeout = 240
        # Let the agent retry at the step level. Hidden transport retries can
        # otherwise make a single final-answer call overrun its phase budget.
        lite_kwargs.setdefault("max_retries", 0)

        try:
            agent_model = LiteLLMModel(
                **lite_kwargs,
                allowed_tools={getattr(tool, "name", "") for tool in tools},
                generation_retries=1,
                action_timeout=action_timeout,
                final_timeout=final_timeout,
            )
            planning_interval = None
            trace: list[dict] = []
            consecutive_parse_failures = 0

            def _capture_step(step, agent=None):
                nonlocal consecutive_parse_failures
                entry = self._serialize_action_step(step, task.phase)
                trace.append(entry)
                error_text = (entry.get("error") or "").lower()
                if "parsing tool call" in error_text:
                    consecutive_parse_failures += 1
                else:
                    consecutive_parse_failures = 0
                if consecutive_parse_failures >= 3:
                    logger.error(
                        "Agent stopped after %d consecutive tool-call parse failures",
                        consecutive_parse_failures,
                    )
                    interrupt = getattr(agent, "interrupt", None)
                    if callable(interrupt):
                        interrupt()
                if task.step_callback:
                    try:
                        task.step_callback(entry)
                    except Exception as callback_error:
                        logger.warning("Agent step callback failed: %s", callback_error)

            agent = ToolCallingAgent(
                tools=tools,
                model=agent_model,
                instructions=system_prompt,
                max_steps=max_steps,
                planning_interval=planning_interval,
                step_callbacks=[_capture_step],
            )
        except Exception as e:
            reset_tool_context(context_tokens)
            logger.error("AgentService: initialization failed: %s", e, exc_info=True)
            return AgentResult(
                content="", model_used=model, exit_code=2,
                error_message=f"Agent initialization failed: {e}",
            )

        logger.info(
            "AgentService: starting agent model=%s provider=%s max_steps=%d tools=%d",
            model_id, provider, max_steps, len(tools),
        )

        # 6. 在独立线程中执行（Smolagents 是同步的）
        # 使用 ThreadPoolExecutor 替代 asyncio.to_thread，
        # 超时后不访问 agent 对象（线程仍在运行，访问可能导致死锁）
        import concurrent.futures
        import contextvars
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        run_context = contextvars.copy_context()
        try:
            result_content = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    executor,
                    run_context.run,
                    agent.run,
                    task.prompt,
                ),
                timeout=task.timeout_seconds,
            )
        except TimeoutError:
            duration = time.monotonic() - start
            # 尝试通知 agent 停止（不保证生效）
            interrupt = getattr(agent, "interrupt", None)
            if callable(interrupt):
                try:
                    interrupt()
                except Exception:
                    pass
            logger.error("AgentService: timed out after %.1fs", task.timeout_seconds)
            return AgentResult(
                content="",
                steps=0,  # 不访问 agent，线程可能还在跑
                duration_seconds=duration,
                model_used=model,
                exit_code=124,
                error_message=f"Agent timed out after {task.timeout_seconds:g} seconds",
            )
        except Exception as e:
            duration = time.monotonic() - start
            logger.error("AgentService: agent.run failed: %s", e, exc_info=True)
            return AgentResult(
                content="",
                steps=0,
                duration_seconds=duration,
                model_used=model,
                exit_code=1,
                error_message=str(e),
            )
        finally:
            executor.shutdown(wait=False)  # 不等待线程完成
            reset_tool_context(context_tokens)

        duration = time.monotonic() - start

        # agent.run() 返回的是字符串
        if isinstance(result_content, str):
            content = result_content
        elif isinstance(result_content, dict):
            content = result_content.get("output", str(result_content))
        else:
            content = str(result_content)

        # 清理 think 标签残留
        content = self._clean_output(content)

        # A transport-level success with an empty model response is not a
        # successful agent run. Never persist it as reusable memory.
        if not content:
            return AgentResult(
                content="",
                steps=self._extract_step_count(agent),
                duration_seconds=duration,
                model_used=model,
                exit_code=1,
                error_message="Agent returned an empty response",
            )

        # 7. 存储记忆
        memory_id = None
        if task.memory_type and task.memory_type != "failure_analysis" and content:
            try:
                memory_id = await self.memory.memorize(MemoryRecord(
                    memory_type=task.memory_type,
                    source_id=task.source_id,
                    title=self._extract_title(content),
                    content=content,
                    metadata=task.memory_filters or {},
                ))
                logger.info("AgentService: memory stored id=%d", memory_id)
            except Exception as e:
                logger.warning("AgentService: memory store failed: %s", e)

        logger.info(
            "AgentService: finished duration=%.1fs content_len=%d memory_id=%s",
            duration, len(content), memory_id,
        )

        return AgentResult(
            content=content,
            steps=self._extract_step_count(agent),
            duration_seconds=duration,
            model_used=model,
            exit_code=0,
            memory_id=memory_id,
            trace=trace,
        )

    async def check_available(self) -> bool:
        """检查 Agent 服务是否可用"""
        # Importing this module already validates the required dependency.
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_task(task: AgentTask) -> str:
        if not isinstance(task.prompt, str) or not task.prompt.strip():
            return "prompt must not be empty"
        if len(task.prompt) > 500_000:
            return "prompt exceeds the 500000 character limit"
        if not isinstance(task.provider_config, dict):
            return "provider_config must be a mapping"
        provider = task.provider_config.get("provider", "openai")
        if not isinstance(provider, str) or not provider.strip():
            return "provider_config.provider must be a non-empty string"
        if not task.provider_config.get("default_model"):
            return "provider_config.default_model is required"
        api_base = task.provider_config.get("api_base_url", "")
        if api_base:
            if not isinstance(api_base, str):
                return "provider_config.api_base_url must be a string"
            parsed = urlparse(api_base)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return "provider_config.api_base_url must be an HTTP(S) URL"
        configured_proxy = os.environ.get("LITELLM_PROXY_URL", settings.LITELLM_PROXY_URL)
        if not task.provider_config.get("api_key") and not configured_proxy:
            return "provider_config.api_key is required"
        if isinstance(task.max_steps, bool) or not isinstance(task.max_steps, int) or not 1 <= task.max_steps <= 100:
            return "max_steps must be between 1 and 100"
        if (
            isinstance(task.timeout_seconds, bool)
            or not isinstance(task.timeout_seconds, (int, float))
            or not 1 <= task.timeout_seconds <= 7200
        ):
            return "timeout_seconds must be between 1 and 7200"
        if task.memory_filters is not None and not isinstance(task.memory_filters, dict):
            return "memory_filters must be a mapping"
        return ""

    @staticmethod
    def _extract_step_count(agent) -> int:
        """Count model action rounds, excluding task and planning records."""
        memory = getattr(agent, "memory", None)
        steps = getattr(memory, "steps", None)
        if not isinstance(steps, (list, tuple)):
            return 0
        action_steps = [step for step in steps if step.__class__.__name__ == "ActionStep"]
        return len(action_steps) if action_steps else len(steps)

    @staticmethod
    def _serialize_action_step(step, phase: str) -> dict:
        """Return a bounded, JSON-safe trace record for live observability."""
        tool_calls = []
        for call in getattr(step, "tool_calls", None) or []:
            try:
                value = call.dict()
            except Exception:
                value = {"name": getattr(call, "name", "unknown"), "arguments": str(call)}
            tool_calls.append(value)
        error = getattr(step, "error", None)
        return {
            "phase": phase,
            "step": int(getattr(step, "step_number", 0) or 0),
            "tool_calls": tool_calls,
            "observation": str(getattr(step, "observations", "") or "")[:2000],
            "model_output": str(getattr(step, "model_output", "") or "")[:1500],
            "error": str(error)[:2000] if error else None,
            "is_final": bool(getattr(step, "is_final_answer", False)),
        }

    def _build_system_prompt(
        self,
        skill_scope: str,
        fallback_prompt: str,
        memories: list,
        memory_type: str = "",
    ) -> str:
        """构建完整的 system prompt = skill 内容 + 历史记忆

        优先级：SKILL.md > DB 配置 > 空
        """
        parts = []

        # 1. Agent 自己的 SKILL.md 优先
        if fallback_prompt:
            parts.append(fallback_prompt)
            logger.info("AgentService: using configured system prompt")

        if not parts and skill_scope:
            skill = self.skill_registry.get_skill_by_scope(skill_scope)
            if skill and skill.content:
                parts.append(skill.content)
                logger.info("AgentService: using skill scope=%s", skill_scope)

        # 2. SKILL.md 不存在时，才用 DB 配置兜底
        # 2.5. 强制 final_answer 指令（GLM-5.2 容易陷入无限探索）
        parts.append(
            "## 终止规则\n"
            "完成足够深度的根因分析后（定位到具体代码行/PR，或由原始日志证实的环境/配置/依赖问题），调用 final_answer 输出报告。"
            "PR 归因不是必填项：只有日志、运行路径和代码 diff 构成因果链时才关联 PR；否则说明直接原因和证据缺口。"
            "不要停留在表层错误，但也不要为了寻找 PR 重复无新增信息的工具调用。"
        )

        if memory_type == "failure_analysis":
            parts.append(
                "## 回归边界与提交归因规则\n"
                "当前失败运行的 head SHA 只表示已知 bad 边界，绝不能直接称为致错提交。"
                "last-good 必须来自相同 workflow、完全相同 job_name、且 Job 自身 conclusion=success 的最近历史运行。"
                "如果上下文同时给出 Workflow Branch/Head SHA 与 Matrix/Code Target Ref/Tested vllm-ascend Commit，"
                "必须以后者作为代码回归边界；Workflow Branch/Head SHA 只能解释 workflow 触发来源，不能用于源码归因。"
                "当 Matrix/Code Target Ref 是 releases/* 等非 main 分支时，main 上不可达的 PR/commit 必须排除；"
                "如无法从当前和 last-good job log 抽到被测代码 SHA，必须声明缺少代码边界，不得退回使用 main workflow diff。"
                "只有代码回归仍是合理解释时，才检查 last-good..bad 区间内与日志症状有关的提交，并用时序、diff、调用路径和运行证据验证 culprit。"
                "如果只能建立相关性而不能证明因果，必须写成‘候选提交/推断’，列出证据缺口和验证方法，"
                "不得输出确定性根因。历史记忆仅作不可信参考，不得覆盖当前运行的原始日志、Git 历史和代码证据。"
            )
            parts.append(
                "## GitHub Actions 下载日志与完整代码仓交叉验证规则\n"
                "以失败 Job 的完整 job log 以及 steps_data 中失败步骤对应的日志片段为初始切入点；"
                "不要假设失败步骤一定叫 stream log，实际可能叫 Run Pytest (xxx)、Run Test、Check、Capture 等。"
                "当日志已明确证明 Runner、网络、磁盘、镜像/依赖、权限、配置、超时或测试断言问题时，可直接完成失败原因分析，不要求代码或 PR 归因。"
                "精度劣化、性能下降或 benchmark 阈值失败仅说明当前测量未达标；未完成同配置复测、环境排除、因果分析和验收目标确认前，"
                "不得建议修改基线、放宽阈值或降低精度要求。"
                "代码仓是代码回归调查时的核心证据源。日志一旦出现文件、函数、参数、堆栈或行为线索，就立即在对应 commit 读取完整源码、调用链和数据流；"
                "源码产生新假设后再返回同一 Run 的其他日志与 artifacts 验证，允许在日志和代码之间反复往返。"
                "同时确认同一逻辑 Job 最近一次真实成功的时间、run_id 和 commit SHA，以该 SHA 到当前 bad/head SHA 建立回归区间。"
                "任意时刻都可用结构化 Git 工具按 ref 查看 last-good、bad/head 或区间内候选提交，无需 checkout 或修改工作树。"
                "对每个候选提交，必须同时阅读该提交新增或修改的测试、提交说明以及被修改函数的所有调用方；"
                "不得只根据实现 diff 反推作者意图。涉及 rank、TP、head 数、shape、长度或索引计算时，必须代入本次 Job 的具体参数，"
                "分别演算 last-good 与 bad/head 的结果，并与测试断言核对。若候选根因或建议修复与该提交测试的明确断言冲突，"
                "必须先解释为何测试不覆盖本次场景，否则不得将其输出为根因或修复建议。"
                "分析输入是从 GitHub Actions 下载并保存在 backend/data 下的 job/run 日志与 artifacts；"
                "仅当平台与故障相关时，才根据 runner.os、runs-on、shell、路径格式和日志证据判断 Runner 是 Linux 或 Windows，"
                "不得仅因生产环境通常是 Linux 就把 Windows 测试日志按 Linux 解释。"
                "分析过程不假设能够登录 Runner，只能使用 GitHub Job 元数据、已下载日志和 artifacts；证据不足时标记平台未知，"
                "且平台未知不得阻塞与平台无关的日志和代码分析。"
                "Agent 的本地分析宿主也可能是 Windows；禁止把分析宿主行为当成 CI Runner 行为，"
                "也不要依赖 head/tail/sed 等平台专属命令探测本地仓库。"
                "源码调查优先使用 git_commit_range、git_show_commit、git_read_file、git_search_symbol、git_compare_file、git_ref_contains。"
                "提出候选提交/PR 前，必须用 git_ref_contains 或等价证据确认它属于本 Job 的目标 ref/被测 commit 历史；"
                "不属于目标 ref 的提交只能作为反证或排除项，不能作为根因候选。"
                "不能只阅读 diff：必须在 last-good 和 bad/head 两个版本中阅读相关完整文件，搜索所有调用点、配置开关、"
                "数据生产者和消费者，并用失败日志证明本次运行确实进入对应路径。"
                "把候选标记为 rejected 前，必须给出可审计的源码调用链不可达或配置互斥证明；"
                "只看到 TORCH_SDPA/FIA/ACL/backend/runner 等运行时标签时，只能降低优先级，不能直接排除。"
                "如果一个提交同时修改多个可能影响结果的机制，必须分别列为竞争假设；除非日志、运行时数据、"
                "单项回滚或可执行测试能排除其他假设，否则不得声称某一具体机制已确认。完整 revert 只能证明提交相关，"
                "不能隔离提交内部的具体致错改动。修复 diff 若未实际运行测试，必须明确标注为未验证示意。"
                "只要正文存在 evidence gap、needs verification、assumption、most likely、candidate 等未证实表述，"
                "结论段和最终 JSON 的 root_cause_summary 也必须保持候选/推断措辞，不得改写成确定性因果。"
            )

        # 3. 历史记忆
        if memories:
            memory_text = MemoryManager.format_memories_for_prompt(memories)
            parts.append(memory_text)

        return "\n\n".join(parts) if parts else ""

    def _resolve_max_steps(self, task: AgentTask) -> int:
        """Return the configured ceiling; agents may finish before reaching it."""
        return task.max_steps or self.DEFAULT_MAX_STEPS

    def _select_tools(self, task: AgentTask) -> list:
        """根据任务类型选择合适的工具集"""
        if task.memory_type == "failure_analysis":
            return list(FAILURE_ANALYSIS_TOOLS)
        # daily_summary / commit_analysis 通常不需要文件工具
        return list(SUMMARY_TOOLS)

    @staticmethod
    def _clean_output(content: str) -> str:
        """清理输出中的 think 标签残留"""
        import re
        # 移除 <｜end▁of▁thinking｜>... 包围块
        content = re.sub(r"<\s*think\s*>.*?<\s*/\s*think\s*>", "", content,
                         flags=re.DOTALL | re.IGNORECASE)
        # 清理多余空行
        content = re.sub(r"\n{3,}", "\n\n", content)
        content = content.strip()
        if len(content) > 1_000_000:
            content = content[:1_000_000] + "\n\n... (output truncated)"
        return content

    @staticmethod
    def _extract_title(content: str, max_len: int = 200) -> str:
        """从内容中提取一句话标题"""
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        # 跳过 markdown 标题行
        for line in lines:
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                return title[:max_len]
        # 取第一个非空行
        if lines:
            return lines[0][:max_len]
        return ""


# ---------------------------------------------------------------------------
# 便捷函数：与 run_with_fallback 保持接口兼容
# ---------------------------------------------------------------------------


async def run_agent(
    prompt: str,
    provider_config: dict,
    system_prompt: str = "",
    work_dir: str | None = None,
    max_steps: int = 20,
    timeout_seconds: float = 1800,
    memory_type: str = "",
    memory_filters: dict | None = None,
    source_id: int | None = None,
    skill_scope: str = "",
    db=None,
) -> AgentResult:
    """
    便捷函数：执行一次 Agent 任务（接口与 run_with_fallback 类似）。

    注意：需要传入 db session。
    """
    if db is None:
        raise AgentServiceError("db session is required for AgentService")

    agent_svc = AgentService(db)
    return await agent_svc.run(AgentTask(
        prompt=prompt,
        provider_config=provider_config,
        system_prompt=system_prompt,
        skill_scope=skill_scope,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        memory_type=memory_type,
        memory_filters=memory_filters,
        source_id=source_id,
        work_dir=work_dir,
    ))
