"""评分权重版本管理 — Pydantic Schema"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ScoringVersionCreateRequest(BaseModel):
    method: str = Field(..., description="权重确定方式: manual/auto/ai")
    weights: dict[str, float] = Field(..., description="权重配置")
    data_summary: dict | None = Field(None, description="数据分布摘要（auto/ai 模式时）")
    confidence_score: float | None = Field(None, ge=0, le=1, description="置信度评分")
    ai_prompt: str | None = Field(None, description="AI 评权 prompt")
    ai_response: str | None = Field(None, description="AI 原始响应")
    ai_model: str | None = Field(None, description="使用的 AI 模型")
    description: str | None = Field(None, description="版本说明")
    repo_name: str | None = Field(None, description="仓库名")
    apply_immediately: bool = Field(False, description="是否立即应用为 active")


class ScoringVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    repo_name: str | None
    weights: dict
    method: str
    status: str
    data_summary: dict | None
    ai_prompt: str | None = None
    ai_response: str | None = None
    ai_model: str | None = None
    confidence_score: float | None = None
    description: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    activated_at: datetime | None = None
    activated_by: str | None = None


class VersionCompareResponse(BaseModel):
    version_a: ScoringVersionResponse
    version_b: ScoringVersionResponse
    weight_diff: dict  # {dim: {from, to, change, change_pct}}
    score_impact: dict | None = None  # {contributor_count, quadrant_changes: [...]}


class ScoreSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: int
    repo_name: str | None
    snapshot_date: date
    period_start: date | None = None
    period_end: date | None = None
    contributor_count: int
    statistics: dict | None = None
    scores: list[dict] | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    description: str | None = None


class ScoreUpdateAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    version_id: int | None = None
    snapshot_id: int | None = None
    repo_name: str | None = None
    created_at: datetime | None = None


class CalculateAutoWeightsResponse(BaseModel):
    weights: dict[str, float]
    confidence: float
    data_summary: dict
    version_id: int | None = None


# ============ AI 报告 Schema ============

class AIReportGenerateRequest(BaseModel):
    report_type: str = Field("custom", description="quarterly/monthly/custom/adhoc")
    period_start: date | None = Field(None, description="分析周期起始日期")
    period_end: date | None = Field(None, description="分析周期结束日期")
    repo_name: str | None = Field(None, description="仓库名")
    model: str | None = Field(None, description="指定 LLM 模型")
    include_individual_evaluations: bool = Field(False, description="是否包含个人评价")


class AIReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_name: str
    report_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    generated_at: datetime | None = None
    generated_by: str | None = None
    generation_source: str | None = None
    weight_version_id: int | None = None
    llm_model: str | None = None
    executive_summary: str | None = None
    confidence_overall: float | None = None
    repo_name: str | None = None
    created_at: datetime | None = None


class AIReportDetailResponse(BaseModel):
    """AI 综合分析报告详情 — 结构化 5 维 + markdown 双轨。

    结构化字段（卡片渲染）：team_health / core_contributors / new_contributors /
    improvement_areas，每个为 dict: {"metrics": {量化指标}, "content": "markdown 正文"}。
    recommendations: list；top_contributors: list（Top N 量化明细）；risk_level: low/medium/high。
    兼容字段：quadrant_analysis / individual_evaluations / report_content。
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_name: str
    report_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    generated_at: datetime | None = None
    generated_by: str | None = None
    generation_source: str | None = None
    weight_version_id: int | None = None
    llm_model: str | None = None
    executive_summary: str | None = None
    # ===== 结构化 5 维（新） =====
    team_health: dict | None = None
    core_contributors: dict | None = None
    new_contributors: dict | None = None
    improvement_areas: dict | None = None
    recommendations: list[dict | str] | None = None
    top_contributors: list[dict] | None = None
    risk_level: str | None = None
    # ===== 兼容字段（旧前端） =====
    quadrant_analysis: dict | None = None
    individual_evaluations: list[dict] | None = None
    confidence_overall: float | None = None
    report_content: str | None = None
    report_file_url: str | None = None
    schedule_cron: str | None = None
    enabled: bool = True
    repo_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
