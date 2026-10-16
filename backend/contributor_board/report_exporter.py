"""贡献者评分报告导出引擎

支持导出 Excel (xlsx) 和 PDF 两种格式。
"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_QUADRANT_LABELS: dict[str, str] = {
    "INCENTIVE": "激励区",
    "IMPROVE": "改进区",
    "ENCOURAGE": "鼓励区",
    "REPLACE": "置换区",
    "unknown": "未知",
}


async def export_excel(
    db: Any,
    scores: list[dict[str, Any]],
    version_id: int | None = None,
) -> tuple[bytes, str]:
    """
    生成 Excel 贡献者评分报表。

    Returns: (xlsx_bytes, filename)
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "贡献者评分"

    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font_white = Font(bold=True, color="FFFFFF", size=11)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    center = Alignment(horizontal="center", vertical="center")

    columns = [
        ("排名", 60),
        ("用户", 150),
        ("贡献分数", 100),
        ("错误分", 80),
        ("象限", 100),
    ]

    headers = [c[0] for c in columns]
    for col_idx, (header, width) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font_white
        cell.fill = header_fill
        cell.alignment = center
        cell.border = thin_border
        ws.column_dimensions[cell.column_letter].width = width

    sorted_scores = sorted(scores, key=lambda s: s.get("score", 0), reverse=True)

    quadrant_fills = {
        "INCENTIVE": PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"),
        "ENCOURAGE": PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid"),
        "IMPROVE": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
        "REPLACE": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"),
    }

    for row_idx, s in enumerate(sorted_scores, 2):
        quadrant_raw = s.get("quadrant", "unknown")
        quadrant_label = _QUADRANT_LABELS.get(quadrant_raw, quadrant_raw)

        values = [
            row_idx - 1,
            s.get("login", ""),
            s.get("score", 0),
            s.get("error_score", 0),
            quadrant_label,
        ]

        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            cell.alignment = center if col_idx in (1, 3, 4, 5) else Alignment(vertical="center")

            if quadrant_raw in quadrant_fills:
                cell.fill = quadrant_fills[quadrant_raw]

    summary_sheet = wb.create_sheet(title="汇总统计")

    total = len(sorted_scores)
    if sorted_scores:
        total_score = sum(s.get("score", 0) for s in sorted_scores)
        avg_score = round(total_score / total, 2)
    else:
        total_score = 0
        avg_score = 0

    quadrant_dist: dict[str, int] = {}
    for s in sorted_scores:
        q = s.get("quadrant", "unknown")
        quadrant_dist[q] = quadrant_dist.get(q, 0) + 1

    summary_data = [
        ["统计项目", "数值"],
        ["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["版本 ID", version_id or "N/A"],
        ["贡献者总数", total],
        ["总分数", round(total_score, 2)],
        ["平均分数", avg_score],
        [],
        ["四象限分布", ""],
    ]

    for q_key in ["INCENTIVE", "ENCOURAGE", "IMPROVE", "REPLACE"]:
        if q_key in quadrant_dist:
            summary_data.append([_QUADRANT_LABELS[q_key], quadrant_dist[q_key]])

    summary_headers_font = Font(bold=True, size=11)
    for row_idx, row_data in enumerate(summary_data, 1):
        for col_idx, val in enumerate(row_data, 1):
            cell = summary_sheet.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            if row_idx == 1:
                cell.font = summary_headers_font
                cell.fill = header_fill
                cell.font = header_font_white
                cell.alignment = center
            elif row_idx == 4 or str(row_idx).endswith("9"):
                cell.font = Font(bold=True)

    summary_sheet.column_dimensions["A"].width = 20
    summary_sheet.column_dimensions["B"].width = 15

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"contributor_report_{ts}.xlsx"
    return buf.read(), filename


async def export_pdf(
    db: Any,
    scores: list[dict[str, Any]],
    version_info: dict[str, Any] | None = None,
    statistics: dict[str, Any] | None = None,
) -> tuple[bytes, str]:
    """
    生成 PDF 贡献者评分报告。

    Returns: (pdf_bytes, filename)
    """
    from jinja2 import Template
    from weasyprint import HTML

    sorted_scores = sorted(scores, key=lambda s: s.get("score", 0), reverse=True)
    top_n = sorted_scores[:20]

    quadrant_dist: dict[str, int] = {}
    for s in sorted_scores:
        q = s.get("quadrant", "unknown")
        quadrant_dist[q] = quadrant_dist.get(q, 0) + 1

    template = Template(PDF_TEMPLATE)

    html_content = template.render(
        title="vLLM Ascend 贡献者评分报告",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        version=version_info or {},
        statistics=statistics or {},
        total=len(sorted_scores),
        top_n=top_n,
        quadrant_dist=quadrant_dist,
        quadrant_labels=_QUADRANT_LABELS,
    )

    pdf_bytes = HTML(string=html_content).write_pdf()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"contributor_report_{ts}.pdf"
    return pdf_bytes, filename


PDF_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4;
    margin: 2cm;
    @bottom-center { content: "Page " counter(page) " of " counter(pages); font-size: 9px; color: #999; }
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: "Noto Sans CJK SC", "WenQuanYi Micro Hei", sans-serif; font-size: 12px; color: #333; }
  h1 { font-size: 20px; text-align: center; margin: 20px 0; color: #1a1a2e; border-bottom: 3px solid #722ed1; padding-bottom: 10px; }
  h2 { font-size: 14px; margin: 16px 0 8px; color: #722ed1; border-left: 3px solid #722ed1; padding-left: 8px; }
  .meta { text-align: center; color: #666; margin-bottom: 20px; font-size: 11px; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0 16px; font-size: 10px; }
  th { background: #722ed1; color: white; padding: 6px 4px; text-align: center; font-weight: bold; }
  td { padding: 4px; border: 1px solid #ddd; text-align: center; }
  tr:nth-child(even) { background: #f9f9f9; }
  .summary-grid { display: flex; gap: 16px; margin: 12px 0; }
  .summary-card { flex: 1; background: #f5f0ff; border-radius: 6px; padding: 12px; text-align: center; }
  .summary-card .num { font-size: 24px; font-weight: bold; color: #722ed1; }
  .summary-card .label { font-size: 10px; color: #666; margin-top: 4px; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 9px; color: white; font-weight: bold; }
  .tag-INCENTIVE { background: #52c41a; }
  .tag-IMPROVE { background: #faad14; }
  .tag-ENCOURAGE { background: #1890ff; }
  .tag-REPLACE { background: #ff4d4f; }
  .tag-unknown { background: #999; }
  .footer { margin-top: 24px; text-align: center; color: #999; font-size: 9px; border-top: 1px solid #eee; padding-top: 8px; }
</style>
</head>
<body>
<h1>{{ title }}</h1>
<div class="meta">生成时间: {{ generated_at }}{% if version.get('version') %} | 版本: {{ version.version }} ({{ version.method }}){% endif %}</div>

<h2>统计摘要</h2>
<div class="summary-grid">
  <div class="summary-card">
    <div class="num">{{ total }}</div>
    <div class="label">贡献者总数</div>
  </div>
  <div class="summary-card">
    <div class="num">{{ "%.1f"|format(statistics.get('score_mean', 0)) }}</div>
    <div class="label">平均分数</div>
  </div>
  {% set qd = statistics.get('quadrant_distribution', {}) %}
  {% for q in ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE'] %}
  {% if qd.get(q) %}
  <div class="summary-card">
    <div class="num">{{ qd[q] }}</div>
    <div class="label">{{ quadrant_labels.get(q, q) }}</div>
  </div>
  {% endif %}
  {% endfor %}
</div>

<h2>Top 20 贡献者</h2>
<table>
  <thead>
    <tr><th>排名</th><th>用户</th><th>分数</th><th>错误分</th><th>象限</th></tr>
  </thead>
  <tbody>
    {% for s in top_n %}
    <tr>
      <td>{{ loop.index }}</td>
      <td style="text-align:left;padding-left:6px;">{{ s.login }}</td>
      <td>{{ "%.1f"|format(s.score|default(0)) }}</td>
      <td>{{ s.error_score|default(0) }}</td>
      <td><span class="tag tag-{{ s.quadrant|default('unknown') }}">{{ quadrant_labels.get(s.quadrant, '未知') }}</span></td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<div class="footer">
  本报告由 vLLM Ascend Dashboard 自动生成 | 数据来源: GitLab API
</div>
</body>
</html>
"""
