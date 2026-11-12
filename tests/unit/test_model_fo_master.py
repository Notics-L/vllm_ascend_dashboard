"""ModelFoMaster（模型-FO 映射主库）单元测试。

覆盖：
1. Excel 解析 _parse_model_fo_master_xlsx（表头校验 / 数据行 / 空行跳过 / 坏表头）
2. 纯函数 _parse_model_and_yaml（test_model 拆分）
3. 纯函数 _lookup_master_model_fo / _lookup_model_fo（yaml 优先 → model 小写）
4. 模块级 _build_job_model_fo_map（DB：快照 model_fo 优先 → ModelFoMaster 回退）
5. 报告 builder 实例方法 _query_model_fo_master / _lookup_model_fo

DB 相关用例需要可用 MySQL（tests/mysql_test_db.py 的 TEST_DATABASE_URL），
无数据库时 pytest 阶段会因 fixture 连接失败而报错，属预期（CI 已具备）。
"""
from __future__ import annotations

import io
from datetime import UTC, date

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.ci import (
    _MODEL_FO_MASTER_IMPORT_HEADERS,
    _build_job_model_fo_map,
    _lookup_master_model_fo,
    _parse_model_fo_master_xlsx,
)
from infrastructure.persistence.models import CIJob, ModelFoMaster, NightlyTestCase
from reporting.ci_daily_report_builder import (
    CIDailyReportBuilder,
    _parse_model_and_yaml,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_xlsx(headers: list[str], rows: list[tuple]) -> bytes:
    """构造 Excel bytes（表头 + 数据行）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "模型FO主库"
    ws.append(headers)
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _master(
    suite: str = "A2",
    node_type: str = "单机",
    test_yaml: str = "Qwen3-30B-A3B-BF16-A2.yaml",
    model_name: str = "qwen3-30b-a3b-bf16-a2-performance",
    coverage_hardware: str | None = "A2单机",
    model_fo: str = "张三 12345678",
    enabled: bool = True,
) -> ModelFoMaster:
    return ModelFoMaster(
        suite=suite,
        node_type=node_type,
        test_yaml=test_yaml,
        model_name=model_name,
        coverage_hardware=coverage_hardware,
        model_fo=model_fo,
        enabled=enabled,
    )


def _nightly_case(
    workflow_name: str = "Nightly-A2",
    job_name: str = "run-1",
    test_model: str | None = None,
    model_fo: str | None = None,
) -> NightlyTestCase:
    return NightlyTestCase(
        report_date=date(2026, 11, 1),
        source_branch="main",
        workflow_name=workflow_name,
        job_name=job_name,
        test_model=test_model,
        model_fo=model_fo,
        enabled=True,
    )


def _ci_job(
    gh_job_id: int,
    run_id: int,
    workflow_name: str,
    job_name: str,
) -> CIJob:
    return CIJob(
        job_id=gh_job_id,
        run_id=run_id,
        workflow_name=workflow_name,
        job_name=job_name,
        status="completed",
        conclusion="failure",
    )


# ---------------------------------------------------------------------------
# Excel 解析
# ---------------------------------------------------------------------------

def test_parse_xlsx_header_mismatch_raises():
    bad = _make_xlsx(["错列1", "错列2", "错列3", "错列4", "错列5", "错列6"], [("x",) * 6])
    with pytest.raises(ValueError):
        _parse_model_fo_master_xlsx(bad)


def test_parse_xlsx_empty_raises():
    buf = io.BytesIO()
    Workbook().save(buf)  # 默认一个空 sheet，无表头
    buf.seek(0)
    # openpyxl 默认 sheet 无内容 → 空表头 → 报错
    with pytest.raises(ValueError):
        _parse_model_fo_master_xlsx(buf.getvalue())


def test_parse_xlsx_normal_rows_skip_empty():
    content = _make_xlsx(
        _MODEL_FO_MASTER_IMPORT_HEADERS,
        [
            ("A2", "单机", "m1.yaml", "model-1", "A2单机", "张三 1111"),
            (None, None, None, None, None, None),  # 全空行跳过
            ("   ", "  ", "", None, "", None),    # 空白行跳过
            ("A3", "单机", "m2.yaml", "model-2", None, "李四 2222"),
        ],
    )
    rows = _parse_model_fo_master_xlsx(content)
    assert len(rows) == 2
    assert rows[0] == {
        "suite": "A2",
        "node_type": "单机",
        "test_yaml": "m1.yaml",
        "model_name": "model-1",
        "coverage_hardware": "A2单机",
        "model_fo": "张三 1111",
    }
    # 空 coverage 归一为 None
    assert rows[1]["coverage_hardware"] is None
    assert rows[1]["model_fo"] == "李四 2222"


# ---------------------------------------------------------------------------
# 纯函数：test_model 拆分
# ---------------------------------------------------------------------------

def test_parse_model_and_yaml_yaml_suffix():
    assert _parse_model_and_yaml("Foo.yaml") == ("Foo", "Foo.yaml")
    assert _parse_model_and_yaml("Bar.yml") == ("Bar", "Bar.yml")


def test_parse_model_and_yaml_no_suffix():
    assert _parse_model_and_yaml("plain-model") == ("plain-model", None)


def test_parse_model_and_yaml_none():
    assert _parse_model_and_yaml(None) == (None, None)
    assert _parse_model_and_yaml("   ") == (None, None)


# ---------------------------------------------------------------------------
# 纯函数：fallback 查询
# ---------------------------------------------------------------------------

def test_lookup_master_yaml_priority_over_model():
    m = {
        "yaml:m1.yaml": "FO-yaml",
        "model:model-1": "FO-model",
    }
    assert _lookup_master_model_fo(m, "m1.yaml", "MODEL-1") == "FO-yaml"
    # 无 yaml 命中 → model 小写
    assert _lookup_master_model_fo(m, None, "MODEL-1") == "FO-model"
    # 两者皆无 → None
    assert _lookup_master_model_fo(m, "m1.yaml", None) == "FO-yaml"
    assert _lookup_master_model_fo(m, "unknown.yaml", None) is None


def test_builder_lookup_model_fo_same_semantics():
    m = {"yaml:m1.yaml": "FO-yaml", "model:model-1": "FO-model"}
    lk = CIDailyReportBuilder._lookup_model_fo
    assert lk(m, "m1.yaml", "model-1") == "FO-yaml"
    assert lk(m, None, "MODEL-1") == "FO-model"
    assert lk(m, None, None) is None


# ---------------------------------------------------------------------------
# DB：job model_fo map 解析（fallback 链）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_job_model_fo_map_snapshot_model_fo_priority(master_session):
    db: AsyncSession = master_session
    # 快照自带 model_fo（优先），同时 master 里有 yaml/model 映射
    db.add(_nightly_case(
        workflow_name="Nightly-A2",
        job_name="run-1",
        test_model="Qwen3-30B-A3B-BF16-A2.yaml",
        model_fo="快照FO",
    ))
    db.add(_master(
        suite="A2", node_type="单机",
        test_yaml="Qwen3-30B-A3B-BF16-A2.yaml",
        model_name="qwen3-30b-a3b-bf16-a2-performance",
        model_fo="主库FO",
    ))
    job = _ci_job(1, 100, "Nightly-A2", "run-1")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    result = await _build_job_model_fo_map(db, [job])
    assert result[job.id] == "快照FO"


@pytest.mark.asyncio
async def test_build_job_model_fo_map_fallback_to_master(master_session):
    db: AsyncSession = master_session
    # 快照无 model_fo，靠 master 回退（yaml 精确命中）
    db.add(_nightly_case(
        workflow_name="Nightly-A2",
        job_name="run-1",
        test_model="Qwen3-30B-A3B-BF16-A2.yaml",
        model_fo=None,
    ))
    db.add(_master(
        suite="A2", node_type="单机",
        test_yaml="Qwen3-30B-A3B-BF16-A2.yaml",
        model_name="qwen3-30b-a3b-bf16-a2-performance",
        model_fo="主库FO-yaml",
    ))
    job = _ci_job(1, 100, "Nightly-A2", "run-1")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    result = await _build_job_model_fo_map(db, [job])
    assert result[job.id] == "主库FO-yaml"


@pytest.mark.asyncio
async def test_build_job_model_fo_map_no_match(master_session):
    db: AsyncSession = master_session
    # 无快照、无 master 命中
    job = _ci_job(1, 100, "Nightly-A2", "ghost-job")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    result = await _build_job_model_fo_map(db, [job])
    assert job.id not in result


@pytest.mark.asyncio
async def test_build_job_model_fo_map_model_name_lowercase_fallback(master_session):
    db: AsyncSession = master_session
    # 快照 test_model 无 .yaml 后缀 → 仅靠 model_name 小写命中
    db.add(_nightly_case(
        workflow_name="Nightly-A2",
        job_name="run-1",
        test_model="qwen3-30b-a3b-bf16-a2",  # 无后缀
        model_fo=None,
    ))
    db.add(_master(
        suite="A2", node_type="单机",
        test_yaml="other.yaml",  # yaml 不匹配
        model_name="Qwen3-30B-A3B-BF16-A2-Performance",
        model_fo="主库FO-model",
    ))
    job = _ci_job(1, 100, "Nightly-A2", "run-1")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    result = await _build_job_model_fo_map(db, [job])
    assert result[job.id] == "主库FO-model"


# ---------------------------------------------------------------------------
# DB：报告 builder 实例方法 master 查询
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_builder_query_model_fo_master(master_session):
    db: AsyncSession = master_session
    db.add(_master(
        suite="A2", node_type="单机",
        test_yaml="m1.yaml", model_name="model-1",
        model_fo="FO-1",
    ))
    db.add(_master(
        suite="A3", node_type="单机",
        test_yaml="m2.yaml", model_name="Model-2",
        model_fo="FO-2",
    ))
    # disabled 记录不应命中
    db.add(_master(
        suite="A4", node_type="单机",
        test_yaml="m3.yaml", model_name="model-3",
        model_fo="FO-3", enabled=False,
    ))
    await db.commit()

    builder = CIDailyReportBuilder(db)
    mapping = await builder._query_model_fo_master({"m1.yaml", "m2.yaml", "m3.yaml"}, {"MODEL-2"})
    assert mapping.get("yaml:m1.yaml") == "FO-1"
    assert mapping.get("model:model-2") == "FO-2"
    assert "yaml:m3.yaml" not in mapping
    assert "model:model-3" not in mapping
