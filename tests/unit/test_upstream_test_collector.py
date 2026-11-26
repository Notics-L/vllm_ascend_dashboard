"""上游测试采集的解析与统计口径测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from infrastructure.core.config import settings
from upstream_test.collector import (
    DEFAULT_CI_WORKFLOW_FILE,
    UpstreamTestCollector,
    _failure_reasons,
    _pick_latest_version,
    _pick_latest_weekly,
    _pick_previous_version,
    _shorten,
    _summary,
    parse_case_csv,
    parse_issue_csv,
)

CASE_CSV = """\ufefftest_path,status,关键片段,分析,原因分类,版本,是否已删除
tests/a.py,passed,,,,,
tests/b.py,failed,"E   AssertionError: boom",分析说明,后端未适配,v0.33.0,
tests/c.py,unknown,OSError: gated repo,需要授权,无法执行-权限,v0.33.0,
tests/d.py,skipped,,,,,
tests/e.py,failed,,,,v0.33.0,
"""

ISSUE_CSV = """test_path,issue,影响程度,影响,notes,title
tests/a.py,12942,,,,[Bug][Upstream]: ray related case failed
tests/b.py,,,,"已修复待CI确认",[Bug][Upstream]: no number
"""


def test_parse_case_csv_handles_bom_and_quotes() -> None:
    rows = parse_case_csv(CASE_CSV)

    assert [row["test_path"] for row in rows] == [
        "tests/a.py",
        "tests/b.py",
        "tests/c.py",
        "tests/d.py",
        "tests/e.py",
    ]
    # 带引号且含逗号的片段必须完整保留
    assert rows[1]["snippet"] == "E   AssertionError: boom"
    assert rows[1]["category"] == "后端未适配"
    assert rows[2]["category"] == "无法执行-权限"


def test_summary_counts_each_status() -> None:
    summary = _summary(parse_case_csv(CASE_CSV))

    assert summary == {
        "passed": 1,
        "failed": 2,
        "skipped": 1,
        "unknown": 1,
        "total": 5,
    }


def test_parse_issue_csv_keeps_nullable_number() -> None:
    rows = parse_issue_csv(ISSUE_CSV, "open")

    assert rows[0]["issue_number"] == 12942
    assert rows[0]["state"] == "open"
    # 没有编号但有标题的行也要保留，后续可人工补齐
    assert rows[1]["issue_number"] is None
    assert rows[1]["notes"] == "已修复待CI确认"


def test_parse_case_csv_skips_rows_without_path() -> None:
    rows = parse_case_csv("test_path,status\n,passed\ntests/x.py,failed\n")
    assert [row["test_path"] for row in rows] == ["tests/x.py"]


def test_pick_latest_weekly_uses_timestamp_suffix() -> None:
    names = [
        "upstream_cases_20261109085518.csv",
        "upstream_cases_20261117091228.csv",
        "weekly.csv",
    ]
    assert _pick_latest_weekly(names) == "upstream_cases_20261117091228.csv"


def test_pick_latest_version_compares_numerically() -> None:
    # 字符串排序会把 v0.9.0 排在 v0.33.0 之后，必须按数值比较
    names = ["v0.9.0.csv", "v0.33.0.csv", "v0.32.0.csv", "local.csv"]
    assert _pick_latest_version(names) == "v0.33.0.csv"


def test_pick_helpers_return_none_on_empty_input() -> None:
    assert _pick_latest_weekly([]) is None
    assert _pick_latest_version(["local.csv"]) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pass", "passed"),
        ("FAIL", "failed"),
        ("error", "failed"),
        ("skip", "skipped"),
        ("", "unknown"),
        ("weird", "unknown"),
    ],
)
def test_status_normalization(raw: str, expected: str) -> None:
    rows = parse_case_csv(f"test_path,status\ntests/x.py,{raw}\n")
    assert rows[0]["status"] == expected


def test_week_window_boundaries_are_comparable() -> None:
    """定时运行的时间戳带时区，窗口起点必须也是 aware datetime。"""
    now = datetime.now(UTC)
    week_start = now - timedelta(days=now.weekday())
    assert week_start.tzinfo is not None


def test_pick_previous_version_returns_next_lower_version() -> None:
    names = ["local.csv", "v0.31.0.csv", "v0.32.0.csv", "v0.33.0.csv"]
    assert _pick_previous_version(names, "v0.33.0.csv") == "v0.32.0.csv"


def test_pick_previous_version_none_when_only_one_version() -> None:
    assert _pick_previous_version(["local.csv", "v0.33.0.csv"], "v0.33.0.csv") is None
    assert _pick_previous_version(["v0.33.0.csv"], None) is None


def test_pick_previous_version_ignores_newer_versions() -> None:
    """只在更旧的版本里找，避免把更高版本当成"上一版本"。"""
    names = ["v0.33.0.csv", "v0.34.0.csv"]
    assert _pick_previous_version(names, "v0.33.0.csv") is None


def test_failure_reasons_groups_and_skips_passed() -> None:
    rows = parse_case_csv(
        "test_path,status,分析,原因分类\n"
        "tests/a.py,failed,,无法执行-后端\n"
        "tests/b.py,failed,,无法执行-后端\n"
        "tests/c.py,unknown,需要授权,\n"
        "tests/d.py,passed,,\n"
        "tests/e.py,skipped,,\n"
    )
    reasons = _failure_reasons(rows)

    # 通过不计入；未通过按分类聚合，分类优先于分析文本
    assert reasons[0] == {"reason": "无法执行-后端", "count": 2}
    assert {"reason": "需要授权", "count": 1} in reasons
    assert {"reason": "未分类", "count": 1} in reasons
    assert sum(item["count"] for item in reasons) == 4


def test_failure_reasons_counts_skipped_with_category() -> None:
    """上游把「无法执行」标成 skipped，这类必须计入，否则低估后端缺口。"""
    rows = parse_case_csv(
        "test_path,status,分析,原因分类\n"
        "tests/a.py,skipped,昇腾镜像无 CUDA,无法执行-后端\n"
        "tests/b.py,skipped,昇腾镜像无 CUDA,无法执行-后端\n"
        "tests/c.py,unknown,,无法执行-后端\n"
        "tests/d.py,failed,,无法执行-后端\n"
        "tests/e.py,passed,,\n"
    )
    assert _failure_reasons(rows) == [{"reason": "无法执行-后端", "count": 4}]


def test_failure_reasons_falls_back_to_uncategorized() -> None:
    rows = parse_case_csv("test_path,status,分析,原因分类\ntests/a.py,failed,,\n")
    assert _failure_reasons(rows) == [{"reason": "未分类", "count": 1}]


def test_failure_reasons_caps_number_of_items() -> None:
    lines = ["test_path,status,分析,原因分类"]
    for index in range(20):
        lines.append(f"tests/case{index}.py,failed,,原因{index}")
    reasons = _failure_reasons(parse_case_csv("\n".join(lines)), limit=5)
    assert len(reasons) == 5


def test_shorten_keeps_short_text_and_truncates_long_text() -> None:
    assert _shorten("短原因") == "短原因"
    long_text = "很长的分析" * 20
    shortened = _shorten(long_text)
    assert shortened.endswith("…")
    assert len(shortened) == 41  # 40 字 + 省略号


def test_shorten_collapses_whitespace() -> None:
    assert _shorten("a\n\n b   c") == "a b c"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_runs", [False, True])
async def test_collection_authenticates_and_audits_github_api_only(
    monkeypatch, caplog, fail_runs, github_audit_records,
):
    token = "local-audit-test-token"
    monkeypatch.setattr(settings, "GITHUB_TOKEN", token)
    operations = github_audit_records

    def respond(request):
        path = request.url.path
        if request.url.host == "raw.githubusercontent.com":
            assert "Authorization" not in request.headers
            return httpx.Response(200, text=CASE_CSV if path.endswith("v0.33.0.csv") else ISSUE_CSV)
        if "/contents/" in path:
            assert "Authorization" not in request.headers
            return httpx.Response(200, json=[{"type": "file", "name": "v0.33.0.csv"}])
        assert request.headers["Authorization"] == f"Bearer {token}"
        if path.endswith("/workflows"):
            return httpx.Response(200, json={"workflows": [{"id": 5, "path": DEFAULT_CI_WORKFLOW_FILE}]})
        if path.endswith("/runs"):
            if fail_runs:
                raise httpx.ReadTimeout(f"raw transport error with {token}", request=request)
            return httpx.Response(200, json={"workflow_runs": []})
        assert "/git/trees/" in path
        return httpx.Response(200, json={"tree": []})

    result = await UpstreamTestCollector(transport=httpx.MockTransport(respond)).collect()

    assert result["upstream_tests"]["available"]
    assert result["ci_summary"]["available"] is not fail_runs
    assert len(operations) == 3
    assert {record["source"] for record in operations} == {"upstream_test"}
    assert {record["credential_source"] for record in operations} == {"global"}
    assert [record["result"] for record in operations] == [
        "success", "failed" if fail_runs else "success", "success",
    ]
    assert token not in repr(result)
    assert token not in caplog.text
