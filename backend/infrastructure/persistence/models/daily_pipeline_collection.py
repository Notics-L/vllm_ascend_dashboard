"""Persist each Daily execution and its workflow/job tree."""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql

from . import Base


class DailyPipelineCollectionRun(Base):
    """The root Daily workflow run; associated workflows live in nodes."""

    __tablename__ = "daily_pipeline_collection_runs"
    __table_args__ = (
        Index("ix_daily_collection_run_time", "started_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    run_id = Column(BigInteger, primary_key=True, autoincrement=False)
    repository = Column(String(255))
    source_branch = Column(String(255))
    deleted_at = Column(mysql.DATETIME(fsp=6))
    workflow = Column(String(255), nullable=False)
    result = Column(String(32), nullable=False)
    # All remote and collection timestamps use UTC.
    started_at = Column(mysql.DATETIME(fsp=6), nullable=True)
    finished_at = Column(mysql.DATETIME(fsp=6), nullable=True)
    last_collected_at = Column(mysql.DATETIME(fsp=6), nullable=True)
    pending_reruns = Column(JSON(none_as_null=True), nullable=True)
    issue_number = Column(BigInteger, nullable=True)
    issue_state = Column(String(16), nullable=True)


class DailyPipelineCollectionNode(Base):
    """A workflow, matrix/call aggregate, or actual job in one Daily tree.

    A NULL parent_id attaches the node directly to the Daily run. Logical
    reusable-workflow job groups have no native GitHub ID (source_id is NULL).
    """

    __tablename__ = "daily_pipeline_collection_nodes"
    __table_args__ = (
        UniqueConstraint("run_id", "id", name="uq_daily_collection_node_owner"),
        ForeignKeyConstraint(
            ["run_id", "parent_id"],
            ["daily_pipeline_collection_nodes.run_id", "daily_pipeline_collection_nodes.id"],
            name="fk_daily_collection_node_parent", ondelete="CASCADE",
        ),
        Index("ix_daily_collection_node_parent", "run_id", "parent_id"),
        Index("ix_daily_collection_node_result", "result", "case_type"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(
        BigInteger,
        ForeignKey("daily_pipeline_collection_runs.run_id", name="fk_daily_collection_node_run", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id = Column(BigInteger, nullable=True)
    node_type = Column(String(16), nullable=False)
    source_id = Column(BigInteger, nullable=True)
    source_run_id = Column(BigInteger, nullable=True)
    is_current = Column(Boolean, nullable=False, server_default="1")
    execution_started_at = Column(mysql.DATETIME(fsp=6))
    failed_at = Column(mysql.DATETIME(fsp=6))
    vllm_ref = Column(String(255))
    name = Column(Text(collation="utf8mb4_bin"), nullable=False)
    result = Column(String(32), nullable=False)
    case_type = Column(String(16), nullable=True)
    hardware = Column(String(32), nullable=True)
    vllm_version = Column(String(32), nullable=True)


class DailyPipelineMergedPR(Base):
    """A pull request merged within one Daily trigger window."""

    __tablename__ = "daily_pipeline_merged_pr"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    trigger_history_id = Column(
        BigInteger,
        ForeignKey("daily_pipeline_trigger_history.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pr_number = Column(BigInteger, primary_key=True)
    title = Column(Text, nullable=False)
    author = Column(String(255), nullable=True)
    merged_by = Column(String(255), nullable=True)
    url = Column(String(512), nullable=True)
    merged_at = Column(mysql.DATETIME(fsp=6), nullable=False)
    issue_id = Column(BigInteger, ForeignKey("daily_pipeline_issues.id", ondelete="SET NULL"))
