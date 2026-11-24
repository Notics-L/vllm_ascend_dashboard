"""History of authenticated GitHub operations issued by the dashboard."""

from sqlalchemy import BigInteger, Column, Index, Integer, SmallInteger, String
from sqlalchemy.dialects.mysql import DATETIME

from infrastructure.persistence.models import Base


class GitHubOperation(Base):
    __tablename__ = "github_operate_log"
    __table_args__ = (
        Index("ix_github_operations_time", "occurred_at", "id"),
        Index("ix_github_operations_repo_time", "repository", "occurred_at", "id"),
        Index("ix_github_operations_result_time", "result", "occurred_at", "id"),
        Index("ix_github_operations_credential_time", "credential_source", "occurred_at", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at = Column(DATETIME(fsp=6), nullable=False)
    process = Column(String(16), nullable=False)
    source = Column(String(40), nullable=False)
    credential_source = Column(String(16))
    actor = Column(String(50))
    repository = Column(String(140))
    method = Column(String(12), nullable=False)
    operation_type = Column(String(8), nullable=False)
    endpoint = Column(String(512), nullable=False)
    result = Column(String(12), nullable=False)
    http_status = Column(SmallInteger)
    duration_ms = Column(Integer, nullable=False)
    error_type = Column(String(24))
    github_request_id = Column(String(64))
