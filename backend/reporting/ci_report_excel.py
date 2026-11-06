"""CI 每日综合报错报告 Excel 多 Sheet 导出。

使用 openpyxl 生成多 Sheet 工作簿：
- Sheet 1 失败详情（核心）：每条失败的分支/Workflow/Job/模型/硬件/结论/耗时/
  问题范畴/原始报错线索行/处理状态/责任范围/根因摘要/GitHub 链接
- Sheet 2 失败维度统计：按问题范畴 / 分支 / 硬件 / 模型 / 处理状态分布
- Sheet 3 运行记录（次级）：窗口内全部 workflow 运行

Sheet 顺序与 PDF 一致：失败详情为核心，运行记录弱化靠后。
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any, Callable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from reporting.ci_daily_report_builder import format_conclusion_zh

logger = logging.getLogger(__name__)

# AI 分析状态 -> 中文
_AI_STATUS_ZH: dict[str, str] = {
    "completed": "已完成",
    "analyzing": "分析中",
    "pending": "待分析",
    "reused": "已复用",
    "failed": "分析失败",
    "degraded": "已降级",
    "cancelled": "已取消",
    "raw_log_collected": "已收集",
}


def _ai_status_zh(status: Any) -> Any:
    if not status:
        return None
    return _AI_STATUS_ZH.get(str(status), str(status))

HEADER_FILL = "1E40AF"
RED_FILL = "FECACA"
GREEN_FILL = "DCFCE7"
AMBER_FILL = "FEF3C7"
GRAY_FILL = "F3F4F6"


class CIDailyReportExcel:
    """Build a multi-sheet Excel workbook for the CI daily report."""

    def __init__(self, report: dict):
        self.report = report

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def render(self) -> bytes:
        wb = Workbook()
        self._build_failures_sheet(wb)
        self._build_distribution_sheet(wb)
        self._build_runs_sheet(wb)

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _headers(ws, title: str, columns: list[str], start_row: int = 1) -> int:
        ws.merge_cells(start_row=start_row, start_column=1,
                       end_row=start_row, end_column=len(columns))
        cell = ws.cell(row=start_row, column=1, value=title)
        cell.font = Font(bold=True, size=14, color="1F2937")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[start_row].height = 28

        header_row = start_row + 1
        for idx, col in enumerate(columns, 1):
            c = ws.cell(row=header_row, column=idx, value=col)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=HEADER_FILL)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for idx in range(1, len(columns) + 1):
            ws.column_dimensions[get_column_letter(idx)].width = 16
        return header_row

    @staticmethod
    def _add_table(ws, header_row: int, column_count: int, row_count: int) -> None:
        if row_count <= 0:
            return
        ref = (f"A{header_row}:"
               f"{get_column_letter(column_count)}{header_row + row_count}")
        tab_id = f"T_{ws.title}_{header_row}_report"
        tbl = Table(displayName=tab_id, ref=ref)
        style = TableStyleInfo(
            name="TableStyleMedium9",
            showRowStripes=True,
            showFirstColumn=False,
            showLastColumn=False,
            showColumnStripes=False,
        )
        tbl.tableStyleInfo = style
        ws.add_table(tbl)

    @staticmethod
    def _fmt(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, float):
            return round(value, 2)
        return value

    # ------------------------------------------------------------------
    # Sheet 1: 失败详情（核心）
    # ------------------------------------------------------------------

    def _build_failures_sheet(self, wb: Workbook) -> None:
        failures = self.report.get("failures", [])
        ws = wb.active
        ws.title = "失败详情"

        # GitHub 前置（第3列），用户可快速跳转
        columns = [
            "日期（北京时间）", "分支", "GitHub 链接",
            "Workflow", "Job", "模型/用例", "硬件", "结论",
            "责任人", "失败关键步骤",
            "耗时(秒)", "耗时", "问题范畴", "AI 分析",
            "精炼错误", "原始报错线索行", "处理状态",
            "责任范围", "根因摘要", "改进措施", "备注",
        ]
        header_row = self._headers(ws, "失败详情（核心）", columns)

        # 需要换行的列索引（1-based）
        WRAP_COLS = {5, 6, 10, 15, 16, 18, 19, 20, 21}
        # 需要颜色填充的列索引
        CONCLUSION_COL = 8
        AI_STATUS_COL = 14
        PROCESSING_COL = 17
        CONCLUSION_COLORS = {
            "失败": RED_FILL, "超时": RED_FILL, "启动失败": RED_FILL,
            "成功": GREEN_FILL, "已取消": GRAY_FILL, "已跳过": GRAY_FILL,
        }
        AI_COLORS = {
            "已完成": GREEN_FILL, "分析中": AMBER_FILL, "待分析": AMBER_FILL,
            "分析失败": RED_FILL, "已复用": None,
        }

        for r, f in enumerate(failures, header_row + 1):
            raw_lines = f.get("raw_log_lines") or []
            if raw_lines:
                error_cell = "\n".join(str(x) for x in raw_lines)
            else:
                error_cell = f.get("error_line") or "—"
            row = [
                self._fmt(f.get("report_datetime") or f.get("report_date")),  # 1
                self._fmt(f.get("source_branch")),                            # 2
                self._fmt(f.get("github_url")),                               # 3
                self._fmt(f.get("workflow_name")),                            # 4
                self._fmt(f.get("job_name")),                                 # 5
                self._fmt(f.get("test_model")) or "—",                        # 6
                self._fmt(f.get("hardware")),                                 # 7
                format_conclusion_zh(f.get("conclusion")),                    # 8
                self._fmt(f.get("owner")) or "—",                             # 9
                self._fmt(f.get("failed_step")) or "—",                       # 10
                self._fmt(f.get("duration_seconds")),                         # 11
                self._fmt(f.get("duration_text")),                            # 12
                self._fmt(f.get("problem_category")) or "未定界",              # 13
                _ai_status_zh(f.get("analysis_status")),                     # 14
                self._fmt(f.get("error_brief")) or "—",                       # 15
                error_cell,                                                   # 16
                self._fmt(f.get("processing_status")),                       # 17
                self._fmt(f.get("model_fo")),                                # 18
                self._fmt(f.get("root_cause_summary")),                      # 19
                self._fmt(f.get("improvement_measures")),                    # 20
                self._fmt(f.get("notes")),                                   # 21
            ]
            for idx, v in enumerate(row, 1):
                cell = ws.cell(row=r, column=idx, value=v)
                # 换行
                if idx in WRAP_COLS:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
                # 结论颜色
                if idx == CONCLUSION_COL and v in CONCLUSION_COLORS:
                    cell.fill = PatternFill("solid", fgColor=CONCLUSION_COLORS[v])
                    cell.font = Font(bold=True, color="1F2937" if v == "失败" else "1F2937")
                # AI 状态颜色
                if idx == AI_STATUS_COL and v in AI_COLORS:
                    fill = AI_COLORS[v]
                    if fill:
                        cell.fill = PatternFill("solid", fgColor=fill)
                # 处理状态颜色
                if idx == PROCESSING_COL and v == "未处理":
                    cell.fill = PatternFill("solid", fgColor=AMBER_FILL)
                    cell.font = Font(bold=True, color="92400E")

        # GitHub 列超链接
        for r in range(header_row + 1, header_row + 1 + len(failures)):
            url_cell = ws.cell(row=r, column=3)
            if url_cell.value and str(url_cell.value).startswith("http"):
                url_cell.hyperlink = str(url_cell.value)
                url_cell.font = Font(color="2563EB", underline="single")

        # 列宽
        self._autosize(ws, len(columns), header_row, len(failures))
        wide = {3: 50, 5: 40, 6: 40, 10: 15, 15: 50, 16: 60, 18: 50, 19: 50, 20: 40, 21: 30}
        for idx, width in wide.items():
            ws.column_dimensions[get_column_letter(idx)].width = width

        self._add_table(ws, header_row, len(columns), len(failures))

    # ------------------------------------------------------------------
    # Sheet 2: 失败维度统计
    # ------------------------------------------------------------------

    def _build_distribution_sheet(self, wb: Workbook) -> None:
        summary = self.report.get("summary", {})
        ws = wb.create_sheet("失败维度统计")

        columns = ["维度", "取值", "数量"]
        header_row = self._headers(ws, "失败维度分布", columns)

        rows: list[list[Any]] = []
        for label, data in (
            ("问题范畴", summary.get("failure_by_category", {})),
            ("分支", summary.get("failure_by_branch", {})),
            ("硬件", summary.get("failure_by_hardware", {})),
            ("模型/用例", summary.get("failure_by_model", {})),
            ("处理状态", summary.get("processing_distribution", {})),
        ):
            for key, count in data.items():
                rows.append([label, str(key), int(count)])

        for r, row in enumerate(rows, header_row + 1):
            for idx, v in enumerate(row, 1):
                ws.cell(row=r, column=idx, value=v)

        for idx in (1, 2, 3):
            ws.column_dimensions[get_column_letter(idx)].width = 20

        self._add_table(ws, header_row, len(columns), len(rows))

    # ------------------------------------------------------------------
    # Sheet 3: 运行记录（次级）
    # ------------------------------------------------------------------

    def _build_runs_sheet(self, wb: Workbook) -> None:
        runs = self.report.get("runs", [])
        window = self.report.get("window", {})
        ws = wb.create_sheet("运行记录")

        columns = [
            "运行ID", "#", "Workflow", "分支", "硬件", "事件", "结论",
            "结果", "开始时间", "完成时间", "耗时(秒)", "耗时", "GitHub",
        ]
        title = f"窗口内运行记录（{window.get('start_beijing','')} ~ {window.get('end_beijing','')}）"
        header_row = self._headers(ws, title, columns)

        for r, run in enumerate(runs, header_row + 1):
            row = [
                self._fmt(run.get("run_id")),
                self._fmt(run.get("run_number")),
                self._fmt(run.get("workflow_name")),
                self._fmt(run.get("branch")),
                self._fmt(run.get("hardware")),
                self._fmt(run.get("event")),
                self._fmt(run.get("conclusion")),
                self._fmt(run.get("result")),
                self._fmt(run.get("started_at")),
                self._fmt(run.get("completed_at")),
                self._fmt(run.get("duration_seconds")),
                self._fmt(run.get("duration_text")),
                self._fmt(run.get("github_url")),
            ]
            for idx, v in enumerate(row, 1):
                cell = ws.cell(row=r, column=idx, value=v)
                # GitHub 列超链接
                if idx == 13 and v and str(v).startswith("http"):
                    cell.hyperlink = str(v)
                    cell.font = Font(color="2563EB", underline="single")

        self._autosize(ws, len(columns), header_row, len(runs))
        ws.column_dimensions["M"].width = 50
        self._add_table(ws, header_row, len(columns), len(runs))

    # ------------------------------------------------------------------
    # 自适应列宽
    # ------------------------------------------------------------------

    @staticmethod
    def _autosize(ws, col_count: int, header_row: int, data_rows: int) -> None:
        for idx in range(1, col_count + 1):
            longest = len(ws.cell(row=header_row, column=idx).value or "")
            for r in range(header_row + 1, header_row + 1 + data_rows):
                v = ws.cell(row=r, column=idx).value
                if v is None:
                    continue
                longest = max(longest, min(len(str(v)), 40))
            ws.column_dimensions[get_column_letter(idx)].width = min(max(longest + 2, 10), 42)

    # ------------------------------------------------------------------
    # filename
    # ------------------------------------------------------------------

    @staticmethod
    def default_filename(report: dict) -> str:
        window = report.get("window", {})
        return f"ci_daily_report_{window.get('start_date', 'unknown')}.xlsx"
