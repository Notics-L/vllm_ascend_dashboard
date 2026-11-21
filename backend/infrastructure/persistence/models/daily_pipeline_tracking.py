"""Daily failure occurrences and durable automatic-processing history."""
from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON

from . import Base


class DailyPipelineIssue(Base):
    __tablename__ = "daily_pipeline_issues"
    __table_args__ = (UniqueConstraint("repository", "number"), UniqueConstraint("repository", "run_id"),)
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    repository = Column(String(255), nullable=False)
    number = Column(BigInteger, nullable=False)
    run_id = Column(BigInteger, nullable=False)
    title = Column(Text, nullable=False)
    state = Column(String(16), nullable=False)
    closed_at = Column(DATETIME(fsp=6))
    deleted_at = Column(DATETIME(fsp=6))


class DailyPipelineVersionFlow(Base):
    __tablename__ = "daily_pipeline_version_flows"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    status = Column(String(24), nullable=False)
    resolution = Column(JSON)
    build = Column(JSON)
    message = Column(Text)
    ready_at = Column(DATETIME(fsp=6))
    notified_at = Column(DATETIME(fsp=6))
    updated_at = Column(DATETIME(fsp=6), nullable=False)


class DailyPipelineFailure(Base):
    __tablename__ = "daily_pipeline_failures"
    __table_args__ = (UniqueConstraint("node_id"), Index("ix_daily_failure_status", "processing_status"))
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    node_id = Column(BigInteger, ForeignKey("daily_pipeline_collection_nodes.id", ondelete="RESTRICT"), nullable=False)
    issue_id = Column(BigInteger, ForeignKey("daily_pipeline_issues.id"))
    version_flow_id = Column(BigInteger, ForeignKey(
        "daily_pipeline_version_flows.id", name="fk_daily_failure_version_flow"
    ), index=True)
    problem_category = Column(String(16))
    processing_status = Column(String(16), nullable=False, default="pending")
    end_reason = Column(String(32))
    replaced_by_id = Column(BigInteger, ForeignKey("daily_pipeline_failures.id"))
    discovered_at = Column(DATETIME(fsp=6), nullable=False)
    ended_at = Column(DATETIME(fsp=6))
    resume_state = Column(JSON)


class DailyPipelineProcessNode(Base):
    __tablename__ = "daily_pipeline_process_nodes"
    __table_args__ = (UniqueConstraint("failure_id", "stage_code"), Index("ix_daily_processing_due", "status", "next_run_at"))
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    failure_id = Column(BigInteger, ForeignKey("daily_pipeline_failures.id"), nullable=False)
    previous_node_id = Column(BigInteger, ForeignKey("daily_pipeline_process_nodes.id"))
    stage_code = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    started_at = Column(DATETIME(fsp=6))
    finished_at = Column(DATETIME(fsp=6))
    next_run_at = Column(DATETIME(fsp=6))
    updated_at = Column(DATETIME(fsp=6), nullable=False)


class DailyPipelineProcessNodeExecution(Base):
    __tablename__ = "daily_pipeline_process_node_executions"
    __table_args__ = (UniqueConstraint("node_id", "attempt_no"),)
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    node_id = Column(BigInteger, ForeignKey("daily_pipeline_process_nodes.id"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)
    source = Column(String(16), nullable=False, default="auto", server_default="auto")
    operator = Column(String(255))
    request_data = Column(JSON)
    result_data = Column(JSON)
    message = Column(Text)
    started_at = Column(DATETIME(fsp=6), nullable=False)
    finished_at = Column(DATETIME(fsp=6))
