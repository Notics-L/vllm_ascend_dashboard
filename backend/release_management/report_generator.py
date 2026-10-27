"""
报告生成器 - PDF 和 Excel 报告生成
"""
import json
import logging
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

logger = logging.getLogger(__name__)


def fmt_duration(sec: float) -> str:
    if sec < 60:
        return f"{sec:.0f}s"
    m = int(sec // 60)
    s = int(sec % 60)
    if m < 60:
        return f"{m}m{s}s" if s > 0 else f"{m}m"
    h = m // 60
    m = m % 60
    return f"{h}h{m}m"


class ReportGenerator:
    """报告生成器"""

    # 样式常量
    title_font = Font(name="Microsoft YaHei", bold=True, size=16, color="1F4E79")
    section_font = Font(name="Microsoft YaHei", bold=True, size=12, color="2E75B6")
    header_font = Font(name="Microsoft YaHei", bold=True, size=10, color="FFFFFF")
    normal_font = Font(name="Microsoft YaHei", size=9)
    bold_font = Font(name="Microsoft YaHei", bold=True, size=9)
    red_bold = Font(name="Microsoft YaHei", bold=True, size=10, color="C00000")

    header_fill = PatternFill(start_color="2E75B6", end_color="2E75B6", fill_type="solid")
    success_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    failure_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    warn_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    gray_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="B0B0B0"),
        right=Side(style="thin", color="B0B0B0"),
        top=Side(style="thin", color="B0B0B0"),
        bottom=Side(style="thin", color="B0B0B0"),
    )

    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # Column spec shortcuts
    _s = str  # string passthrough
    _f = float
    _r2 = lambda v: round(v, 2) if isinstance(v, (int, float)) else 0
    _int = int
    _fmt = fmt_duration

    @staticmethod
    def _style_header(ws, row: int, ncols: int):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = ReportGenerator.header_font
            cell.fill = ReportGenerator.header_fill
            cell.alignment = ReportGenerator.header_align
            cell.border = ReportGenerator.thin_border

    @staticmethod
    def _status_fill(status: str):
        s = str(status).lower()
        if s == "success":
            return ReportGenerator.success_fill
        elif s == "failure":
            return ReportGenerator.failure_fill
        elif s in ("skipped", "cancelled", "action_required"):
            return ReportGenerator.warn_fill
        return None

    @staticmethod
    def _style_row(ws, row: int, ncols: int):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = ReportGenerator.normal_font
            cell.border = ReportGenerator.thin_border
            cell.alignment = ReportGenerator.center_align

    @staticmethod
    def _build_sheet(
        wb: Workbook, sheet_name: str, headers: list[str],
        rows: list[dict[str, Any]], columns: list[tuple[str, object]],
        status_col: int | None = None,
        left_align_cols: set[int] | None = None,
    ) -> None:
        """Build a sheet from column specs: each (key, transform_fn).

        Args:
            status_col: 0-based index into columns list for status-cell coloring.
            left_align_cols: set of column names to left-align.
        """
        ws = wb.create_sheet(sheet_name)
        for i, h in enumerate(headers, 1):
            ws.cell(row=1, column=i, value=h)
        ReportGenerator._style_header(ws, 1, len(headers))

        left_cols = left_align_cols or set()
        for idx, row_data in enumerate(rows, 2):
            for ci, (key, transform) in enumerate(columns, 1):
                raw = row_data.get(key, "")
                val = transform(raw) if callable(transform) else raw
                cell = ws.cell(row=idx, column=ci, value=val)
                if key in left_cols:
                    cell.alignment = ReportGenerator.left_align

            if status_col is not None:
                status_key = columns[status_col][0]
                status_val = row_data.get(status_key, "")
                fill = ReportGenerator._status_fill(status_val)
                if fill:
                    ws.cell(row=idx, column=status_col + 1).fill = fill

            ReportGenerator._style_row(ws, idx, len(columns))

    async def generate_excel(
        self,
        workflow_name: str,
        stats: dict[str, Any],
        runs: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        evaluation: dict[str, Any] | None = None,
        timeline: list[dict[str, Any]] | None = None,
        phase_data: list[dict[str, Any]] | None = None,
        target_data: list[dict[str, Any]] | None = None,
        arch_data: list[dict[str, Any]] | None = None,
        step_data: list[dict[str, Any]] | None = None,
        run_breakdown: list[dict[str, Any]] | None = None,
    ) -> bytes:
        wb = Workbook()

        # Sheet 1: 综合概览
        self._build_overview_sheet(wb, workflow_name, stats, evaluation)

        # Sheet 2: 构建记录明细
        self._build_runs_sheet(wb, runs)

        # Sheet 3: Job Group 层级
        self._build_groups_sheet(wb, groups)

        # Sheet 4: 阶段耗时分析
        if phase_data:
            self._build_phase_sheet(wb, phase_data)

        # Sheet 5: 目标耗时分析
        if target_data:
            self._build_target_sheet(wb, target_data)

        # Sheet 6: 架构分布
        if arch_data:
            self._build_arch_sheet(wb, arch_data)

        # Sheet 7: Top20 慢 Step
        if step_data:
            self._build_step_sheet(wb, step_data)

        # Sheet 8: Top15 最慢构建
        if run_breakdown:
            self._build_run_breakdown_sheet(wb, run_breakdown)

        # Sheet 9: 构建时间线
        if timeline:
            self._build_timeline_sheet(wb, timeline)

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()

    def _build_overview_sheet(self, wb: Workbook, workflow_name: str,
                               stats: dict, evaluation: dict | None):
        ws = wb.active
        ws.title = "综合概览"
        ws.sheet_properties.tabColor = "2E75B6"

        ws.merge_cells("A1:F1")
        ws["A1"] = f"{workflow_name} 构建分析报告"
        ws["A1"].font = self.title_font
        ws["A1"].alignment = Alignment(horizontal="center")
        ws.row_dimensions[1].height = 35

        ws["A3"] = "基本统计"
        ws["A3"].font = self.section_font
        ws.merge_cells("A3:F3")

        stats_list = [
            ("生成时间", datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")),
            ("运行总数", str(stats.get("total", 0))),
            ("成功次数", str(stats.get("successful", 0))),
            ("失败次数", str(stats.get("failed", 0))),
            ("成功率", f"{stats.get('success_rate', 0):.1f}%"),
            ("平均执行耗时", fmt_duration(stats.get("avg_exec", 0))),
            ("最短执行耗时", fmt_duration(stats.get("min_exec", 0))),
            ("最长执行耗时", fmt_duration(stats.get("max_exec", 0))),
            ("耗时波动系数 (CV)", f"{stats.get('cv_exec', 0):.1f}%"),
            ("平均排队时间", fmt_duration(stats.get("avg_wait", 0))),
            ("平均墙钟总耗时", fmt_duration(stats.get("avg_wall", 0))),
            ("异常慢构建", str(stats.get("abnormal_count", 0))),
            ("严重排队次数", str(stats.get("long_wait_count", 0))),
        ]

        for i, (label, value) in enumerate(stats_list, 4):
            cell_l = ws.cell(row=i, column=1, value=label)
            cell_l.font = self.bold_font
            cell_l.border = self.thin_border
            cell_l.fill = self.gray_fill
            cell_v = ws.cell(row=i, column=2, value=value)
            cell_v.font = self.red_bold
            cell_v.border = self.thin_border

        if evaluation:
            eval_row = len(stats_list) + 6
            ws.cell(row=eval_row, column=1, value="AI 评估摘要").font = self.section_font
            ws.merge_cells(f"A{eval_row}:F{eval_row}")

            ai_stats = [
                ("可信度评分", f"{evaluation.get('trust_score', 0):.1f}/100"),
                ("可信度等级", evaluation.get("trust_level", "N/A")),
                ("是否生产级", "是" if evaluation.get("production_ready") else "否"),
                ("执行摘要", evaluation.get("executive_summary", "N/A")),
            ]
            for i, (label, value) in enumerate(ai_stats, eval_row + 1):
                ws.cell(row=i, column=1, value=label).font = self.bold_font
                ws.cell(row=i, column=1).border = self.thin_border
                ws.cell(row=i, column=2, value=value).font = self.normal_font
                ws.cell(row=i, column=2).border = self.thin_border

    def _build_runs_sheet(self, wb: Workbook, runs: list[dict]):
        self._build_sheet(
            wb, "构建记录明细",
            ["运行ID", "状态", "分支", "事件", "创建时间", "排队时间", "执行时间", "墙钟时间", "Job数"],
            runs,
            [
                ("github_run_id", self._s),
                ("status", self._s),
                ("branch", self._s),
                ("event", self._s),
                ("created_at", self._s),
                ("wait_duration", self._fmt),
                ("exec_duration", self._fmt),
                ("wall_duration", self._fmt),
                ("num_jobs", self._s),
            ],
            status_col=1,
            left_align_cols={"github_run_id", "branch", "event", "created_at"},
        )

    def _build_groups_sheet(self, wb: Workbook, groups: list[dict]):
        self._build_sheet(
            wb, "Job 层级数据",
            ["Group", "Target", "OS", "Phase", "状态", "总耗时(秒)", "格式化耗时", "架构", "Sub-Job", "Runner", "Steps数"],
            groups,
            [
                ("group_name", self._s),
                ("target", self._s),
                ("os_name", self._s),
                ("phase", self._s),
                ("status", self._s),
                ("total_duration", self._r2),
                ("total_duration", self._fmt),
                ("architecture", self._s),
                ("sub_job_name", self._s),
                ("runner_name", self._s),
                ("steps", lambda v: len(v) if isinstance(v, list) else 0),
            ],
            status_col=4,
            left_align_cols={"group_name", "sub_job_name"},
        )

    def _build_phase_sheet(self, wb: Workbook, phase_data: list[dict]):
        self._build_sheet(
            wb, "阶段耗时分析",
            ["阶段", "次数", "总耗时(s)", "平均耗时(s)", "最长耗时(s)", "格式化平均"],
            phase_data,
            [
                ("name", self._s),
                ("count", self._int),
                ("total", self._r2),
                ("avg", self._r2),
                ("max", self._r2),
                ("avg", self._fmt),
            ],
            left_align_cols={"name"},
        )

    def _build_target_sheet(self, wb: Workbook, target_data: list[dict]):
        self._build_sheet(
            wb, "目标耗时分析",
            ["目标", "次数", "平均耗时(s)", "最短(s)", "最长(s)", "总耗时(s)", "格式化平均"],
            target_data,
            [
                ("name", self._s),
                ("count", self._int),
                ("avg", self._r2),
                ("min", self._r2),
                ("max", self._r2),
                ("total", self._r2),
                ("avg", self._fmt),
            ],
            left_align_cols={"name"},
        )

    def _build_arch_sheet(self, wb: Workbook, arch_data: list[dict]):
        self._build_sheet(
            wb, "架构分布",
            ["架构", "次数", "总耗时(s)", "平均耗时(s)", "格式化平均"],
            arch_data,
            [
                ("name", self._s),
                ("count", self._int),
                ("total", self._r2),
                ("avg", self._r2),
                ("avg", self._fmt),
            ],
            left_align_cols={"name"},
        )

    def _build_step_sheet(self, wb: Workbook, step_data: list[dict]):
        self._build_sheet(
            wb, "慢Step Top20",
            ["Step名称", "执行次数", "总耗时(s)", "平均耗时(s)", "最长耗时(s)", "格式化平均"],
            step_data,
            [
                ("name", self._s),
                ("count", self._int),
                ("total", self._r2),
                ("avg", self._r2),
                ("max", self._r2),
                ("avg", self._fmt),
            ],
            left_align_cols={"name"},
        )

    def _build_run_breakdown_sheet(self, wb: Workbook, run_breakdown: list[dict]):
        self._build_sheet(
            wb, "最慢构建 Top15",
            ["运行ID", "日期", "状态", "排队(s)", "执行(s)", "墙钟(s)", "Job数", "格式化墙钟"],
            run_breakdown,
            [
                ("run_id", self._s),
                ("date", self._s),
                ("status", self._s),
                ("wait", self._r2),
                ("exec", self._r2),
                ("wall", self._r2),
                ("jobs", self._int),
                ("wall", self._fmt),
            ],
            status_col=2,
            left_align_cols={"run_id", "date"},
        )

    def _build_timeline_sheet(self, wb: Workbook, timeline: list[dict]):
        self._build_sheet(
            wb, "构建时间线",
            ["日期", "完整时间", "运行ID", "状态", "排队(s)", "执行(s)", "墙钟(s)", "Job数"],
            timeline,
            [
                ("date", self._s),
                ("date_full", self._s),
                ("run_id", self._s),
                ("status", self._s),
                ("wait", self._r2),
                ("exec", self._r2),
                ("wall", self._r2),
                ("jobs", self._int),
            ],
            status_col=3,
            left_align_cols={"date", "date_full", "run_id"},
        )

    @staticmethod
    async def generate_pdf(
        workflow_name: str,
        stats: dict[str, Any],
        runs: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        failed_runs: list[dict[str, Any]] | None = None,
        evaluation: dict[str, Any] | None = None,
        timeline: list[dict[str, Any]] | None = None,
        phase_data: list[dict[str, Any]] | None = None,
        target_data: list[dict[str, Any]] | None = None,
        arch_data: list[dict[str, Any]] | None = None,
        step_data: list[dict[str, Any]] | None = None,
        run_breakdown: list[dict[str, Any]] | None = None,
    ) -> tuple[bytes, str]:
        """生成 PDF 分析报告"""
        from jinja2 import Template
        from weasyprint import HTML

        import importlib.resources as resources

        template_path = resources.files("release_management").joinpath("pdf_template.html")
        template_source = template_path.read_text(encoding="utf-8")
        template = Template(template_source)

        def _status_label(status: str) -> str:
            mapping = {
                "success": "成功",
                "failure": "失败",
                "cancelled": "取消",
                "skipped": "跳过",
                "running": "运行中",
                "action_required": "需处理",
            }
            return mapping.get(str(status).lower(), status)

        def _bar(value: float, max_val: float, width: int = 30, fill_char: str = "▓", empty_char: str = "") -> str:
            """Generate a Unicode bar chart string for WeasyPrint."""
            if max_val <= 0:
                return ""
            filled = round(value / max_val * width)
            filled = max(0, min(filled, width))
            return f'<span class="bar-on">{fill_char * filled}</span><span class="bar-off">{empty_char * max(0, width - filled)}</span>'

        def _stacked_bar(wait: float, exec_val: float, total_max: float, width: int = 30) -> str:
            """Generate a stacked bar: wait (orange) + exec (blue) for timeline."""
            if total_max <= 0:
                return "░" * width
            wait_count = round(wait / total_max * width)
            exec_count = round(exec_val / total_max * width)
            total = min(wait_count + exec_count, width)
            wait_count = min(wait_count, total)
            exec_count = total - wait_count
            empty = width - total
            return (f'<span class="bar-wait">{"▓" * wait_count}</span>'
                    f'<span class="bar-exec">{"█" * exec_count}</span>'
                    f'<span class="bar-off">{"░" * empty}</span>')

        def _pie_segments(items: list[dict], total: float, width: int = 30) -> str:
            """Generate a pie-like proportional bar from items with 'total' key."""
            if not total:
                return ""
            hex_colors = ["#2E75B6", "#5ada7a", "#fd9a31", "#e06024", "#9f7aea", "#17a2b8"]
            parts = []
            used = 0
            for i, item in enumerate(items):
                val = item.get("total", 0)
                seg_len = max(1, round(val / total * width))
                if used + seg_len > width:
                    seg_len = width - used
                if seg_len <= 0:
                    continue
                clr = hex_colors[i % len(hex_colors)]
                name = item.get("name", "")
                parts.append(f'<span style="color:{clr};" title="{name}">▓</span>' * seg_len)
                used += seg_len
            return "".join(parts)

        # Pre-compute max values for bar chart scaling (avoid Jinja2 map|max issues)
        phase_list = phase_data or []
        target_list = target_data or []
        step_list = step_data or []
        timeline_list = timeline or []
        arch_list = arch_data or []

        max_phase_total = max((p.get("total", 0) for p in phase_list), default=0)
        max_target_avg = max((t.get("avg", 0) for t in target_list), default=0)
        max_step_total = max((s.get("total", 0) for s in step_list), default=0)
        max_arch_total = max((a.get("total", 0) for a in arch_list), default=0)
        phase_total_all = sum(p.get("total", 0) for p in phase_list)
        max_timeline_wall = max((t.get("wall", 0) for t in timeline_list), default=0)

        html_content = template.render(
            workflow_name=workflow_name,
            generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            stats=stats,
            runs=runs[:20],
            failed_runs=(failed_runs or [])[:20],
            groups=groups[:50],
            evaluation=evaluation,
            timeline=timeline_list,
            phase_data=phase_list,
            target_data=target_list,
            arch_data=arch_list,
            step_data=step_list,
            run_breakdown=run_breakdown or [],
            max_phase_total=max_phase_total,
            max_target_avg=max_target_avg,
            max_step_total=max_step_total,
            max_arch_total=max_arch_total,
            phase_total_all=phase_total_all,
            max_timeline_wall=max_timeline_wall,
            fmt_duration=fmt_duration,
            status_label=_status_label,
            bar=_bar,
            stacked_bar=_stacked_bar,
            pie_segments=_pie_segments,
            max=max,
            min=min,
        )

        pdf_bytes = HTML(string=html_content).write_pdf()
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        filename = f"release_build_report_{ts}.pdf"
        return pdf_bytes, filename
