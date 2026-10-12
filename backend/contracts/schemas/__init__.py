"""
Pydantic Schemas 定义
"""
import json
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer, field_validator

from infrastructure.core.config import settings

# 导出所有 Schema 类
__all__ = [
    # User
    "UserBase", "UserCreate", "UserUpdate", "UserResponse", "PasswordChange", "PasswordReset",
    # Auth
    "Token", "LoginRequest", "RegisterRequest",
    # CI
    "CIResultBase", "CIResultResponse", "CIJobResponse", "CIJobDetailResponse", "CIStats",
    # Model
    "ModelConfigBase", "ModelConfigCreate", "ModelConfigUpdate", "ModelConfigResponse",
    "ModelReportResponse", "ModelReportCreate", "ModelReportUpdate",
    "ModelTrendData", "ModelComparisonRequest", "ModelComparisonResponse",
    "StartupCommandRequest", "StartupCommandResponse",
    # Model Sync Config
    "ModelSyncConfigBase", "ModelSyncConfigCreate", "ModelSyncConfigUpdate", "ModelSyncConfigResponse",
    # Performance
    "PerformanceDataBase", "PerformanceDataCreate", "PerformanceDataResponse", "PerformanceComparison",
    # Job Owner
    "JobOwnerBase", "JobOwnerCreate", "JobOwnerUpdate", "JobOwnerResponse",
    # Project Dashboard
    "ProjectDashboardConfigResponse", "ProjectDashboardConfigUpdate",
    "ReleaseInfo", "VllmVersionInfo", "ModelSupportMatrix", "ModelSupportEntry",
    # Resource Dashboard
    "KubernetesClusterCreate", "KubernetesClusterUpdate", "KubernetesClusterResponse",
    "KubernetesClusterTestResponse", "ResourceQuantity", "ClusterResourceSummary",
    "ResourcePodInfo", "ResourceDashboardResponse",
    "StaleIssue", "PRActionRequest", "TagComparisonRequest", "TagComparisonResult", "CommitInfo",
    # Resource Metrics
    "NpuMetricPoint", "ClusterNpuMetrics", "NpuMetricsResponse",
    "NodeMetricPoint", "NodeSeries", "ClusterNodeMetrics", "NodeMetricsResponse",
    "ResourceMetricsConfigResponse", "ResourceMetricsConfigUpdate", "RESOURCE_METRICS_CONFIG_KEY",
    # Alert Rules
    "AlertRuleCreate", "AlertRuleUpdate", "AlertRuleResponse",
    "AlertHistoryResponse",
    # Daily Summary
    "GenerateSummaryRequest", "FetchDataRequest", "DailySummaryResponse", "DailySummaryListResponse",
    "DailySummaryListItem", "FetchDataResponse", "GenerateSummaryResponse",
    "LLMProviderResponse", "DailySummaryConfigResponse",
    # Failure Analysis
    "FailureAnalysisResponse", "FailureAnalysisListResponse", "FailureAnalysisAnalyzeRequest",
    # PR Pipeline
    "PullRequestBase", "PullRequestResponse",
    "PRPipelineOverview", "PRPipelineStageDistribution",
    "PRPipelineMetrics", "PRPipelinePercentileMetric",
    "PRPipelineContributor", "PRPipelineKanban",
    "PRPipelineListFilter", "PRPipelineListResponse",
    "PRPipelineTrendPoint", "PRPipelineTrendsResponse",
    "PRPipelineSyncRequest", "PRPipelineHistoricalSyncRequest",
    # Stats
    "LoginStatsResponse", "FeatureUsageStatsResponse", "FeatureUsageTrendPoint",
    # Test Board
    "TestHealthScore", "TestCaseResponse", "TestRunResponse",
    "TestSuiteResponse", "TestOverviewResponse",
    "FlakyCaseDetail", "FailureCategoryBreakdown",
    "OwnerMatrixItem", "ModuleHealthItem",
    "TestCaseFeatureColumn", "TestCaseFeatureMatrixRow",
    "TestCaseFeatureMatrixStatistics", "TestCaseFeatureMatrixResponse",
    "TestBoardSyncRequest", "FailureAnnotationRequest",
    "TestCaseUpdateRequest",
    # Common
    "Message", "PaginatedResponse",
    # Log Center
    "LogQueryRequest", "LogEntryResponse", "LogQueryResponse",
    "LogSourceInfo", "LogSourcesResponse",
    # Contributor Board
    "ContributorBase", "ContributorResponse", "ContributorDetailResponse",
    "ContributorListFilter", "ContributorListResponse",
    "QuadrantPoint", "QuadrantDataResponse",
    "CommitClassificationResponse", "ClassificationSummary",
    "BuildPlanEvaluateRequest", "BuildPlanResponse", "BuildOptimizationStats",
    "BuildPlanListFilter", "BuildPlanListResponse",
    "ContributorRankingItem", "ContributorRankingResponse",
    "ContributorTrendPoint", "ContributorTrendResponse",
    "ClassificationTrendPoint", "ClassificationTrendResponse",
    "ContributorOverviewStats",
    "ContributorSyncRequest", "ContributorSyncResponse",
]


# ============ User Schemas ============

class UserBase(BaseModel):
    """用户基础 Schema"""
    username: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_-]+$')
    email: str | None = Field(None, max_length=100)

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is not None and '@' not in v:
            raise ValueError('无效的邮箱格式')
        return v


class UserCreate(UserBase):
    """创建用户 Schema"""
    password: str = Field(..., min_length=6, max_length=128)
    role: str | None = Field(None, description="用户角色 (user, admin, super_admin)")

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError('密码长度至少为 6 位')
        return v


class UserUpdate(BaseModel):
    """更新用户 Schema"""
    email: str | None = None
    role: str | None = None
    is_active: bool | None = None

    @field_validator('email')
    @classmethod
    def validate_email_update(cls, v: str | None) -> str | None:
        if v is not None and '@' not in v:
            raise ValueError('无效的邮箱格式')
        return v


class PasswordChange(BaseModel):
    """用户修改密码 Schema"""
    old_password: str = Field(..., min_length=6, description="当前密码")
    new_password: str = Field(..., min_length=6, description="新密码")

    @field_validator('new_password')
    @classmethod
    def validate_new_password(cls, v: str, info) -> str:
        """验证新密码"""
        if len(v) < 6:
            raise ValueError('新密码长度至少为 6 位')
        # 获取旧密码进行比较
        old_password = info.data.get('old_password')
        if old_password and v == old_password:
            raise ValueError('新密码不能与旧密码相同')
        return v


class PasswordReset(BaseModel):
    """管理员重置用户密码 Schema"""
    new_password: str = Field(..., min_length=6, description="新密码")


class UserResponse(UserBase):
    """用户响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    is_active: bool
    created_at: datetime


# ============ Auth Schemas ============

class Token(BaseModel):
    """Token 响应"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str


class RegisterRequest(BaseModel):
    """用户注册请求"""
    username: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_-]+$')
    email: str = Field(..., max_length=100)
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is not None and '@' not in v:
            raise ValueError('无效的邮箱格式')
        return v

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError('密码长度至少为 6 位')
        return v


# ============ CI Schemas ============

class CIResultBase(BaseModel):
    """CI 结果基础 Schema"""
    workflow_name: str
    run_id: int
    run_number: int | None = None
    job_name: str | None = None
    status: str
    conclusion: str | None = None
    event: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    hardware: str | None = None


class CIResultResponse(CIResultBase):
    """CI 结果响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    created_at: datetime

    @computed_field
    @property
    def github_html_url(self) -> str | None:
        """构造 GitHub HTML URL"""
        if not self.run_id or not settings.GITHUB_OWNER or not settings.GITHUB_REPO:
            return None
        return f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{self.run_id}"


class CIJobBase(BaseModel):
    """CI Job 基础 Schema"""
    job_id: int
    run_id: int
    workflow_name: str
    job_name: str
    status: str
    conclusion: str | None = None
    hardware: str | None = None
    runner_name: str | None = None


class CIJobResponse(CIJobBase):
    """CI Job 响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    runner_labels: list[str] | None = None
    steps_summary: list[dict] | None = None
    vllm_ascend_commit: str | None = None
    vllm_ascend_commit_date: str | None = None
    vllm_ascend_commit_message: str | None = None
    version_evidence_status: str | None = None
    created_at: datetime

    @computed_field
    @property
    def github_job_url(self) -> str:
        """构造 GitHub Job URL"""
        if not self.run_id or not self.job_id:
            return ""
        return f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{self.run_id}/job/{self.job_id}"


class CIJobDetailResponse(CIJobResponse):
    """CI Job 详细响应 Schema"""
    steps_data: list[dict] | None = None
    logs_url: str | None = None


class CIStats(BaseModel):
    """CI 统计信息"""
    total_runs: int
    passed_runs: int
    failed_runs: int
    other_runs: int
    success_rate: float
    avg_duration_seconds: float | None = None
    last_7_days: dict | None = None


class WorkflowLatestResult(BaseModel):
    """Workflow 最新运行结果"""
    workflow_name: str
    hardware: str | None = None
    latest_run: dict | None = None


class CITrend(BaseModel):
    """CI 趋势数据点"""
    date: str
    total_runs: int
    success_runs: int
    success_rate: float
    avg_duration_seconds: float | None = None
    max_duration_seconds: float | None = None


class CISyncResponse(BaseModel):
    """CI 同步响应"""
    success: bool
    message: str
    collected_count: int | None = None


class CIDailyReport(BaseModel):
    """CI 每日报告"""
    date: str
    summary: dict
    workflow_results: list[dict]
    job_stats: list[dict] | None = None
    markdown_report: str


# ============ Daily Failure Tracking Schemas ============

class DailyFailureJob(BaseModel):
    """每日失败 Job"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    run_id: int
    report_date: str | None = None
    source_branch: str = "main"
    workflow_name: str
    job_name: str
    conclusion: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    hardware: str | None = None
    owner: str | None = None
    owner_email: str | None = None
    display_name: str | None = None
    test_model: str | None = None
    model_fo: str | None = None
    deployment_type: str | None = None
    processing_time: datetime | None = None
    closure_time: datetime | None = None
    processing_status: str = "未处理"
    problem_category: str | None = None
    related_pr: str | None = None
    notes: str | None = None
    updated_by: str | None = None
    status_updated_at: datetime | None = None
    github_job_url: str | None = None


class DailyFailureStats(BaseModel):
    """每日失败统计"""
    date: str
    total_failed_jobs: int
    cancelled: int = 0
    unprocessed: int
    processing: int
    fixed: int
    closed: int


class DailyFailureUpdateRequest(BaseModel):
    """更新处理状态请求"""
    processing_status: str = Field(..., pattern="^(未处理|处理中|已关闭)$")
    owner: str | None = Field(default=None, max_length=100)
    problem_category: str | None = None
    related_pr: str | None = None
    notes: str | None = None
    processing_time: datetime | None = None
    closure_time: datetime | None = None


class DailyFailureBatchUpdateRequest(BaseModel):
    """批量更新失败记录；未提交的字段保持原值。"""

    processing_status: str | None = Field(
        default=None,
        pattern="^(未处理|处理中|已关闭)$",
    )
    owner: str | None = Field(default=None, max_length=100)
    problem_category: str | None = None
    related_pr: str | None = None
    notes: str | None = None
    processing_time: datetime | None = None
    closure_time: datetime | None = None


class DailyFailureListResponse(BaseModel):
    """每日失败列表响应"""
    date: str
    stats: DailyFailureStats
    jobs: list[DailyFailureJob]


# ============ Nightly Test Case Schemas ============

class NightlyTestCaseBase(BaseModel):
    """Nightly 用例基础字段"""
    report_date: date | str | None = None
    source_branch: str = "main"
    workflow_name: str = Field(..., max_length=100)
    job_name: str = Field(..., max_length=500)
    display_name: str | None = Field(None, max_length=200)
    test_model: str | None = Field(None, max_length=200)
    model_fo: str | None = Field(None, max_length=100)
    owner: str | None = Field(None, max_length=100)
    deployment_type: str | None = Field(None, max_length=100)
    notes: str | None = None
    enabled: bool = True


class NightlyTestCaseCreate(NightlyTestCaseBase):
    """创建用例"""
    pass


class NightlyTestCaseUpdate(BaseModel):
    """更新用例（所有字段可选）"""
    report_date: str | None = None
    source_branch: str | None = None
    workflow_name: str | None = Field(None, max_length=100)
    job_name: str | None = Field(None, max_length=500)
    display_name: str | None = Field(None, max_length=200)
    test_model: str | None = Field(None, max_length=200)
    model_fo: str | None = Field(None, max_length=100)
    owner: str | None = Field(None, max_length=100)
    deployment_type: str | None = Field(None, max_length=100)
    notes: str | None = None
    enabled: bool | None = None


class NightlyTestCaseResponse(NightlyTestCaseBase):
    """用例响应"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime | None = None

    @field_serializer('report_date')
    def serialize_report_date(self, value: date | str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, date):
            return value.isoformat()
        return value


# ============ Model Schemas ============

class ModelConfigBase(BaseModel):
    """模型配置基础 Schema"""
    model_name: str = Field(..., max_length=200)
    series: str | None = None
    config_yaml: str | None = None
    official_doc_url: str | None = Field(None, max_length=500)


class ModelConfigCreate(ModelConfigBase):
    """创建模型配置 Schema"""
    pass


class ModelConfigUpdate(BaseModel):
    """更新模型配置 Schema"""
    series: str | None = None
    config_yaml: str | None = None
    status: str | None = None
    key_metrics_config: dict[str, Any] | None = None
    pass_threshold: dict[str, Any] | None = None
    startup_commands: dict[str, dict[str, dict[str, str]]] | None = None
    official_doc_url: str | None = Field(None, max_length=500)


class ModelConfigResponse(ModelConfigBase):
    """模型配置响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    key_metrics_config: dict[str, Any] | None = None
    pass_threshold: dict[str, Any] | None = None
    startup_commands: dict[str, dict[str, dict[str, str]]] | None = None
    official_doc_url: str | None = None
    created_at: datetime
    updated_at: datetime


class ModelReportBase(BaseModel):
    """模型报告基础 Schema"""
    model_config_id: int
    workflow_run_id: int | None = None
    report_json: dict[str, Any]
    pass_fail: str | None = None
    metrics_json: dict[str, Any] | None = None


class ModelReportCreate(ModelReportBase):
    """创建模型报告 Schema"""
    report_markdown: str | None = None
    auto_pass_fail: str | None = None
    vllm_version: str | None = None
    hardware: str | None = None


class ModelReportUpdate(BaseModel):
    """更新模型报告 Schema"""
    pass_fail: str | None = None
    auto_pass_fail: str | None = None
    manual_override: bool | None = None
    metrics_json: dict[str, Any] | None = None
    vllm_version: str | None = None
    vllm_ascend_version: str | None = None
    hardware: str | None = None
    report_json: dict[str, Any] | None = None

    # 新模板字段
    dtype: str | None = None
    features: list[str] | None = None
    serve_cmd: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    tasks: list[dict[str, Any]] | None = None


class ModelReportResponse(BaseModel):
    """模型报告响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    model_config_id: int
    workflow_run_id: int | None = None
    report_json: dict[str, Any] | None = None
    report_markdown: str | None = None
    pass_fail: str | None = None
    auto_pass_fail: str | None = None
    manual_override: bool = False
    metrics_json: dict[str, Any] | None = None
    vllm_version: str | None = None
    hardware: str | None = None
    created_at: datetime

    # 新模板字段
    dtype: str | None = None
    features: list[str] | None = None
    serve_cmd: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    tasks: list[dict[str, Any]] | None = None


class ModelTrendData(BaseModel):
    """模型趋势数据"""
    date: str
    pass_fail: str | None = None
    metrics: dict[str, Any] = {}
    tasks: list[dict[str, Any]] = []  # Task 数据


class ModelComparisonRequest(BaseModel):
    """模型对比请求"""
    report_ids: list[int] = Field(..., min_length=2, max_length=2, description="需要对比的两个报告 ID")


class ModelComparisonResponse(BaseModel):
    """模型对比响应"""
    reports: list[ModelReportResponse]
    metrics_comparison: dict[str, Any]
    changes: dict[str, Any]
    tasks_comparison: list[dict[str, Any]] | None = None  # Task 级别对比数据


class StartupCommandRequest(BaseModel):
    """启动命令请求"""
    vllm_version: str
    command: str
    notes: str | None = None


class StartupCommandResponse(BaseModel):
    """启动命令响应"""
    vllm_version: str
    command: str
    notes: str | None = None


# ============ Performance Schemas ============

class PerformanceDataBase(BaseModel):
    """性能数据基础 Schema"""
    test_name: str
    hardware: str
    model_name: str
    vllm_version: str | None = None
    test_type: str | None = None


class PerformanceDataCreate(PerformanceDataBase):
    """创建性能数据 Schema"""
    metrics_json: dict
    timestamp: datetime


class PerformanceDataResponse(PerformanceDataBase):
    """性能数据响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    vllm_commit: str | None = None
    vllm_ascend_commit: str | None = None
    metrics_json: dict
    timestamp: datetime
    created_at: datetime


class PerformanceComparison(BaseModel):
    """性能对比结果"""
    baseline: dict
    current: dict
    change: dict


# ============ Workflow Config Schemas ============

class WorkflowConfigBase(BaseModel):
    """Workflow 配置基础 Schema"""
    workflow_name: str = Field(..., description="显示名称，如 'Nightly-A2'")
    workflow_file: str = Field(..., description="workflow 文件名，如 'schedule_nightly_test_a2.yaml'")
    hardware: str = Field(..., description="硬件类型：A2, A3, 310P 等")
    event: str | None = "schedule"  # 采集的事件类型，空字符串=不过滤
    actor: str | None = None  # 触发人过滤（GitHub actor login），空=不过滤
    description: str | None = None
    enabled: bool = True
    display_order: int = 0
    stats_start_hour: int | None = Field(None, description="统计和失败物化时间窗口起始小时（0-23），None=不过滤")
    stats_end_hour: int | None = Field(None, description="统计和失败物化时间窗口结束小时（0-23），None=不过滤")
    materialize_name_regex: str | None = Field(
        None,
        description="CI 采集时匹配 GitHub 原始 Workflow 名称的正则；仅决定是否入库，不用于页面展示",
    )
    auto_failure_analysis_enabled: bool = Field(True, description="是否自动分析该 Workflow 的失败 Job；不影响手动分析")
    last_sync_at: datetime | None = Field(None, description="上次同步时间")


class WorkflowConfigCreate(WorkflowConfigBase):
    """创建 Workflow 配置 Schema"""
    pass


class WorkflowConfigUpdate(BaseModel):
    """更新 Workflow 配置 Schema"""
    workflow_name: str | None = None
    workflow_file: str | None = None
    hardware: str | None = None
    event: str | None = None
    actor: str | None = None
    description: str | None = None
    enabled: bool | None = None
    display_order: int | None = None
    stats_start_hour: int | None = None
    stats_end_hour: int | None = None
    materialize_name_regex: str | None = None
    auto_failure_analysis_enabled: bool | None = None


class WorkflowConfigResponse(WorkflowConfigBase):
    """Workflow 配置响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ============ Model Sync Config Schemas ============

class ModelSyncConfigBase(BaseModel):
    """模型同步配置基础 Schema"""
    workflow_name: str = Field(..., description="workflow 显示名称")
    workflow_file: str = Field(..., description="workflow 文件名")
    artifacts_pattern: str | None = Field(None, description="artifacts 名称匹配规则")
    file_patterns: list[str] | None = Field(None, description="需要下载的文件路径模式")
    branch: str = "main"  # 分支名称过滤（如 "main", "zxy_fix_ci"）
    enabled: bool = True


class ModelSyncConfigCreate(ModelSyncConfigBase):
    """创建模型同步配置 Schema"""
    pass


class ModelSyncConfigUpdate(BaseModel):
    """更新模型同步配置 Schema"""
    workflow_name: str | None = None
    workflow_file: str | None = None
    artifacts_pattern: str | None = None
    file_patterns: list[str] | None = None
    branch: str | None = None
    enabled: bool | None = None


class ModelSyncConfigResponse(ModelSyncConfigBase):
    """模型同步配置响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Validator 自动解析数据库中的 JSON 字符串
    @field_validator('file_patterns', mode='before')
    @classmethod
    def parse_file_patterns(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None
        return v


# ============ Failure Analysis Schemas ============

class FailureAnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    run_id: int
    workflow_name: str
    job_name: str
    failure_date: datetime
    failure_fingerprint: str | None = None
    reused_analysis_id: int | None = None
    problem_category: str | None = None
    root_cause_summary: str | None = None
    improvement_measures_summary: str | None = None
    report_file_path: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    generation_time_seconds: float | None = None
    analysis_status: str = "pending"
    analysis_phase: str | None = None
    evidence_ledger: dict | None = None
    validation_result: dict | None = None
    agent_trace: list[dict] | None = None
    agent_steps: int | None = None
    error_message: str | None = None
    triggered_by: str | None = None
    share_token: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("evidence_ledger", mode="before")
    @classmethod
    def normalize_legacy_evidence_ledger(cls, value: Any) -> Any:
        """Keep list-shaped legacy/demo ledgers readable through the API."""
        if isinstance(value, list):
            return {"entries": value}
        return value


class FailureAnalysisListResponse(BaseModel):
    total: int
    items: list[FailureAnalysisResponse]


class FailureAnalysisKnowledgeGraphNode(BaseModel):
    id: str
    type: str
    label: str
    title: str | None = None
    subtitle: str | None = None
    status: str | None = None
    confidence: str | None = None
    data: dict[str, Any] | None = None


class FailureAnalysisKnowledgeGraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    type: str
    data: dict[str, Any] | None = None


class FailureAnalysisKnowledgeGraphResponse(BaseModel):
    analysis_id: int
    generated_at: datetime
    nodes: list[FailureAnalysisKnowledgeGraphNode]
    edges: list[FailureAnalysisKnowledgeGraphEdge]
    stats: dict[str, int]


class FailureAnalysisAnalyzeRequest(BaseModel):
    force: bool = False
    days_back: int = 7


# ============ Stats Schemas ============

class FeatureUsageTrendPoint(BaseModel):
    """功能使用趋势数据点"""
    date: str
    count: int

class LoginStatsResponse(BaseModel):
    """登录统计响应"""
    total_users: int
    active_users_today: int
    active_users_7days: int
    active_users_30days: int
    login_trend: list[dict]
    top_users_by_login_count: list[dict]

class FeatureUsageStatsResponse(BaseModel):
    """功能使用统计响应"""
    total_requests: int
    feature_ranking: list[dict]
    user_activity_ranking: list[dict]
    daily_trend: list[FeatureUsageTrendPoint]


# ============ Common Schemas ============

class Message(BaseModel):
    """通用消息响应"""
    message: str


class PaginatedResponse(BaseModel):
    """分页响应"""
    total: int
    items: list[Any]
    page: int
    page_size: int


# ============ Job Owner Schemas ============

class JobOwnerBase(BaseModel):
    """Job 责任人基础 Schema"""
    workflow_name: str = Field(..., description="Workflow 名称")
    job_name: str = Field(..., description="Job 名称")
    display_name: str | None = Field(None, description="Job 显示名")
    owner: str = Field(..., description="责任人姓名")
    email: str | None = Field(None, description="责任人邮箱")
    notes: str | None = Field(None, description="备注信息")
    is_hidden: bool = Field(False, description="是否隐藏")


class JobOwnerCreate(JobOwnerBase):
    """创建 Job 责任人 Schema"""
    pass


class JobOwnerUpdate(BaseModel):
    """更新 Job 责任人 Schema"""
    owner: str | None = None
    email: str | None = None
    notes: str | None = None
    display_name: str | None = None
    is_hidden: bool | None = None


class JobOwnerResponse(JobOwnerBase):
    """Job 责任人响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# ============ Job Stats Schemas ============

class JobStats(BaseModel):
    """Job 统计数据"""
    workflow_name: str
    job_name: str
    display_name: str | None = None
    owner: str | None = None
    owner_email: str | None = None
    total_runs: int
    success_runs: int
    failure_runs: int
    success_rate: float
    avg_duration_seconds: float | None = None
    min_duration_seconds: int | None = None
    max_duration_seconds: int | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_conclusion: str | None = None


# ============ Project Dashboard Schemas ============

class ReleaseInfo(BaseModel):
    """发布版本信息"""
    version: str
    is_stable: bool
    published_at: datetime
    docker_commands: dict[str, str]  # mirror name -> docker pull command


class VllmVersionInfo(BaseModel):
    """main 分支 vllm 版本信息"""
    vllm_version: str
    vllm_ascend_version: str
    conf_py_url: str
    updated_at: datetime


class ModelSupportEntry(BaseModel):
    """模型支持矩阵条目"""
    model_name: str
    series: str  # Qwen, Llama, etc.
    support: str  # supported, experimental, not_supported, untested
    note: str | None = None
    doc_link: str | None = None
    # Feature support flags
    weight_format: str | None = None  # e.g., "Bfloat16/W8A8"
    kv_cache_type: str | None = None  # e.g., "Bfloat16/Float16"
    supported_hardware: str | None = None
    chunked_prefill: bool | None = None
    automatic_prefix_cache: bool | None = None
    lora: bool | None = None
    speculative_decoding: bool | None = None
    async_scheduling: bool | None = None
    tensor_parallel: bool | None = None
    pipeline_parallel: bool | None = None
    expert_parallel: bool | None = None
    data_parallel: bool | None = None
    prefilled_decode_disaggregation: bool | None = None
    piecewise_aclgraph: bool | None = None
    fullgraph_aclgraph: bool | None = None
    max_model_len: str | int | None = None
    mlp_weight_prefetch: bool | None = None


class ModelSupportMatrix(BaseModel):
    """模型支持矩阵"""
    entries: list[ModelSupportEntry]
    source_url: str
    updated_at: datetime


class StaleIssue(BaseModel):
    """超期未 review 的 issue"""
    number: int
    title: str
    html_url: str
    created_at: datetime
    updated_at: datetime
    days_stale: int
    author: str | None = None
    labels: list[str] = []


class PRActionRequest(BaseModel):
    """PR 操作请求"""
    pr_number: int
    workflow_id: int | None = None  # 用于重新触发 CI 时指定 workflow


class TagComparisonRequest(BaseModel):
    """Tag 对比请求"""
    base_tag: str
    head_tag: str


class CommitInfo(BaseModel):
    """commit 信息"""
    sha: str
    title: str
    message: str
    author: str
    date: datetime
    category: str  # BugFix, Feature, Performance, Refactor, Doc, CI, Misc
    pr_number: int | None = None


class TagComparisonResult(BaseModel):
    """Tag 对比结果"""
    base_tag: str
    head_tag: str
    total_commits: int
    commits: list[CommitInfo]
    summary: dict[str, int]  # category -> count
    bug_fixes: list[CommitInfo]
    features: list[CommitInfo]
    performance_improvements: list[CommitInfo]


class VersionQualityReportRequest(BaseModel):
    """版本质量评估报告生成请求"""
    base_tag: str
    head_tag: str
    force_regenerate: bool = False


class VersionQualityReportMeta(BaseModel):
    """版本质量评估报告元数据"""
    report_id: str
    base_tag: str
    head_tag: str
    generated_at: str
    llm_provider: str | None = None
    llm_model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    generation_time_seconds: float | None = None
    total_commits: int = 0
    open_bugs: int = 0
    merged_prs: int = 0
    ci_success_rate: float | None = None
    stars: int | None = None
    forks: int | None = None
    data_sources: list[str] = Field(default_factory=list)


class ProjectDashboardConfigBase(BaseModel):
    """项目看板配置基础 Schema"""
    config_key: str = Field(..., max_length=100)
    config_value: dict[str, Any]
    description: str | None = Field(None, max_length=500)


class ProjectDashboardConfigUpdate(BaseModel):
    """更新项目看板配置 Schema"""
    config_value: dict[str, Any]
    description: str | None = Field(None, max_length=500)


class ProjectDashboardConfigResponse(ProjectDashboardConfigBase):
    """项目看板配置响应 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# Import Daily Summary schemas
from .alert_rules import (
    AlertConditionCreate,
    AlertConditionGroupCreate,
    AlertConditionGroupResponse,
    AlertConditionResponse,
    AlertHistoryResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
)
from .daily_summary import (
    DailySummaryConfigResponse,
    DailySummaryListItem,
    DailySummaryListResponse,
    DailySummaryResponse,
    FetchDataRequest,
    FetchDataResponse,
    GenerateSummaryRequest,
    GenerateSummaryResponse,
    LLMProviderResponse,
)
from .issue_diagnosis import (
    CIJobOption,
    CommitOption,
    IssueDiagnosisRequest,
)
from .logs import (
    LogEntryResponse,
    LogQueryRequest,
    LogQueryResponse,
    LogSourceInfo,
    LogSourcesResponse,
)
from .nightly_gantt import (
    NightlyGanttItem,
    NightlyGanttKpi,
    NightlyGanttResponse,
    NightlyGanttRunMeta,
)
from .npu_occupancy import (
    NpuOccupancyAnalysisResponse,
    NpuOccupancyDetailsResponse,
    NpuOccupancyOption,
    NpuOccupancyOptionsResponse,
    NpuOccupancyTrendResponse,
)
from .pr_pipeline import (
    PRPipelineContributor,
    PRPipelineHistoricalSyncRequest,
    PRPipelineKanban,
    PRPipelineListFilter,
    PRPipelineListResponse,
    PRPipelineMetrics,
    PRPipelineOverview,
    PRPipelinePercentileMetric,
    PRPipelineStageDistribution,
    PRPipelineSyncRequest,
    PRPipelineTrendPoint,
    PRPipelineTrendsResponse,
    PullRequestBase,
    PullRequestResponse,
)
from .resource_dashboard import (
    ClusterResourceSummary,
    KubernetesClusterCreate,
    KubernetesClusterResponse,
    KubernetesClusterTestResponse,
    KubernetesClusterUpdate,
    ResourceDashboardResponse,
    ResourceNodeInfo,
    ResourcePodInfo,
    ResourceQuantity,
)
from .resource_metrics import (
    RESOURCE_METRICS_CONFIG_KEY,
    ClusterNodeMetrics,
    ClusterNpuMetrics,
    NodeMetricPoint,
    NodeMetricsResponse,
    NodeSeries,
    NpuMetricPoint,
    NpuMetricsResponse,
    ResourceMetricsConfigResponse,
    ResourceMetricsConfigUpdate,
)
from .test_board import (
    FailureAnnotationRequest,
    FailureCategoryBreakdown,
    FlakyCaseDetail,
    ModuleHealthItem,
    OwnerMatrixItem,
    TestBoardSyncRequest,
    TestCaseFeatureColumn,
    TestCaseFeatureMatrixResponse,
    TestCaseFeatureMatrixRow,
    TestCaseFeatureMatrixStatistics,
    TestCaseResponse,
    TestCaseUpdateRequest,
    TestHealthScore,
    TestOverviewResponse,
    TestRunResponse,
    TestSuiteResponse,
)
from .contributor_board import (
    BuildOptimizationStats,
    BuildPlanEvaluateRequest,
    BuildPlanListFilter,
    BuildPlanListResponse,
    BuildPlanResponse,
    ClassificationSummary,
    ClassificationTrendPoint,
    ClassificationTrendResponse,
    CommitClassificationResponse,
    ContributorBase,
    ContributorDetailResponse,
    ContributorListFilter,
    ContributorListResponse,
    ContributorOverviewStats,
    ContributorRankingItem,
    ContributorRankingResponse,
    ContributorResponse,
    ContributorSyncRequest,
    ContributorSyncResponse,
    ContributorTrendPoint,
    ContributorTrendResponse,
    QuadrantDataResponse,
    QuadrantPoint,
)
