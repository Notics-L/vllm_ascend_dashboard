"""贡献者评分报告 PDF 导出引擎"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any

from contributor_board.constants import QUADRANT_LABELS

logger = logging.getLogger(__name__)


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
        all_scores=sorted_scores,
        quadrant_dist=quadrant_dist,
        quadrant_labels=QUADRANT_LABELS,
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
<div class="meta">生成时间: {{ generated_at }}{% if version.get('version') %} | 版本: {{ version.get('version') }}  ({% if version.get('method') == 'auto' %}自动算权{% elif version.get('method') == 'ai' %}AI 评权{% else %}手动设置{% endif %}{% endif %}</div>

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

<h2>全量贡献者评分排行 (共 {{ total }} 人)</h2>
<p style="font-size:10px;color:#999;margin-bottom:8px;">全量导出：所有贡献者按分数从高到低排序</p>
<table>
  <thead>
    <tr><th>排名</th><th>用户</th><th>分数</th><th>错误分</th><th>象限</th></tr>
  </thead>
  <tbody>
    {% for s in all_scores %}
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
