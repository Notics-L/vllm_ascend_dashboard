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

                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    # If no LLM result, build algorithm-only report
        if not ai_result:
            stats_text = self._build_data_context(runs, algorithm_result)
            ai_result = {
                "executive_summary": (
                    f"基于算法的自动评估（未配置 LLM）。"
                    f"本次评估覆盖 {len(runs)} 次构建，"
                    f"可信度评分 {trust_score}/100，"
                    f"等级: {trust_level}。"
                    f"{'达到生产级标准' if production_ready else '未完全达到生产级标准'}。"
                ),
                "algorithm_breakdown": algorithm_result.get("breakdown", {}),
                "data_summary": stats_text,
                "findings": [],
                "risk_factors": [],
                "recommendations": [],
                "data_quality_issues": [],
                "production_ready": production_ready,
                "production_ready_reason": (
                    f"算法评分 {trust_score}/100，{'>= 80' if production_ready else '< 80'}"
                ),
            }

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
        """基于算法的可信度评分 (0-100)

        评分维度:
        1. 成功率 (30%)
        2. 稳定性 (25%): 使用平滑曲线而非激进截断
        3. 趋势 (20%): 近期 vs 整体
        4. 合规性 (15%): 默认满分
        5. 覆盖率 (10%): 分支/Job 覆盖
        """
        total = len(runs)
        if total == 0:
            return {"total_score": 0, "breakdown": {}, "level": "low"}

        # 1. 成功率维度 (30%)
        success_rate = sum(1 for r in runs if r.status == "success") / total
        success_score = round(success_rate * 30, 1)

        # 2. 稳定性维度 (25%): 使用平滑 S 曲线
        durations = [r.exec_duration for r in runs if r.exec_duration > 0]
        if len(durations) > 2:
            avg_dur = statistics.mean(durations)
            stdev_dur = statistics.stdev(durations)
            cv = stdev_dur / max(avg_dur, 1)
            # 平滑曲线: CV=0 → 25 分, CV=0.5 → 18 分, CV=1.0 → 12 分, CV>=2 → 5 分
            stability_score = round(max(5, 25 - (cv * 10)), 1)
        else:
            stability_score = 18.0

        # 3. 趋势维度 (20%): 最近 N 次 vs 整体成功率
        # 取最近 1/3 数据（至少 3 条）与整体比较
        split_n = max(3, total // 3)
        recent = runs[-split_n:]
        earlier = runs[:-split_n] if len(runs) > split_n else recent

        success_recent = sum(1 for r in recent if r.status == "success") / max(len(recent), 1)
        success_earlier = sum(1 for r in earlier if r.status == "success") / max(len(earlier), 1)

        # 趋势分数：以近期成功率为基础，如果近期比早期好则加分
        if success_recent >= success_earlier:
            trend_score = round(min(20, success_recent * 20 + (success_recent - success_earlier) * 20), 1)
        else:
            trend_score = round(success_recent * 20, 1)
        trend_score = min(trend_score, 20)

        # 4. 合规性维度 (15%) — 默认满分，后续与三方件联合评估
        compliance_score = 15.0

        # 5. 覆盖率维度 (10%): 基于不同分支和 job 数量
        unique_branches = set()
        total_jobs = []
        for r in runs:
            if r.branch:
                unique_branches.add(r.branch)
            if r.num_jobs > 0:
                total_jobs.append(r.num_jobs)

        # 分支覆盖: 1 分支 → 2 分, 2+ → 4 分, 3+ → 5 分
        branch_score = min(5, len(unique_branches) * 2)

        # Job 多样性: 构建包含多个 job → 说明测试覆盖面广
        if total_jobs:
            avg_jobs = sum(total_jobs) / len(total_jobs)
            job_score = min(5, avg_jobs / 2)  # 10 jobs → 5 分满分
        else:
            job_score = 1.0
        coverage_score = round(branch_score + job_score, 1)

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
