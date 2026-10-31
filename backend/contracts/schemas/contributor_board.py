"""
贡献者看板相关的 Pydantic Schemas
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field


# ============ Contributor Schemas ============

class ContributorBase(BaseModel):
    github_id: int = Field(description="GitHub 用户 ID")
    login: str = Field(description="GitHub 用户名")
    avatar_url: str | None = None
    email: str | None = None
    name: str | None = None
    is_community: bool = True
    role_types: list[str] | None = None
    company: str | None = None
    employee_id: str | None = Field(None, description="工号")


class ContributorResponse(ContributorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_contribution_at: datetime | None = None
    last_contribution_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ContributorDetailResponse(ContributorResponse):
    merged_pr_count: int = 0
    pending_pr_count: int = Field(default=0, description="未合并 PR 数")
    code_additions: int = 0
    code_deletions: int = 0
    review_count: int = 0
    approval_count: int = 0
    contribution_score: float = 0.0
    error_score: float = 0.0
    quadrant: str | None = None
    emails: list[str] = Field(default_factory=list, description="关联邮箱列表")
    is_bot: bool = Field(default=False, description="是否为机器人账号")
    ci_failure_count: int = Field(default=0, description="CI 失败次数")
    first_review_hours_avg: float | None = Field(None, description="首次 Review 响应时长（小时均值）")
    merge_hours_avg: float | None = Field(None, description="从创建到合并时长（小时均值）")
    ci_duration_hours_avg: float | None = Field(None, description="CI 执行时长（小时均值）")


# ============ Contributor List Request ============

class ContributorListFilter(BaseModel):
    role_type: str | None = Field(None, description="角色过滤: developer/reviewer/maintainer")
    repo_name: str | None = Field(None, description="仓库名过滤")
    period_start: date | None = None
    period_end: date | None = None
    search: str | None = Field(None, description="搜索用户名")
    quadrant: str | None = Field(None, description="象限过滤: INCENTIVE/IMPROVE/ENCOURAGE/REPLACE")
    sort_by: str | None = Field(None, description="排序字段: score/merged_pr_count/code_additions/code_deletions/pending_pr_count/review_count")
    sort_order: str = Field("desc", description="排序方向: asc/desc")
    limit: int = Field(100, ge=1, le=500, description="返回条数上限")
    offset: int = Field(0, ge=0, description="偏移量")


class ContributorListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contributors: list[ContributorResponse] = []
    total: int = 0


# ============ Quadrant Schemas ============

class QuadrantPoint(BaseModel):
    """四象限散点图中的一个点"""
    login: str = Field(description="用户名")
    avatar_url: str | None = None
    contribution_score: float = Field(description="贡献分数")
    error_score: float = Field(default=0, description="出错分数（原始值）")
    y_value: float = Field(default=0, description="当前Y轴指标值")
    y_axis_type: str = Field(default="error_score", description="当前Y轴类型")
    quadrant: str = Field(description="象限: INCENTIVE/IMPROVE/ENCOURAGE/REPLACE")
    role_label: str | None = Field(None, description="角色标签: star/active/observer/at_risk")
    merged_pr_count: int = Field(default=0, description="合并 PR 数")
    is_community: bool = Field(default=True, description="是否社区贡献者")
    company: str | None = Field(None, description="公司（来自邮箱域名）")
    primary_email: str | None = Field(None, description="主要邮箱")
    is_bot: bool = Field(default=False, description="是否为机器人账号")

    QUADRANT_COLORS: dict[str, str] = {
        "INCENTIVE": "#52c41a",
        "IMPROVE": "#faad14",
        "ENCOURAGE": "#1890ff",
        "REPLACE": "#ff4d4f",
    }

    @computed_field  # type: ignore[misc]
    @property
    def color(self) -> str:
        return self.QUADRANT_COLORS.get(self.quadrant, "#d9d9d9")


class QuadrantDataResponse(BaseModel):
    points: list[QuadrantPoint] = []
    quad_x: float = Field(description="X 轴分割阈值")
    quad_y: float = Field(description="Y 轴分割阈值")
    y_axis_type: str = Field(default="error_score", description="当前 Y 轴指标类型")
    repo_name: str | None = Field(None, description="当前筛选的仓库")
    period_start: date | None = None
    period_end: date | None = None
    total_count: int = 0
    quadrant_counts: dict[str, int] = Field(default_factory=dict, description="各象限人数")


# ============ Commit Classification Schemas ============

class CommitClassificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    commit_sha: str
    commit_message: str | None = None
    commit_type: str
    confidence: float = 1.0
    modules_touched: list[str] | None = None
    files_changed_count: int = 0
    is_chip_specific: bool = False
    chip_target: str | None = None
    contributor_login: str | None = None
    pr_number: int | None = None
    created_at: datetime


class ClassificationSummary(BaseModel):
    total_commits: int = 0
    type_distribution: dict[str, int] = Field(default_factory=dict, description="各类型提交数量")
    type_percentage: dict[str, float] = Field(default_factory=dict, description="各类型提交占比")
    chip_specific_count: int = 0
    module_hotspot: dict[str, int] = Field(default_factory=dict, description="模块热点")


# ============ Build Plan Schemas ============

class BuildPlanEvaluateRequest(BaseModel):
    pr_number: int = Field(description="PR 号")
    commit_message: str | None = Field(None, description="提交信息（可选，不传则从 GitHub 获取）")
    files_changed: list[str] | None = Field(None, description="变更文件列表（可选）")


class BuildPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    pr_number: int
    commit_sha: str
    commit_type: str
    build_type: str = Field(description="构建类型: full_build/minimal_build/nightly_only/skip/smart_check/conservative")
    chips: list[str] | None = None
    os_list: list[str] | None = None
    cache_strategy: str | None = None
    estimated_saving_percent: float = 0
    actual_execution: bool = False
    reasoning: str | None = Field(None, description="决策原因")
    files_changed: list[str] | None = Field(None, description="变更文件列表")
    created_at: datetime | None = None


class BuildOptimizationStats(BaseModel):
    total_evaluations: int = 0
    skipped_builds: int = 0
    reduced_builds: int = 0
    full_builds: int = 0
    avg_saving_percent: float = 0
    by_type: dict[str, int] = Field(default_factory=dict, description="按构建类型分布")


class BuildPlanListFilter(BaseModel):
    pr_number: int | None = None
    build_type: str | None = None
    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)


class BuildPlanListResponse(BaseModel):
    plans: list[BuildPlanResponse] = []
    total: int = 0


# ============ Ranking Schemas ============

class ContributorRankingItem(BaseModel):
    login: str = Field(description="用户名")
    avatar_url: str | None = None
    score: float = Field(description="排行分数")
    rank: int = Field(description="排名")
    merged_pr_count: int = 0
    pending_pr_count: int = Field(default=0, description="未合并 PR 数")
    code_additions: int = Field(default=0, description="代码增加行数")
    code_deletions: int = Field(default=0, description="代码删除行数")
    review_count: int = 0
    approval_count: int = 0
    contribution_score: float = 0
    error_score: float = Field(default=0, description="出错分数")
    commit_count: int = Field(default=0, description="代码提交次数")
    dynamic_quadrant: str | None = Field(None, description="动态四象限: INCENTIVE/IMPROVE/ENCOURAGE/REPLACE")
    role_label: str | None = Field(None, description="角色标签: star/active/observer/at_risk")
    employee_id: str | None = Field(None, description="工号")
    company: str | None = Field(None, description="公司（来自邮箱域名）")
    primary_email: str | None = Field(None, description="主要邮箱")
    is_bot: bool = Field(default=False, description="是否为机器人账号")


class ContributorRankingResponse(BaseModel):
    role_type: str = Field(description="角色类型")
    rankings: list[ContributorRankingItem] = []
    total: int = Field(default=0, description="总记录数")
    repo_name: str | None = Field(None, description="当前筛选的仓库")
    period_start: date | None = None
    period_end: date | None = None


# ============ Settings Schemas ============

class ContributorBoardSetting(BaseModel):
    setting_key: str
    setting_value: dict
    description: str | None = None


class QuadrantConfig(BaseModel):
    y_axis_type: str = Field(default="review_count", description="Y轴指标: error_score/review_count/merged_pr_count/code_additions")


class ScoringWeightsConfig(BaseModel):
    merged_pr_weight: float = Field(default=10.0, ge=0, description="PR合并权重")
    code_line_weight: float = Field(default=0.5, ge=0, description="代码行权重")
    review_weight: float = Field(default=3.0, ge=0, description="Review权重")
    approval_weight: float = Field(default=5.0, ge=0, description="Approval权重")
    core_module_weight: float = Field(default=8.0, ge=0, description="核心模块权重")
    files_changed_weight: float = Field(default=0.3, ge=0, description="文件变更数权重")
    bugfix_weight: float = Field(default=6.0, ge=0, description="BugFix 贡献权重")
    feature_weight: float = Field(default=4.0, ge=0, description="Feature 贡献权重")
    active_duration_weight: float = Field(default=0.01, ge=0, description="活跃持续时间权重（按天）")


class RoleThresholdsConfig(BaseModel):
    star_threshold: float = Field(default=30, ge=0, description="Star(明星)分数阈值")
    active_threshold: float = Field(default=15, ge=0, description="Active(活跃)分数阈值")
    max_error_score: float = Field(default=2, ge=0, description="最大容错分数")


class ContributorBoardSettingsResponse(BaseModel):
    quadrant_config: QuadrantConfig
    scoring_weights: ScoringWeightsConfig
    role_thresholds: RoleThresholdsConfig


class UpdateQuadrantConfigRequest(BaseModel):
    y_axis_type: str = Field(description="Y轴指标: error_score/review_count/merged_pr_count/code_additions")
    repo_name: str | None = Field(None, description="仓库名（用于区分不同仓库的设置）")


class UpdateScoringWeightsRequest(BaseModel):
    merged_pr_weight: float = Field(ge=0)
    code_line_weight: float = Field(ge=0)
    review_weight: float = Field(ge=0)
    approval_weight: float = Field(ge=0)
    core_module_weight: float = Field(ge=0)
    files_changed_weight: float = Field(ge=0, default=0.3)
    bugfix_weight: float = Field(ge=0, default=6.0)
    feature_weight: float = Field(ge=0, default=4.0)
    active_duration_weight: float = Field(ge=0, default=0.01)
    repo_name: str | None = Field(None, description="仓库名（用于区分不同仓库的设置）")


class UpdateRoleThresholdsRequest(BaseModel):
    star_threshold: float = Field(ge=0)
    active_threshold: float = Field(ge=0)
    max_error_score: float = Field(ge=0)
    repo_name: str | None = Field(None, description="仓库名（用于区分不同仓库的设置）")


# ============ Sync Schedule Schemas ============

class ContributorSyncScheduleConfig(BaseModel):
    """定时同步设置"""
    full_cron: str = Field(
        default="0 2 * * *",
        description="全量同步 CRON 表达式（5字段，默认每日 02:00）"
    )
    incremental_interval_minutes: int = Field(
        default=360, ge=30, le=1440,
        description="增量同步间隔（分钟），最小 30，最大 1440（24小时）"
    )
    enabled: bool = Field(
        default=True, description="是否启用定时同步"
    )


class ContributorSyncScheduleUpdateRequest(BaseModel):
    full_cron: str | None = Field(None, description="全量同步 CRON 表达式（5字段）")
    incremental_interval_minutes: int | None = Field(None, ge=30, le=1440, description="增量同步间隔（分钟）")
    enabled: bool | None = Field(None, description="是否启用定时同步")


# ============ Trend Schemas ============

class ContributorTrendPoint(BaseModel):
    date: date
    merged_pr_count: int = 0
    code_additions: int = 0
    code_deletions: int = 0
    review_count: int = 0
    contribution_score: float = 0
    error_score: float = 0


class ContributorTrendResponse(BaseModel):
    login: str
    points: list[ContributorTrendPoint] = []
    period_start: date | None = None
    period_end: date | None = None


class ClassificationTrendPoint(BaseModel):
    date: date
    type_distribution: dict[str, int] = Field(default_factory=dict)


class ClassificationTrendResponse(BaseModel):
    points: list[ClassificationTrendPoint] = []
    period_start: date | None = None
    period_end: date | None = None


# ============ Overview Stats ============

class ContributorOverviewStats(BaseModel):
    total_contributors: int = 0
    community_contributors: int = 0
    total_commits_classified: int = 0
    classification_distribution: dict[str, int] = Field(default_factory=dict)
    quadrant_distribution: dict[str, int] = Field(default_factory=dict)
    build_optimization_stats: BuildOptimizationStats = Field(default_factory=BuildOptimizationStats)
    top_contributors: list[ContributorRankingItem] = []
    last_synced_at: datetime | None = None


# ============ Sync Schemas ============

class ContributorSyncRequest(BaseModel):
    since_date: date | None = Field(None, description="增量同步起始日期，不传则从 2024-01-01 全量采集")
    until_date: date | None = Field(None, description="增量同步截止日期，不传则为当前时间")
    full_resync: bool = Field(False, description="是否全量重新评估")
    repo_name: str | None = Field(None, description="指定仓库名，不传则同步所有仓库")


class ContributorSyncResponse(BaseModel):
    contributors_synced: int = 0
    commits_classified: int = 0
    build_plans_created: int = 0
    errors: list[str] = []
    duration_seconds: float = 0
    synced_at: datetime | None = None
    record_id: int | None = Field(None, description="同步记录 ID")


class ContributorSyncRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repo_name: str | None = None
    sync_type: str = ""
    status: str
    since_date: date | None = None
    until_date: date | None = None
    contributors_synced: int = 0
    commits_classified: int = 0
    total_prs_scanned: int = 0
    build_plans_created: int = 0
    errors: list[str] | None = None
    duration_seconds: float = 0
    synced_at: datetime | None = None
    progress_percent: float = 0
    progress_message: str | None = None
    trigger_source: str = "manual"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    @property
    def error_list(self) -> list[str]:
        if isinstance(self.errors, list):
            return self.errors
        if isinstance(self.errors, str):
            try:
                import json
                return json.loads(self.errors)
            except Exception:
                return [self.errors]
        return []


class ContributorSyncHistoryResponse(BaseModel):
    records: list[ContributorSyncRecordResponse] = []
    total: int = 0


class ContributorSyncSummary(BaseModel):
    """同步统计摘要"""
    total_syncs: int = 0
    completed_count: int = 0
    failed_count: int = 0
    running_count: int = 0
    total_contributors: int = 0
    total_commits: int = 0
    total_build_plans: int = 0
    total_prs_scanned: int = 0
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None


# ============ Role Stats ============

class RoleStats(BaseModel):
    developer_count: int = 0
    reviewer_count: int = 0
    maintainer_count: int = 0
    total_pr: int = 0
    total_code_lines: int = 0
    total_reviews: int = 0
    total_approvals: int = 0


class ModuleHotspotItem(BaseModel):
    module: str
    count: int
    percentage: float = Field(default=0, description="占总变更的百分比")
    contributors: int = Field(default=0, description="涉及贡献者数")


class ModuleHotspotResponse(BaseModel):
    modules: list[ModuleHotspotItem] = []
    total_changes: int = 0
    repo_name: str | None = Field(None, description="当前筛选的仓库")
    period_start: date | None = None
    period_end: date | None = None


# ============ Policy Schemas ============

class PolicyItemResponse(BaseModel):
    action: str = Field(description="策略动作")
    severity: str = Field(description="严重程度: info/warning/critical")
    execution: str = Field(description="执行方式: auto/suggestion/alert")
    description: str = Field(description="策略描述")
    reason: str = Field(description="策略原因")
    impact_level: str = Field(default="", description="影响等级: 高/中/低")


class PolicyResultResponse(BaseModel):
    login: str
    quadrant: str
    contribution_score: float
    error_score: float
    policies: list[PolicyItemResponse] = []
    has_critical: bool = False
    has_warning: bool = False
    evaluated_at: datetime | None = None
    auto_count: int = 0
    suggestion_count: int = 0
    alert_count: int = 0


# ============ Trend Schemas (Extended) ============

class CommitTrendPoint(BaseModel):
    date: str
    merged_pr_count: int = 0
    code_additions: int = 0
    code_deletions: int = 0


class ReviewTrendPoint(BaseModel):
    date: str
    review_count: int = 0
    approval_count: int = 0


class ScoreTrendPoint(BaseModel):
    date: str
    contribution_score: float = 0
    error_score: float = 0
    review_count: int = 0
    merged_pr_count: int = 0
    code_additions: int = 0


class ExtendedTrendResponse(BaseModel):
    login: str
    commit_trend: list[CommitTrendPoint] = []
    review_trend: list[ReviewTrendPoint] = []
    score_trend: list[ScoreTrendPoint] = []


# ============ 热力图 Schemas ============

class HeatmapDataPoint(BaseModel):
    date: str = Field(description="日期 (YYYY-MM-DD)")
    commit_count: int = Field(default=0, description="当日提交数")
    weekDay: int = Field(default=0, description="星期几 (0=Mon...6=Sun)")


class ContributorHeatmapResponse(BaseModel):
    login: str
    heatmap_data: list[HeatmapDataPoint] = []


# ============ 模块+类型分布 Schemas ============

class ModuleDistributionItem(BaseModel):
    module: str
    commit_count: int = 0
    code_additions: int = 0
    code_deletions: int = 0


class TypeDistributionItem(BaseModel):
    commit_type: str
    count: int = 0


class ContributorDistributionResponse(BaseModel):
    login: str
    modules: list[ModuleDistributionItem] = []
    type_distribution: list[TypeDistributionItem] = []


# ============ 提交时间线 Schemas ============

class TimelineCommitItem(BaseModel):
    commit_hash: str
    message: str = ""
    classification: str = ""
    module: str = ""
    committed_at: datetime | None = None
    files_changed: int = 0
    code_additions: int = 0
    code_deletions: int = 0


class ContributorTimelineResponse(BaseModel):
    login: str
    commits: list[TimelineCommitItem] = []


# ============ Common ============

class Message(BaseModel):
    success: bool
    message: str
