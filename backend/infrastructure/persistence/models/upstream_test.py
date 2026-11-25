"""上游测试模块的数据模型。

上游仓库 ``vllm-ascend/upstream-test`` 每天产出三组 CSV：

- ``weekly/upstream_cases_<ts>.csv``：本周上游用例执行结果快照
- ``local/<version>.csv``：某个 vLLM 版本的本地上游用例执行结果
- ``issue/issue*.csv``：上游用例关联的 issue

本模块把每次拉取结果落成一份不可变的快照，页面只读最新快照，因此每日同步
不会改写历史，也便于排查某一天的统计来源。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.types import JSON as JSONType

from infrastructure.persistence.models import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UpstreamTestSnapshot(Base):
    """一次上游测试结果抓取的完整快照。"""

    __tablename__ = "upstream_test_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # 抓取元信息
    fetched_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    source_repo = Column(String(200), nullable=False, default="vllm-ascend/upstream-test")
    source_commit = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="success", index=True)
    error_message = Column(Text, nullable=True)
    # 部分数据源失败时的告警（JSON 数组），便于页面提示"数据不完整"
    warnings = Column(JSONType, nullable=True)

    # 本周用例结果：{"total": n, "passed": n, "failed": n, "skipped": n, "unknown": n}
    weekly_file = Column(String(255), nullable=True)
    weekly_summary = Column(JSONType, nullable=True)
    # 未通过用例的原因聚合：[{"reason": str, "count": n}]
    weekly_failure_reasons = Column(JSONType, nullable=True)

    # 最新版本的本地用例结果
    version_name = Column(String(64), nullable=True, index=True)
    version_file = Column(String(255), nullable=True)
    # 上一个版本名（仅作展示，说明当前版本相对于哪个版本）
    previous_version_name = Column(String(64), nullable=True)
    version_summary = Column(JSONType, nullable=True)
    version_failure_reasons = Column(JSONType, nullable=True)

    # issue 统计：{"open": n, "resolved": n, "other": n}
    issue_summary = Column(JSONType, nullable=True)
    # 相对上一份快照新增的未关闭 issue 数；无历史可比对时为 None
    issue_added_count = Column(Integer, nullable=True)

    # 上游总用例统计（vLLM 主仓库 tests/**/test_*.py），可迁移率的分母
    upstream_tests = Column(JSONType, nullable=True)

    # CI 执行信息（E2E-upstream workflow）
    ci_summary = Column(JSONType, nullable=True)

    # 明细行，供页面下钻
    weekly_cases = Column(JSONType, nullable=True)
    version_cases = Column(JSONType, nullable=True)
    issues = Column(JSONType, nullable=True)


class UpstreamTestCaseResult(Base):
    """上游用例单条结果（最新快照的展开，便于按用例查询）。"""

    __tablename__ = "upstream_test_case_results"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id = Column(BigInteger, nullable=False, index=True)
    scope = Column(String(20), nullable=False, index=True)  # weekly | version
    version_name = Column(String(64), nullable=True, index=True)
    test_path = Column(String(500), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    snippet = Column(Text, nullable=True)
    analysis = Column(Text, nullable=True)
    category = Column(String(100), nullable=True, index=True)
    case_version = Column(String(64), nullable=True)
    removed = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class UpstreamTestIssue(Base):
    """上游用例关联的 issue（最新快照的展开）。"""

    __tablename__ = "upstream_test_issues"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id = Column(BigInteger, nullable=False, index=True)
    state = Column(String(20), nullable=False, index=True)  # open | resolved | other
    issue_number = Column(Integer, nullable=True, index=True)
    issue_url = Column(String(500), nullable=True)
    issue_title = Column(String(1000), nullable=True)
    test_path = Column(String(500), nullable=True, index=True)
    impact_level = Column(String(100), nullable=True)
    impact = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
