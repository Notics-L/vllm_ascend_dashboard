"""
数据模型定义
"""
from datetime import UTC, datetime, timezone

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import backref, declarative_base, relationship
from sqlalchemy.types import JSON

# 创建基类
Base = declarative_base()

# 导出所有模型类，方便其他地方导入
__all__ = [
    "Base", "User", "ModelConfig", "ModelReport", "CIResult", "CIJob",
    "DailyFailureRecord",
    "WorkflowConfig", "PerformanceData", "JobOwner", "NightlyTestCase", "ModelFoMapping",
    "ModelSyncConfig", "ProjectDashboardConfig", "KubernetesClusterConfig",
    "DailyPR", "DailyIssue", "DailyCommit", "DailySummary", "LLMProviderConfig",
    "DailyReportHistory", "ResourceNpuMetrics", "JobFailureAnalysis",
    "ResourceNodeMetrics",
    "AlertRule", "AlertConditionGroup", "AlertCondition", "AlertHistory",
    "JobFailureAnalysis",
    "PullRequest",
    "UserLoginLog", "FeatureUsageLog", "TokenBlacklist",
    "IssueDiagnosisHistory",
    "TestCase", "TestRun", "TestSuiteSnapshot", "FailureAnnotation",
    "AppLog", "AnalysisMemory", "AnalysisEmbedding",
    "SchedulerHeartbeat",
    "NpuOccupancyRawEnv", "NpuOccupancySyncState",
    "CINpuJobFact", "CINpuQueueSnapshot", "CINpuSyncState",
    "Contributor", "ContributorMetrics", "CommitClassification", "ErrorRecord", "ImageBuildPlan", "QuadrantSnapshot",
    "ContributorScoringVersion", "ContributorScoreSnapshot", "ContributorScoreUpdateAudit",
    "ContributorAIReport",
    "ReleaseWorkflow", "ReleaseBuildRun", "ReleaseBuildGroup",
    "ReleaseThirdPartyVersion", "ReleaseThirdPartyBaseline",
    "ReleaseAIEvaluation", "ReleaseAnalysisTask",
]


# 发布件管理模型（必须在 Base 创建之后导入，避免循环导入）
from infrastructure.persistence.models.release_management import (
    ReleaseAIEvaluation,
    ReleaseAnalysisTask,
    ReleaseBuildGroup,
    ReleaseBuildRun,
    ReleaseSyncSchedule,
    ReleaseThirdPartyBaseline,
    ReleaseThirdPartyVersion,
    ReleaseWorkflow,
)


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user", index=True)  # user, admin, super_admin
    email = Column(String(100), unique=True, nullable=False)  # unique=True 已自带索引
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    # 关系
    model_configs = relationship("ModelConfig", back_populates="creator")
    login_logs = relationship("UserLoginLog", back_populates="user", cascade="all, delete-orphan")
    usage_logs = relationship("FeatureUsageLog", back_populates="user", cascade="all, delete-orphan")


class ModelConfig(Base):
    """模型配置表"""
    __tablename__ = "model_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(200), nullable=False, index=True)
    series = Column(String(50), index=True)  # Qwen, Llama, DeepSeek, Other
    config_yaml = Column(Text)
    status = Column(String(20), default="active")  # active, inactive
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    # 新增字段：关键指标配置、Pass 阈值、启动命令（多版本）
    key_metrics_config = Column(Text)  # JSON 格式，配置关键 metrics
    pass_threshold = Column(Text)  # JSON 格式，Pass 判定阈值
    startup_commands = Column(Text)  # JSON 格式，存储多版本 vLLM 启动命令
    official_doc_url = Column(String(500))  # 官方文档链接

    # 关系
    creator = relationship("User", back_populates="model_configs")
    reports = relationship("ModelReport", back_populates="model_config", cascade="all, delete-orphan")


class ModelReport(Base):
    """模型看板报告表"""
    __tablename__ = "model_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_config_id = Column(Integer, ForeignKey("model_configs.id"), index=True)
    model_registry_id = Column(Integer, ForeignKey("model_registry.id"), index=True)  # 过渡期双写
    workflow_run_id = Column(BigInteger, index=True)  # GitHub workflow run ID
    report_json = Column(JSON, nullable=False)
    pass_fail = Column(String(10))  # pass, fail
    metrics_json = Column(JSON)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), index=True)

    # 新增字段
    report_markdown = Column(Text)  # Markdown 格式报告原文
    auto_pass_fail = Column(String(10))  # 系统自动判定的结果
    manual_override = Column(Boolean, default=False)  # 是否手动覆盖过 Pass/Fail
    vllm_version = Column(String(50))  # vLLM 版本
    hardware = Column(String(20))  # 硬件类型：A2, A3

    # 新模板字段
    dtype = Column(String(50))  # 权重类型：w8a8, fp16 等
    features = Column(JSON)  # 特性列表：["mlp_prefetch", "bbb"]
    serve_cmd = Column(JSON)  # 启动命令：{"mix": "..."} 或 {"pd": {...}}
    environment = Column(JSON)  # 环境变量：{"ENV1": "aaa"}
    tasks = Column(JSON)  # 完整的 tasks 数组（包含 test_input, target 等）

    # 关系
    model_config = relationship("ModelConfig", back_populates="reports")


class CIResult(Base):
    """CI 结果表（workflow 级别）"""
    __tablename__ = "ci_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_name = Column(String(100), nullable=False, index=True)
    run_id = Column(BigInteger, nullable=False, unique=True)
    run_number = Column(Integer)  # workflow run 编号
    status = Column(String(20), index=True)  # completed, in_progress, queued
    conclusion = Column(String(20))  # success, failure, cancelled
    event = Column(String(50))  # schedule, push, pull_request
    branch = Column(String(100))  # 分支名
    head_sha = Column(String(100))  # commit sha
    started_at = Column(TIMESTAMP, index=True)
    completed_at = Column(TIMESTAMP, index=True)
    duration_seconds = Column(Integer)
    hardware = Column(String(20), index=True)  # A2, A3
    data = Column(Text)  # 完整的 workflow run 数据
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), index=True)
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class CIJob(Base):
    """CI Job 表（job 级别）"""
    __tablename__ = "ci_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(BigInteger, nullable=False, unique=True)  # GitHub job ID
    run_id = Column(BigInteger, nullable=False, index=True)  # 关联的 workflow run_id
    workflow_name = Column(String(100), nullable=False, index=True)
    job_name = Column(String(500), nullable=False)  # job 名称
    status = Column(String(20), index=True)  # completed, in_progress, queued
    conclusion = Column(String(50))  # success, failure, cancelled, skipped
    started_at = Column(TIMESTAMP, index=True)
    completed_at = Column(TIMESTAMP, index=True)
    duration_seconds = Column(Integer)
    hardware = Column(String(20), index=True)  # A2, A3, 310P
    runner_name = Column(String(200))  # runner 名称
    runner_labels = Column(Text)  # runner 标签（JSON 格式）
    steps_data = Column(Text)  # job steps 详细信息（JSON 格式）
    logs_url = Column(String(500))  # job 日志 URL
    data = Column(Text)  # 完整的 job 数据 (LONGTEXT)
    # 每日失败追踪：人工处理状态
    processing_status = Column(String(20), default="未处理", index=True)  # 未处理/处理中/已修复/已关闭
    notes = Column(Text)  # 处理备注
    updated_by = Column(String(50))  # 最后更新人
    status_updated_at = Column(TIMESTAMP)  # 状态最后更新时间
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), index=True)
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class CINpuJobFact(Base):
    """Normalized, read-only CI NPU job fact keyed by the GitHub job id."""
    __tablename__ = "ci_npu_job_facts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(BigInteger, nullable=False, unique=True)
    run_id = Column(BigInteger, nullable=False, index=True)
    repository = Column(String(255), nullable=False, index=True)
    workflow_name = Column(String(255), nullable=True)
    job_name = Column(String(500), nullable=True)
    pr_number = Column(Integer, nullable=True, index=True)
    run_url = Column(String(500), nullable=True)
    job_url = Column(String(500), nullable=True)
    queued_at = Column(TIMESTAMP, nullable=True, index=True)
    queued_at_source = Column(String(64), nullable=False, default="missing")
    started_at = Column(TIMESTAMP, nullable=True, index=True)
    completed_at = Column(TIMESTAMP, nullable=True, index=True)
    status = Column(String(32), nullable=True, index=True)
    conclusion = Column(String(64), nullable=True)
    runner_labels = Column(JSON, nullable=True)
    rule_version = Column(String(64), nullable=False)
    pool = Column(String(128), nullable=True, index=True)
    accelerator_model = Column(String(64), nullable=True)
    mapped_cards = Column(Integer, nullable=True)
    mapping_status = Column(String(32), nullable=False, default="unmatched")
    data_quality_status = Column(String(64), nullable=False, default="ok")
    source_updated_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (
        Index("ix_ci_npu_facts_repository_queued", "repository", "queued_at"),
        Index("ix_ci_npu_facts_pool_status", "pool", "status"),
    )


class CINpuQueueSnapshot(Base):
    """Durable current-state samples used for the Queue / Running 24h trend."""
    __tablename__ = "ci_npu_queue_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    captured_at = Column(TIMESTAMP, nullable=False)
    waiting_jobs = Column(Integer, nullable=False, default=0)
    waiting_cards = Column(Integer, nullable=False, default=0)
    running_jobs = Column(Integer, nullable=False, default=0)
    running_cards = Column(Float, nullable=False, default=0)

    __table_args__ = (Index("ix_ci_npu_queue_snapshots_captured", "captured_at"),)


class CINpuSyncState(Base):
    """Per-repository durable sync state for CI NPU fact collection."""
    __tablename__ = "ci_npu_sync_state"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    repository = Column(String(255), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="idle")
    successful_watermark = Column(TIMESTAMP, nullable=True)
    last_successful_sync = Column(TIMESTAMP, nullable=True)
    last_job_id = Column(BigInteger, nullable=True)
    active_task_id = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    rule_version = Column(String(64), nullable=True)
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)


class DailyFailureRecord(Base):
    """每日失败记录物化表 — CI sync 后自动填充，JOIN 静态用例表快照"""
    __tablename__ = "daily_failure_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_date = Column(Date, nullable=False, index=True)  # 失败日期（北京时间）
    source_branch = Column(String(100), nullable=False, default="main", index=True)  # 来源分支
    workflow_name = Column(String(100), nullable=False, index=True)
    job_name = Column(String(500), nullable=False, index=True)
    run_id = Column(BigInteger, nullable=False)
    job_id = Column(BigInteger)  # GitHub job_id
    conclusion = Column(String(50))
    started_at = Column(TIMESTAMP)
    completed_at = Column(TIMESTAMP)
    duration_seconds = Column(Integer)
    hardware = Column(String(20))
    # 静态用例快照（物化时从 nightly_test_cases 复制）
    display_name = Column(String(200))
    test_model = Column(String(200))
    model_fo = Column(String(100))
    owner = Column(String(100))
    deployment_type = Column(String(100))
    processing_time = Column(TIMESTAMP)
    closure_time = Column(TIMESTAMP)
    # 人工处理状态
    processing_status = Column(String(20), default="未处理", index=True)
    problem_category = Column(String(50))  # 问题分类
    related_pr = Column(String(20))  # 关联 PR 号
    notes = Column(Text)
    updated_by = Column(String(50))
    status_updated_at = Column(TIMESTAMP)
    # GitHub 链接快照
    github_job_url = Column(String(500))
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('report_date', 'source_branch', 'workflow_name', 'job_name',
                         name='uq_daily_failure_date_branch_wf_job'),
    )


class WorkflowConfig(Base):
    """Workflow 配置表"""
    __tablename__ = "workflow_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_name = Column(String(100), nullable=False, unique=True)  # 显示名称，如 "Nightly-A2"
    workflow_file = Column(String(100), nullable=False, unique=True)  # workflow 文件名，如 "schedule_nightly_test_a2.yaml"
    hardware = Column(String(20), nullable=False)  # 硬件类型：A2, A3, 310P 等
    event = Column(String(50), default="schedule")  # 采集的事件类型：schedule/push/pull_request/workflow_dispatch，空=不过滤
    actor = Column(String(100), nullable=True)  # 触发人过滤（GitHub actor login），空=不过滤
    description = Column(String(500))  # 描述信息
    enabled = Column(Boolean, default=True)  # 是否启用
    display_order = Column(Integer, default=0)  # 显示顺序
    stats_start_hour = Column(Integer, nullable=True)  # 统计及失败物化时间窗口起始小时（0-23）
    stats_end_hour = Column(Integer, nullable=True)  # 统计及失败物化时间窗口结束小时（0-23）
    materialize_name_regex = Column(String(500), nullable=True)  # 采集前匹配 GitHub 原始 Workflow 名称
    auto_failure_analysis_enabled = Column(Boolean, nullable=False, default=True)  # 是否自动分析该 Workflow 的失败 Job

    # 新增字段：同步状态跟踪（用于前端显示）
    last_sync_at = Column(TIMESTAMP)  # 上次同步时间（包括手动和自动）

    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class PerformanceData(Base):
    """性能数据表"""
    __tablename__ = "performance_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    test_name = Column(String(200), nullable=False, index=True)
    hardware = Column(String(20), nullable=False, index=True)  # A2, A3
    model_name = Column(String(200), nullable=False, index=True)
    vllm_version = Column(String(50), index=True)
    vllm_commit = Column(String(40))
    vllm_ascend_commit = Column(String(40))
    test_type = Column(String(20))  # latency, throughput, serving
    metrics_json = Column(Text, nullable=False)
    timestamp = Column(TIMESTAMP, nullable=False, index=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class JobOwner(Base):
    """Job 责任人配置表"""
    __tablename__ = "job_owners"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_name = Column(String(100), nullable=False, index=True)  # workflow 名称
    job_name = Column(String(500), nullable=False, index=True)  # job 名称
    display_name = Column(String(200))  # Job 显示名（可选）
    owner = Column(String(100), nullable=False)  # 责任人姓名
    email = Column(String(100))  # 责任人邮箱（可选）
    notes = Column(String(500))  # 备注信息（可选）
    is_hidden = Column(Boolean, default=False, index=True)  # 是否隐藏
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    # 唯一约束：workflow_name + job_name 组合唯一
    __table_args__ = (
        UniqueConstraint('workflow_name', 'job_name', name='uq_job_owner_workflow_job'),
    )


class NightlyTestCase(Base):
    """Nightly 时序快照表 — 每天从各分支的 nightly_config.yaml 快照一份"""
    __tablename__ = "nightly_test_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_date = Column(Date, nullable=False, index=True)  # 快照日期（北京时间）
    source_branch = Column(String(100), nullable=False, default="main", index=True)  # 来源分支
    workflow_name = Column(String(100), nullable=False, index=True)  # Nightly-A2 / Nightly-A3
    job_name = Column(String(500), nullable=False, index=True)  # job 名称
    display_name = Column(String(200))  # 显示名
    test_model = Column(String(200))  # 测试的模型名称
    model_fo = Column(String(100))  # 模型 FO
    owner = Column(String(100))  # 测试负责人
    deployment_type = Column(String(100))  # 部署方式
    notes = Column(Text)  # 备注
    enabled = Column(Boolean, default=True, index=True)  # 是否启用
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('report_date', 'source_branch', 'workflow_name', 'job_name',
                         name='uq_nightly_test_case_date_branch_wf_job'),
    )


class ModelFoMapping(Base):
    """Current model-to-FO mapping used by future Nightly snapshots."""
    __tablename__ = "model_fo_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_key = Column(String(255), nullable=False, unique=True, index=True)
    model_fo = Column(String(100), nullable=False)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class ModelSyncConfig(Base):
    """模型报告同步配置表"""
    __tablename__ = "model_sync_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_name = Column(String(100), nullable=False, index=True)  # workflow 显示名称
    workflow_file = Column(String(100), nullable=False, unique=True)  # workflow 文件名
    artifacts_pattern = Column(String(200))  # artifacts 名称匹配规则（如 "model-report-*"）
    file_patterns = Column(Text)  # JSON 数组，需要下载的文件路径模式（如 ["results/*.yaml", "lm_eval_results/*.json"]）
    branch = Column(String(100), default="main")  # 分支名称过滤（如 "main", "zxy_fix_ci"）
    enabled = Column(Boolean, default=True)  # 是否启用
    last_sync_at = Column(TIMESTAMP)  # 上次同步时间
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class ProjectDashboardConfig(Base):
    """项目看板配置表"""
    __tablename__ = "project_dashboard_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(100), unique=True, nullable=False)  # 配置键
    config_value = Column(JSON, nullable=False)  # 配置值（JSON 格式）
    description = Column(String(500))  # 配置描述
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class KubernetesClusterConfig(Base):
    """Kubernetes 集群配置表"""
    __tablename__ = "kubernetes_cluster_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(String(500))
    kubeconfig_encrypted = Column(Text, nullable=False)
    context = Column(String(200))
    default_label_selector = Column(String(500))
    namespaces = Column(String(500), nullable=False, default="vllm-project")
    npu_resource_name = Column(String(200), nullable=False, default="huawei.com/Ascend910")
    enabled = Column(Boolean, default=True, index=True)
    display_order = Column(Integer, default=0)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class DailyReportHistory(Base):
    """每日运行报告发送历史表"""
    __tablename__ = "daily_report_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_date = Column(String(10), nullable=False, index=True)
    recipients = Column(Text, nullable=False)
    subject = Column(String(200), nullable=False)
    status = Column(String(20), default="pending", index=True)
    sent_at = Column(TIMESTAMP)
    error_message = Column(Text)
    ci_summary = Column(JSON)
    model_summary = Column(JSON)
    github_summary = Column(JSON)
    performance_summary = Column(JSON)
    ai_report_content = Column(Text)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class ResourceNpuMetrics(Base):
    """资源 NPU 指标采集表"""
    __tablename__ = "resource_npu_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_cluster_configs.id"), nullable=False, index=True)
    cluster_name = Column(String(100), nullable=False)
    npu_total = Column(Float, default=0)
    npu_used = Column(Float, default=0)
    npu_available = Column(Float, default=0)
    npu_utilization = Column(Float, default=0)
    executing_pods_count = Column(Integer, default=0)
    pr_count = Column(Integer, default=0)
    top_pods_json = Column(JSON)
    # Lifecycle snapshots are kept separately from ``top_pods_json`` so the
    # existing current-resource and PR-demand readers remain active-only.
    pod_timeline_json = Column(JSON)
    collected_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), index=True)

    __table_args__ = (
        Index("ix_resource_npu_metrics_cluster_collected", "cluster_id", "collected_at"),
    )


class ResourceNodeMetrics(Base):
    """资源节点指标采集表"""
    __tablename__ = "resource_node_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_cluster_configs.id"), nullable=False, index=True)
    cluster_name = Column(String(100), nullable=False)
    node_name = Column(String(250), nullable=False, index=True)
    cpu_cores_total = Column(Float, default=0)
    cpu_cores_used = Column(Float, default=0)
    cpu_cores_available = Column(Float, default=0)
    cpu_utilization = Column(Float, default=0)
    memory_bytes_total = Column(Float, default=0)
    memory_bytes_used = Column(Float, default=0)
    memory_bytes_available = Column(Float, default=0)
    memory_utilization = Column(Float, default=0)
    npu_total = Column(Float, default=0)
    npu_used = Column(Float, default=0)
    npu_available = Column(Float, default=0)
    npu_utilization = Column(Float, default=0)
    executing_pods_count = Column(Integer, default=0)
    collected_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), index=True)

    __table_args__ = (
        Index("ix_resource_node_metrics_cluster_collected", "cluster_id", "collected_at"),
    )


class AlertRule(Base):
    """告警规则表（规则头）"""
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    cluster_id = Column(Integer, ForeignKey("kubernetes_cluster_configs.id"), nullable=True)
    node_name = Column(String(250), nullable=True)
    enabled = Column(Boolean, default=True, index=True)
    notify_email = Column(Boolean, default=True)
    notification_email = Column(String(100), nullable=True)
    last_triggered_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class JobFailureAnalysis(Base):
    """CI Job 失败分析表"""
    __tablename__ = "job_failure_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(BigInteger, nullable=False, unique=True)
    run_id = Column(BigInteger, nullable=False, index=True)
    workflow_name = Column(String(100), nullable=False, index=True)
    job_name = Column(String(500), nullable=False)
    failure_date = Column(TIMESTAMP, nullable=False, index=True)

    failure_fingerprint = Column(String(32), index=True)
    reused_analysis_id = Column(Integer)

    problem_category = Column(String(20), index=True)
    root_cause_summary = Column(String(500))
    improvement_measures_summary = Column(String(500))
    report_file_path = Column(String(200))
    pdf_file_path = Column(String(200))

    llm_provider = Column(String(50))
    llm_model = Column(String(100))
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    generation_time_seconds = Column(Float)

    analysis_status = Column(String(20), default="pending", index=True)
    analysis_phase = Column(String(30), default="queued")
    evidence_ledger = Column(JSON)
    validation_result = Column(JSON)
    agent_trace = Column(JSON)
    agent_steps = Column(Integer, default=0)
    error_message = Column(String(500))
    triggered_by = Column(String(20), default="manual")  # "manual" | "scheduler"
    share_token = Column(String(64), unique=True)  # 公开分享 token
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class JobLogSummary(Base):
    """一次性生成并永久复用的 Job 日志根因摘要。"""
    __tablename__ = "job_log_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(BigInteger, nullable=False, unique=True, index=True)
    run_id = Column(BigInteger, nullable=False, index=True)
    summary = Column(Text)
    log_excerpt = Column(Text)
    status = Column(String(20), nullable=False, default="pending", index=True)
    llm_provider = Column(String(50))
    llm_model = Column(String(100))
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    generation_time_seconds = Column(Float)
    error_message = Column(String(500))
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class AlertConditionGroup(Base):
    """告警条件组表（组间 AND）"""
    __tablename__ = "alert_condition_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id"), nullable=False, index=True)
    logic = Column(String(10), nullable=False, default="AND")
    display_order = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class AlertCondition(Base):
    """告警条件表"""
    __tablename__ = "alert_conditions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("alert_condition_groups.id"), nullable=False, index=True)
    metric_field = Column(String(50), nullable=False)
    operator = Column(String(10), nullable=False)
    threshold = Column(Float, nullable=False)
    is_exclude = Column(Boolean, default=False)
    display_order = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class AlertHistory(Base):
    """告警触发历史表"""
    __tablename__ = "alert_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id"), nullable=False, index=True)
    rule_name = Column(String(100), nullable=False)
    actual_value = Column(Float, nullable=False)
    cluster_id = Column(Integer, nullable=True)
    cluster_name = Column(String(100), nullable=True)
    node_name = Column(String(250), nullable=True)
    condition_details = Column(JSON, nullable=True)
    triggered_at = Column(TIMESTAMP, nullable=False, default=lambda: datetime.now(UTC), index=True)
    notification_sent = Column(Boolean, default=False)
    notification_error = Column(Text, nullable=True)


class UserLoginLog(Base):
    __tablename__ = "user_login_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    login_time = Column(TIMESTAMP, nullable=False, default=lambda: datetime.now(UTC), index=True)
    ip_address = Column(String(64))
    ip_address_hashed = Column(String(64))
    user_agent = Column(String(500))
    login_method = Column(String(20), default="password")
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))

    user = relationship("User", back_populates="login_logs")


class FeatureUsageLog(Base):
    __tablename__ = "feature_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    feature_name = Column(String(100), nullable=False, index=True)
    request_path = Column(String(500), nullable=False, index=True)
    access_time = Column(TIMESTAMP, nullable=False, default=lambda: datetime.now(UTC), index=True)
    metadata_json = Column(JSON)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))

    user = relationship("User", back_populates="usage_logs")


class IssueDiagnosisHistory(Base):
    __tablename__ = "issue_diagnosis_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    diagnosis_type = Column(String(20), nullable=False)  # pr_pipeline, ci_job, manual
    target_id = Column(String(100), nullable=False)  # PR number or job ID
    target_label = Column(String(200))  # PR title or job name
    report_content = Column(Text)
    model_used = Column(String(100))
    duration_seconds = Column(Float, default=0)
    status = Column(String(20), default="success")
    is_liked = Column(Boolean, default=False)
    like_count = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))

    user = relationship("User", backref="diagnosis_histories")


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_jti = Column(String(100), unique=True, nullable=False)
    blacklisted_at = Column(TIMESTAMP, nullable=False, default=lambda: datetime.now(UTC), index=True)
    expires_at = Column(TIMESTAMP, nullable=False, index=True)


# 导入每日总结相关模型
from .daily_summary import DailyCommit, DailyIssue, DailyPR, DailySummary, LLMProviderConfig
from .memory import AnalysisEmbedding, AnalysisMemory
from .test_board import FailureAnnotation, TestCase, TestRun, TestSuiteSnapshot


class PullRequest(Base):
    """PR 全生命周期数据"""
    __tablename__ = "pull_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pr_number = Column(BigInteger, nullable=False, index=True)
    owner = Column(String(100), nullable=False, index=True)
    repo = Column(String(100), nullable=False, index=True)

    title = Column(String(500), nullable=False)
    author = Column(String(100), nullable=False, index=True)
    author_avatar_url = Column(String(500))
    author_avatar_base64 = Column(Text)  # Cached avatar as data:image/png;base64,...
    author_email = Column(String(200))
    html_url = Column(String(500))
    state = Column(String(20), nullable=False, index=True)
    is_draft = Column(Boolean, default=False, index=True)
    labels = Column(JSON, default=list)

    head_branch = Column(String(200))
    head_sha = Column(String(40), index=True)
    base_branch = Column(String(200))

    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)
    changed_files = Column(Integer, default=0)

    pipeline_stage = Column(String(20), index=True)
    review_status = Column(String(20), index=True)
    reviewers = Column(JSON, default=list)
    ci_status = Column(String(20), index=True)
    ci_workflow_run_id = Column(BigInteger)

    first_review_at = Column(TIMESTAMP)
    first_approved_at = Column(TIMESTAMP)
    ci_started_at = Column(TIMESTAMP)
    ci_completed_at = Column(TIMESTAMP)
    merged_at = Column(TIMESTAMP)
    closed_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, nullable=False, index=True, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    data = Column(JSON)

    __table_args__ = (
        UniqueConstraint("pr_number", "owner", "repo", name="uq_pr_owner_repo"),
    )


class AppLog(Base):
    """应用日志（DB 日志 handler 持久化目标表）"""
    __tablename__ = "app_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(String(30))
    level = Column(String(10), index=True)
    module = Column(String(200))
    function_name = Column(String(200))
    line_number = Column(Integer)
    message = Column(Text)
    traceback = Column(Text)


class ModelRegistry(Base):
    """统一模型注册表 — 合并 ModelConfig + 支持矩阵条目"""
    __tablename__ = "model_registry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(255), nullable=False, index=True)
    role = Column(String(20), nullable=False, default="generative")
    display_name = Column(String(255))
    series = Column(String(50), index=True)
    model_type = Column(String(50), nullable=False, default="text_generative")
    tier = Column(String(20))
    support_status = Column(String(30), nullable=False, default="untested")
    weight_formats = Column(JSON)
    kv_cache_types = Column(JSON)
    supported_hardware = Column(JSON)
    max_model_len = Column(String(50))
    note = Column(Text)
    upstream_issue = Column(String(255))
    official_doc_url = Column(String(500))
    config_yaml = Column(Text)
    startup_commands = Column(JSON)
    key_metrics_config = Column(JSON)
    pass_threshold = Column(JSON)
    first_supported_version = Column(String(50))
    manual_overrides = Column(JSON)
    status = Column(String(20), default="active")
    created_by = Column(Integer, ForeignKey("users.id"))
    source = Column(String(20), default="manual")
    upstream_synced_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    feature_matrix = relationship("ModelFeatureMatrix", back_populates="registry", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("model_name", "role", name="uq_model_name_role"),
    )


class ModelFeatureMatrix(Base):
    """模型特性矩阵 — 替代原 JSON toggle 字段"""
    __tablename__ = "model_feature_matrix"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("model_registry.id"), nullable=False, index=True)
    feature_key = Column(String(50), nullable=False)
    feature_status = Column(String(20), nullable=False)
    hardware_scope = Column(JSON)
    note = Column(Text)
    verified_by_report = Column(Boolean, default=False)
    verified_report_id = Column(Integer)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    registry = relationship("ModelRegistry", back_populates="feature_matrix")

    __table_args__ = (
        UniqueConstraint("model_id", "feature_key", name="uq_model_feature"),
    )


class FeatureCompatibility(Base):
    """特性互操作矩阵 — 来自 feature_matrix.md"""
    __tablename__ = "feature_compatibility"

    id = Column(Integer, primary_key=True, autoincrement=True)
    feature_a = Column(String(100), nullable=False)
    feature_b = Column(String(100), nullable=False)
    compatibility = Column(String(20), nullable=False)
    footnote = Column(Text)
    synced_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("feature_a", "feature_b", name="uq_feature_pair"),
    )


class CodeMetricsSnapshot(Base):
    """代码度量快照 — 每日采集的主表"""
    __tablename__ = "code_metrics_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo = Column(String(200), nullable=False, default="vllm-ascend")
    branch = Column(String(100), nullable=False, default="main")
    tag = Column(String(100))  # git tag for version comparison (e.g., v0.18.0)
    snapshot_date = Column(Date, nullable=False)
    collection_status = Column(String(20), default="complete")  # complete/partial/failed
    collection_duration_seconds = Column(Integer, default=0)

    # 代码规模
    total_loc = Column(Integer, default=0)
    total_raw_lines = Column(Integer, default=0)
    loc_python = Column(Integer, default=0)
    loc_cpp = Column(Integer, default=0)
    loc_c = Column(Integer, default=0)
    loc_cmake = Column(Integer, default=0)
    loc_shell = Column(Integer, default=0)
    total_functions = Column(Integer, default=0)
    total_files = Column(Integer, default=0)

    # 圈复杂度
    cc_total = Column(Integer, default=0)
    cc_per_method = Column(Float, default=0)
    cc_maximum = Column(Integer, default=0)
    cc_huge_count = Column(Integer, default=0)
    cc_huge_ratio = Column(Float, default=0)
    cc_adequacy = Column(Float, default=0)

    # 嵌套深度
    max_depth = Column(Integer, default=0)
    depth_huge_count = Column(Integer, default=0)
    depth_huge_ratio = Column(Float, default=0)

    # 函数体量
    method_lines_total = Column(Integer, default=0)
    lines_per_method = Column(Float, default=0)
    huge_method_count = Column(Integer, default=0)
    huge_method_ratio = Column(Float, default=0)
    huge_file_count = Column(Integer, default=0)
    huge_headerfile_count = Column(Integer, default=0)

    # 重复率
    dup_blocks = Column(Integer, default=0)
    dup_lines = Column(Integer, default=0)
    dup_ratio = Column(Float, default=0)

    # 安全规范
    unsafe_functions_count = Column(Integer, default=0)
    warning_suppression_count = Column(Integer, default=0)
    lint_errors = Column(Integer, default=0)
    lint_warnings = Column(Integer, default=0)

    # 技术债务
    todo_count = Column(Integer, default=0)
    fixme_count = Column(Integer, default=0)
    hack_count = Column(Integer, default=0)

    # 健康度评分
    health_score = Column(Float, default=0)
    health_score_complexity = Column(Float, default=0)
    health_score_security = Column(Float, default=0)
    health_score_duplication = Column(Float, default=0)
    health_score_method_size = Column(Float, default=0)
    health_score_tech_debt = Column(Float, default=0)
    health_score_lint = Column(Float, default=0)

    # 模块级数据 (JSON)
    module_loc = Column(JSON)  # {"vllm_ascend": 12345, "csrc": 67890, ...}
    language_loc = Column(JSON)  # {"Python": 12345, "C++": 67890, ...}

    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("snapshot_date", "repo", "branch", name="uq_snapshot_date_repo_branch"),
    )


class CodeComplexityDetail(Base):
    """圈复杂度明细 — 超大复杂度函数列表"""
    __tablename__ = "code_metrics_complexity_details"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(Integer, ForeignKey("code_metrics_snapshots.id"), nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    function_name = Column(String(200), nullable=False)
    language = Column(String(20))  # Python/C++
    cyclomatic_complexity = Column(Integer)
    max_nesting_depth = Column(Integer)
    function_lines = Column(Integer)
    start_line = Column(Integer)

    snapshot = relationship("CodeMetricsSnapshot", backref="complexity_details")


class CodeDuplicationDetail(Base):
    """重复代码块明细"""
    __tablename__ = "code_metrics_duplication_details"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(Integer, ForeignKey("code_metrics_snapshots.id"), nullable=False, index=True)
    file_a = Column(String(500), nullable=False)
    file_b = Column(String(500), nullable=False)
    lines = Column(Integer, default=0)
    token_count = Column(Integer, default=0)
    fragment = Column(Text)

    snapshot = relationship("CodeMetricsSnapshot", backref="duplication_details")


class CodeSecurityDetail(Base):
    """安全规范明细"""
    __tablename__ = "code_metrics_security_details"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(Integer, ForeignKey("code_metrics_snapshots.id"), nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    line_number = Column(Integer)
    severity = Column(String(20))  # error/warning/info
    tool = Column(String(50))  # cppcheck/clang-tidy/ruff/mypy
    rule_id = Column(String(100))
    message = Column(Text)

    snapshot = relationship("CodeMetricsSnapshot", backref="security_details")


class CodeMetricsFileHeatmap(Base):
    """文件变更热力图"""
    __tablename__ = "code_metrics_file_heatmap"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo = Column(String(200), nullable=False, default="vllm-ascend")
    file_path = Column(String(500), nullable=False)
    change_count = Column(Integer, default=0)
    bug_fix_count = Column(Integer, default=0)
    last_changed = Column(TIMESTAMP)
    last_commit_sha = Column(String(40))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("repo", "file_path", name="uq_heatmap_repo_file"),
    )


class SchedulerHeartbeat(Base):
    """调度器心跳表 — 独立 scheduler 进程定期写入，API 进程读取以判断调度器是否在运行。

    Phase A 将 scheduler 拆为独立容器后，API 进程内的 APScheduler 实例为空，
    `/system/config/status` 无法再通过进程内状态判断调度器存活。
    scheduler 进程每 20s 写入一行（单例 id=1），API 进程根据 updated_at 新鲜度判断。
    """
    __tablename__ = "scheduler_heartbeat"

    id = Column(Integer, primary_key=True, autoincrement=False)  # 固定=1，单例
    running = Column(Boolean, default=False)  # APScheduler.running 快照
    jobs = Column(JSON)  # {job_id: {"name": str, "next_run": iso_str|null}, ...}
    pid = Column(Integer)  # 写入进程 PID，便于调试
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class NpuOccupancyRawEnv(Base):
    __tablename__ = "npu_occupancy_raw_envs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    env_id = Column(String(255), nullable=False)
    source_updated_at = Column(TIMESTAMP, nullable=False)
    pod_name = Column(String(255), nullable=True)
    pool = Column(String(255), nullable=True)
    effective_start_at = Column(TIMESTAMP, nullable=True)
    effective_end_at = Column(TIMESTAMP, nullable=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (
        UniqueConstraint("env_id", name="uq_npu_occupancy_env_id"),
        Index("ix_npu_occupancy_pod_name", "pod_name"),
        Index("ix_npu_occupancy_pool_end_start", "pool", "effective_end_at", "effective_start_at"),
        Index("ix_npu_occupancy_effective_end", "effective_end_at"),
    )


class NpuOccupancySyncState(Base):
    __tablename__ = "npu_occupancy_sync_state"

    id = Column(Integer, primary_key=True, autoincrement=False)
    status = Column(String(20), nullable=False, default="idle")
    last_successful_sync = Column(TIMESTAMP, nullable=True)
    last_snapshot_at = Column(TIMESTAMP, nullable=True)
    active_task_id = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)


class Contributor(Base):
    """贡献者主表"""
    __tablename__ = "contributors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    github_id = Column(BigInteger, nullable=False, unique=True, index=True)
    login = Column(String(100), nullable=False, unique=True, index=True)
    avatar_url = Column(String(500))
    email = Column(String(200))
    name = Column(String(200))
    is_community = Column(Boolean, default=True, index=True)
    role_types = Column(JSON)
    company = Column(String(200))
    employee_id = Column(String(100), nullable=True, index=True)
    first_contribution_at = Column(TIMESTAMP)
    last_contribution_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class ContributorMetrics(Base):
    """贡献者聚合指标表"""
    __tablename__ = "contributor_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contributor_id = Column(Integer, ForeignKey("contributors.id"), nullable=False, index=True)
    contributor_login = Column(String(100), nullable=False, index=True)
    repo_name = Column(String(200), nullable=True, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    merged_pr_count = Column(Integer, default=0)
    pending_pr_count = Column(Integer, default=0)
    code_additions = Column(Integer, default=0)
    code_deletions = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    approval_count = Column(Integer, default=0)
    merge_count = Column(Integer, default=0)
    core_module_count = Column(Integer, default=0)
    closed_pr_count = Column(Integer, default=0)
    commit_count = Column(Integer, default=0)
    contribution_score = Column(Float, default=0)
    error_score = Column(Float, default=0)
    quadrant = Column(String(20))
    ci_failure_count = Column(Integer, default=0)
    first_review_hours_avg = Column(Float, nullable=True)
    merge_hours_avg = Column(Float, nullable=True)
    ci_duration_hours_avg = Column(Float, nullable=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('contributor_login', 'period_start', 'period_end', name='uq_contributor_metrics_period'),
    )

    def to_weight_dict(self) -> dict:
        """提取自动算权所需的9个维度数据"""
        return {
            "merged_pr_count": self.merged_pr_count or 0,
            "code_additions": self.code_additions or 0,
            "review_count": self.review_count or 0,
            "approval_count": self.approval_count or 0,
            "core_module_count": self.core_module_count or 0,
            "files_changed_count": self.files_changed_count if hasattr(self, "files_changed_count") else 0,
            "commit_count": self.commit_count or 0,
        }


class ContributorBoardSettings(Base):
    """贡献者看板设置表"""
    __tablename__ = "contributor_board_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_key = Column(String(100), nullable=False, unique=True, index=True)
    setting_value = Column(JSON, nullable=False)
    description = Column(String(500), nullable=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class CommitClassification(Base):
    """提交分类记录表"""
    __tablename__ = "commit_classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    commit_sha = Column(String(40), nullable=False, unique=True, index=True)
    commit_message = Column(Text)
    commit_type = Column(String(20), nullable=False, index=True)
    confidence = Column(Float, default=1.0)
    modules_touched = Column(JSON)
    files_changed_count = Column(Integer, default=0)
    is_chip_specific = Column(Boolean, default=False)
    chip_target = Column(String(50))
    contributor_login = Column(String(100), index=True)
    pr_number = Column(Integer, index=True)
    repo_name = Column(String(200), nullable=True, index=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class ErrorRecord(Base):
    """出错记录表"""
    __tablename__ = "error_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contributor_login = Column(String(100), nullable=False, index=True)
    error_type = Column(String(50), nullable=False, index=True)
    related_pr_number = Column(Integer, index=True)
    related_commit_sha = Column(String(40))
    revert_count = Column(Integer, default=0)
    bugfix_linked_count = Column(Integer, default=0)
    ci_failure_count = Column(Integer, default=0)
    nightly_failure_count = Column(Integer, default=0)
    weight_score = Column(Float, default=0)
    description = Column(Text)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class ImageBuildPlan(Base):
    """镜像构建计划记录表"""
    __tablename__ = "image_build_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pr_number = Column(Integer, nullable=False, index=True)
    commit_sha = Column(String(40), nullable=False)
    commit_type = Column(String(20))
    build_type = Column(String(50), nullable=False)
    chips = Column(JSON)
    os_list = Column(JSON)
    cache_strategy = Column(String(50))
    estimated_saving_percent = Column(Float, default=0)
    actual_execution = Column(Boolean, default=False)
    reasoning = Column(String(500))
    files_changed = Column(JSON)
    repo_name = Column(String(200), nullable=True, index=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class QuadrantSnapshot(Base):
    """四象限历史快照表"""
    __tablename__ = "quadrant_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contributor_id = Column(Integer, ForeignKey("contributors.id"), nullable=False, index=True)
    contributor_login = Column(String(100), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    quadrant = Column(String(20), nullable=False)
    contribution_score = Column(Float, default=0)
    error_score = Column(Float, default=0)
    meta = Column(JSON)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('contributor_login', 'snapshot_date', name='uq_quadrant_snapshot_date'),
    )


class ContributorSyncRecord(Base):
    """收集器同步记录表"""
    __tablename__ = "contributor_sync_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_name = Column(String(200), nullable=True, index=True)
    sync_type = Column(String(20), nullable=False, default="full")
    status = Column(String(20), nullable=False, index=True)
    since_date = Column(Date)
    until_date = Column(Date, nullable=True)
    contributors_synced = Column(Integer, default=0)
    commits_classified = Column(Integer, default=0)
    total_prs_scanned = Column(Integer, default=0)
    build_plans_created = Column(Integer, default=0)
    errors = Column(JSON)
    duration_seconds = Column(Float, default=0)
    synced_at = Column(TIMESTAMP)
    progress_percent = Column(Float, default=0)
    progress_message = Column(String(500))
    trigger_source = Column(String(20), nullable=False, default="manual")
    started_at = Column(TIMESTAMP, nullable=True)
    completed_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))


class ContributorScoringVersion(Base):
    """评分权重版本记录表"""
    __tablename__ = "contributor_scoring_version"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(30), nullable=False, comment="版本号: v1.0.0, v1.1.0...")
    repo_name = Column(String(200), nullable=True, index=True)

    weights = Column(JSON, nullable=False, comment="权重配置快照")

    method = Column(Enum("manual", "auto", "ai", name="scoring_method_enum"), nullable=False, comment="权重确定方式")
    status = Column(Enum("draft", "active", "archived", name="scoring_version_status"), nullable=False, default="draft", comment="版本状态")

    data_summary = Column(JSON, nullable=True, comment="算权时数据分布摘要")

    ai_prompt = Column(Text, nullable=True)
    ai_response = Column(Text, nullable=True)
    ai_model = Column(String(100), nullable=True)

    confidence_score = Column(Float, nullable=True)
    description = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    created_by = Column(String(100), nullable=True)
    activated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), nullable=True)
    activated_by = Column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_scoring_version_repo_status", "repo_name", "status"),
    )


class ContributorScoreSnapshot(Base):
    """分数快照表"""
    __tablename__ = "contributor_score_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version_id = Column(Integer, ForeignKey("contributor_scoring_version.id", ondelete="CASCADE"), nullable=False, index=True)
    repo_name = Column(String(200), nullable=True, index=True)

    snapshot_date = Column(Date, nullable=False, index=True)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    contributor_count = Column(Integer, nullable=False, default=0)

    statistics = Column(JSON, nullable=True)
    scores = Column(JSON, nullable=False)

    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    created_by = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)


class ContributorScoreUpdateAudit(Base):
    """评分更新审计日志表"""
    __tablename__ = "contributor_score_update_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(50), nullable=False, comment="操作类型")
    version_id = Column(Integer, nullable=True)
    snapshot_id = Column(Integer, nullable=True)
    repo_name = Column(String(200), nullable=True, index=True)

    details = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)

    weight_diff = Column(JSON, nullable=True)
    max_weight_shift = Column(Float, nullable=True, comment="最大单项权重变化幅度(%)")

    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    created_by = Column(String(100), nullable=True)


class ContributorAIReport(Base):
    """AI 综合分析报告表"""
    __tablename__ = "contributor_ai_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_name = Column(String(300), nullable=False)
    report_type = Column(String(50), nullable=True, comment="quarterly/monthly/custom/adhoc")
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    generated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    generated_by = Column(String(100), nullable=True)
    generation_source = Column(String(20), nullable=True, comment="manual/scheduled/ai_auto")
    weight_version_id = Column(Integer, ForeignKey("contributor_scoring_version.id"), nullable=True)
    llm_model = Column(String(100), nullable=True)
    executive_summary = Column(Text, nullable=True)
    quadrant_analysis = Column(JSON, nullable=True)
    individual_evaluations = Column(JSON, nullable=True)
    recommendations = Column(JSON, nullable=True)
    confidence_overall = Column(Float, nullable=True)
    report_content = Column(Text, nullable=True)
    report_file_url = Column(String(500), nullable=True)
    schedule_cron = Column(String(100), nullable=True)
    enabled = Column(Boolean, default=True)
    repo_name = Column(String(200), nullable=True, index=True)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("idx_ai_report_period", "period_start", "period_end"),
        Index("idx_ai_report_created", "generated_at"),
    )
