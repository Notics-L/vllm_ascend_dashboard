"""
发布件管理 - 数据库模型
"""
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from infrastructure.persistence.models import Base


class ReleaseWorkflow(Base):
    """发布件工作流配置表"""
    __tablename__ = "release_workflows"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    repo_owner = Column(String(100), nullable=False, index=True)
    repo_name = Column(String(100), nullable=False)
    workflow_id = Column(BigInteger, nullable=False)
    workflow_name = Column(String(255), nullable=False)
    workflow_path = Column(String(255), nullable=False)
    workflow_type = Column(String(50), default="image_build")
    enabled = Column(Boolean, default=True)
    last_sync_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("repo_owner", "repo_name", "workflow_path", name="uq_release_workflow"),
    )


class ReleaseBuildRun(Base):
    """构建运行记录表"""
    __tablename__ = "release_build_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id = Column(BigInteger, ForeignKey("release_workflows.id"), nullable=False, index=True)
    github_run_id = Column(BigInteger, nullable=False)
    status = Column(String(50), nullable=False, index=True)
    event = Column(String(50))
    branch = Column(String(255))
    commit_sha = Column(String(40))
    created_at_remote = Column(DateTime, index=True)
    started_at_remote = Column(DateTime)
    completed_at_remote = Column(DateTime)
    wait_duration = Column(Float, default=0)
    exec_duration = Column(Float, default=0)
    wall_duration = Column(Float, default=0)
    num_jobs = Column(Integer, default=0)
    conclusion_summary = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("workflow_id", "github_run_id", name="uq_release_build_github_run"),
        Index("ix_release_build_workflow_status", "workflow_id", "status"),
    )


class ReleaseBuildGroup(Base):
    """Job 层级数据表"""
    __tablename__ = "release_build_groups"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(BigInteger, ForeignKey("release_build_runs.id"), nullable=False, index=True)
    group_name = Column(String(255), nullable=False)
    target = Column(String(500))
    os_name = Column(String(500))
    phase = Column(String(50))
    status = Column(String(50))
    total_duration = Column(Float, default=0)
    runner_name = Column(String(255))
    architecture = Column(String(20))
    sub_job_name = Column(String(255))
    steps_json = Column(Text)

    __table_args__ = (
        Index("ix_release_build_group_target_phase", "target", "phase"),
    )


class ReleaseThirdPartyVersion(Base):
    """三方件版本记录表"""
    __tablename__ = "release_third_party_versions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(BigInteger, ForeignKey("release_build_runs.id"), nullable=False, index=True)
    component_name = Column(String(255), nullable=False, index=True)
    component_version = Column(String(100), nullable=False)
    component_category = Column(String(50))
    source_type = Column(String(50))
    source_line = Column(Text)
    repository_url = Column(String(500))
    detected_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_release_tp_component_version", "component_name", "component_version"),
    )


class ReleaseThirdPartyBaseline(Base):
    """第三方组件基线表"""
    __tablename__ = "release_third_party_baselines"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    component_name = Column(String(255), nullable=False, unique=True)
    component_category = Column(String(50))
    approved_versions = Column(Text)
    min_version = Column(String(100))
    max_version = Column(String(100))
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class ReleaseAIEvaluation(Base):
    """AI 评估报告表"""
    __tablename__ = "release_ai_evaluations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id = Column(BigInteger, ForeignKey("release_workflows.id"), nullable=False, index=True)
    evaluation_type = Column(String(50))
    period_start = Column(Date)
    period_end = Column(Date)
    run_count = Column(Integer, default=0)
    trust_score = Column(Float)
    trust_level = Column(String(20))
    production_ready = Column(Boolean, default=False)
    llm_model = Column(String(100))
    executive_summary = Column(Text)
    findings = Column(Text)
    recommendations = Column(Text)
    risk_factors = Column(Text)
    data_quality_issues = Column(Text)
    report_content = Column(Text)
    generated_by = Column(String(255))
    generated_at = Column(DateTime, default=lambda: datetime.now(UTC), index=True)


class ReleaseAnalysisTask(Base):
    """分析任务调度表"""
    __tablename__ = "release_analysis_tasks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id = Column(BigInteger, ForeignKey("release_workflows.id"), nullable=False, index=True)
    task_type = Column(String(50))
    trigger_source = Column(String(50), default="manual")
    status = Column(String(50), default="pending", index=True)
    progress = Column(Integer, default=0)
    progress_message = Column(String(500))
    total_items = Column(Integer, default=0)
    processed_items = Column(Integer, default=0)
    collected_items = Column(Integer, default=0)
    days_back = Column(Integer, default=30)
    max_runs = Column(Integer, default=100)
    duration_seconds = Column(Float, default=0)
    error_message = Column(Text)
    errors_json = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_release_task_workflow_status", "workflow_id", "status"),
    )


class ReleaseSyncSchedule(Base):
    """发布件定时同步配置表（每工作流一条）"""
    __tablename__ = "release_sync_schedules"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id = Column(BigInteger, ForeignKey("release_workflows.id"), nullable=False, unique=True, index=True)
    sync_mode = Column(String(20), default="incremental")  # full / incremental / recent_days
    recent_days = Column(Integer, default=30)
    cron_expression = Column(String(50), default="0 2 * * *")
    is_active = Column(Boolean, default=False)
    last_triggered_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
