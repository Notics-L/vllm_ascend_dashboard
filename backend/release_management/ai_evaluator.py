"""
AI 可信度评估 - 算法评分 + LLM 综合分析
"""
import json
import logging
import statistics
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_client import GitHubAuthenticationError
from infrastructure.clients.llm_client import LLMClient, LLMError
from infrastructure.persistence.models import (
    ReleaseAnalysisTask,
    ReleaseAIEvaluation,
    ReleaseBuildRun,
    ReleaseThirdPartyBaseline,
    ReleaseThirdPartyVersion,
)
from infrastructure.persistence.models.daily_summary import LLMProviderConfig

logger = logging.getLogger(__name__)


class TrustworthinessEvaluator:
    """数据可信度评估：算法评分 + AI 综合分析"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate(
        self,
        workflow_id: int,
        evaluation_type: str = "trustworthiness",
        period_start: str | None = None,
        period_end: str | None = None,
        model_name: str | None = None,
        generated_by: str | None = None,
    ) -> tuple[int, ReleaseAIEvaluation, ReleaseAnalysisTask]:
        """执行评估，返回 (task_id, evaluation, task)"""
        task = ReleaseAnalysisTask(
            workflow_id=workflow_id,
            task_type="evaluate",
            status="running",
            progress=10,
            started_at=datetime.now(UTC),
        )
        self.db.add(task)
        await self.db.flush()

        conditions = [ReleaseBuildRun.workflow_id == workflow_id]
        ps = None
        pe = None
        if period_start:
            ps = datetime.fromisoformat(period_start)
            conditions.append(ReleaseBuildRun.created_at_remote >= ps)
        if period_end:
            pe = datetime.fromisoformat(period_end)
            conditions.append(ReleaseBuildRun.created_at_remote <= pe)

        runs = (await self.db.execute(
            select(ReleaseBuildRun).where(*conditions).order_by(
                ReleaseBuildRun.created_at_remote
            )
        )).scalars().all()

        if not runs:
            task.status = "failed"
            task.error_message = "没有找到构建记录"
            task.completed_at = datetime.now(UTC)
            await self.db.commit()
            raise ValueError("没有找到构建记录")

        task.progress = 20
        await self.db.flush()

        algorithm_result = self.calculate_algorithm_score(runs)

        task.progress = 50
        await self.db.flush()

        ai_result = {}
        trust_score = algorithm_result["total_score"]
        trust_level = algorithm_result["level"]
        production_ready = trust_score >= 80

        try:
            ai_result = await self._ai_comprehensive_eval(
                algorithm_result, list(runs), model_name
            )
            if ai_result.get("production_ready") is not None:
                production_ready = ai_result["production_ready"]
        except (LLMError, GitHubAuthenticationError, Exception) as e:
            logger.warning(f"AI evaluation failed, using algorithm-only results: {e}")

        task.progress = 80
        await self.db.flush()

        evaluation = ReleaseAIEvaluation(
            workflow_id=workflow_id,
            evaluation_type=evaluation_type,
            period_start=ps.date() if ps else None,
            period_end=pe.date() if pe else None,
            run_count=len(runs),
            trust_score=trust_score,
            trust_level=trust_level,
            production_ready=production_ready,
            llm_model=model_name or "algorithm_only",
            executive_summary=ai_result.get("executive_summary", ""),
            findings=json.dumps(ai_result.get("findings", []), ensure_ascii=False),
            recommendations=json.dumps(ai_result.get("recommendations", []), ensure_ascii=False),
            risk_factors=json.dumps(ai_result.get("risk_factors", []), ensure_ascii=False),
            data_quality_issues=json.dumps(ai_result.get("data_quality_issues", []), ensure_ascii=False),
            report_content=json.dumps(ai_result, ensure_ascii=False),
            generated_by=generated_by,
        )
        self.db.add(evaluation)

        task.status = "completed"
        task.progress = 100
        task.completed_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(evaluation)

        return task.id, evaluation, task

    @staticmethod
    def calculate_algorithm_score(runs: list) -> dict[str, Any]:
        """基于算法的可信度评分 (0-100)"""
        total = len(runs)
        if total == 0:
            return {"total_score": 0, "breakdown": {}, "level": "low"}

        # 1. 成功率维度 (25%)
        success_rate = sum(1 for r in runs if r.status == "success") / total
        success_score = success_rate * 25

        # 2. 稳定性维度 (25%): CV 越低越稳定
        durations = [r.exec_duration for r in runs if r.exec_duration > 0]
        if len(durations) > 2:
            cv = statistics.stdev(durations) / max(statistics.mean(durations), 1)
            stability_score = max(0, (1 - cv / 0.5)) * 25
        else:
            stability_score = 12.5

        # 3. 覆盖度维度 (20%)
        unique_archs = set()
        unique_os = set()
        unique_branches = set()
        for r in runs:
            if r.branch:
                unique_branches.add(r.branch)
        coverage_raw = min(len(unique_archs) * 3 + len(unique_os) * 3 + min(len(unique_branches), 3) * 4, 100)
        coverage_score = coverage_raw / 100 * 20

        # 4. 合规性维度 (15%) — 默认满分，后续与三方件联合
        compliance_score = 15

        # 5. 趋势维度 (15%): 最近 5 次 vs 之前
        recent_5 = runs[-5:] if len(runs) >= 10 else runs
        success_recent = sum(1 for r in recent_5 if r.status == "success") / max(len(recent_5), 1)
        trend_score = success_recent * 15

        total_score = round(
            success_score + stability_score + coverage_score + compliance_score + trend_score, 1
        )
        total_score = min(total_score, 100)

        return {
            "total_score": total_score,
            "breakdown": {
                "success": round(success_score, 1),
                "stability": round(stability_score, 1),
                "coverage": round(coverage_score, 1),
                "compliance": round(compliance_score, 1),
                "trend": round(trend_score, 1),
            },
            "level": "high" if total_score >= 80 else ("medium" if total_score >= 60 else "low"),
        }

    async def _ai_comprehensive_eval(
        self,
        algorithm_result: dict[str, Any],
        runs: list,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """调用 LLM 生成综合评估"""
        try:
            active_provider = (await self.db.execute(
                select(LLMProviderConfig).where(
                    LLMProviderConfig.enabled, LLMProviderConfig.is_active
                ).limit(1)
            )).scalar_one_or_none()

            if not active_provider:
                return {}

            active_model = model_name or active_provider.default_model
        except Exception as e:
            logger.warning(f"Could not get active LLM provider: {e}")
            return {}

        system_prompt = (
            "你是一个软件工程发布质量评估专家。"
            "基于提供的构建数据和算法评分，对发布件进行可信度评估，"
            "并给出是否达到生产级的判断和建议。\n\n"
            "请严格以 JSON 格式返回，包含以下字段：\n"
            "- executive_summary: 100 字以内的执行摘要\n"
            '- findings: 核心发现列表 [{id, title, description, severity: "high"|"medium"|"low"}]\n'
            '- risk_factors: 风险因子列表 [{name, description, impact}]\n'
            "- data_quality_issues: 数据质量问题列表\n"
            '- recommendations: 改进建议列表 [{title, description, priority: "P0"|"P1"|"P2"}]\n'
            '- production_ready: boolean（是否达到生产级）\n'
            "- production_ready_reason: 判断理由"
        )

        stats_text = self._build_data_context(runs, algorithm_result)

        user_prompt = f"""请对以下构建数据进行可信度评估：

{stats_text}

算法评分：{json.dumps(algorithm_result, ensure_ascii=False, indent=2)}

请从以下角度判断：
1. 数据可信度：成功率、稳定性、覆盖度、趋势
2. 生产级标准：成功率 >= 95%，耗时波动可控，有多架构覆盖，最近趋势稳定
3. 风险因素：频繁失败、耗时异常增长、版本不一致等
"""

        try:
            llm = LLMClient()
            result = await llm.generate(
                provider=active_provider.provider,
                model=active_model,
                api_key=active_provider.api_key,
                api_base=active_provider.api_base_url,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=4096,
            )

            return json.loads(result.content)
        except (LLMError, json.JSONDecodeError, Exception) as e:
            logger.warning(f"LLM call failed: {e}")
            return {}

    @staticmethod
    def _build_data_context(runs: list, algorithm_result: dict) -> str:
        total = len(runs)
        success_ct = sum(1 for r in runs if r.status == "success")
        fail_ct = sum(1 for r in runs if r.status == "failure")

        exec_durs = [r.exec_duration for r in runs if r.exec_duration > 0]
        avg_exec = sum(exec_durs) / len(exec_durs) if exec_durs else 0
        min_exec = min(exec_durs) if exec_durs else 0
        max_exec = max(exec_durs) if exec_durs else 0

        wait_durs = [r.wait_duration for r in runs]
        avg_wait = sum(wait_durs) / len(wait_durs) if wait_durs else 0

        branches = set(r.branch for r in runs if r.branch)

        return (
            f"总运行次数: {total}\n"
            f"成功: {success_ct}, 失败: {fail_ct}, 成功率: {success_ct / max(total, 1) * 100:.1f}%\n"
            f"平均执行耗时: {avg_exec:.0f}s, 最短: {min_exec:.0f}s, 最长: {max_exec:.0f}s\n"
            f"平均排队时间: {avg_wait:.0f}s\n"
            f"涉及分支数: {len(branches)} ({', '.join(list(branches)[:5])})"
        )
