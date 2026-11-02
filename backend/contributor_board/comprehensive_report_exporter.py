"""综合 PDF 报告导出 — 含封面、统计卡、四象限分布、Top N 明细 + AI 逐人评价、整体分析建议"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from contributor_board.constants import QUADRANT_LABELS

logger = logging.getLogger(__name__)

TOP_N_DIMENSIONS_SHOW = [
    ("commit_count", "提交数"),
    ("code_files", "代码文件"),
    ("doc_files", "文档文件"),
    ("pr_merged", "PR 合并"),
    ("pr_total", "PR 总量"),
    ("active_days_90", "活跃天数(90d)"),
    ("span_days", "贡献持续期"),
    ("review_count", "检视数"),
    ("approval_count", "审批数"),
]


async def export_comprehensive_pdf(
    db: Any,
    top_n_list: list[dict[str, Any]],
    period_desc: str,
    statistics: dict[str, Any],
    ai_evaluations: dict[str, str] | None = None,
    recommendations: list[str] | None = None,
    risk_level: str | None = None,
    confidence_overall: float | None = None,
    repo_name: str | None = None,
) -> tuple[bytes, str]:
    """
    生成 PDF 综合分析报告。

    Args:
        db: AsyncSession (用于获取 LLM 配置，可选)
        top_n_list: Top N 贡献者数据，每项需包含 login/score/tier/role 等
        period_desc: 时间范围文本
        statistics: 统计 dict，含 total_contributors / score_avg / score_max /
            quadrant_distribution / dimension_averages
        ai_evaluations: {login: evaluation_text} 可选 AI 逐人评价
        recommendations: AI 决策建议列表
        risk_level: risk_level 可选
        confidence_overall: confidence_overall 可选
        repo_name: 仓库名

    Returns: (pdf_bytes, filename)
    """
    from jinja2 import Template
    from weasyprint import HTML

    template = Template(COMPREHENSIVE_PDF_TEMPLATE)
    quadrant_dist = statistics.get("quadrant_distribution", {})
    dim_averages = statistics.get("dimension_averages", {})

    html_content = template.render(
        title="vLLM Ascend 贡献者综合分析报告",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        period_desc=period_desc,
        repo_name=repo_name or "",
        risk_level=_risk_label(risk_level),
        risk_color=_risk_color(risk_level),
        confidence_overall=(round(confidence_overall * 100, 0) if confidence_overall else None),
        total=statistics.get("total_contributors", len(top_n_list)),
        score_avg=statistics.get("score_avg", 0),
        score_max=statistics.get("score_max", 0),
        score_min=statistics.get("score_min", 0),
        top_n=top_n_list,
        quadrant_dist=quadrant_dist,
        quadrant_labels=QUADRANT_LABELS,
        dim_averages=dim_averages,
        ai_evaluations=ai_evaluations or {},
        recommendations=recommendations or [],
        dimension_items=TOP_N_DIMENSIONS_SHOW,
    )

    pdf_bytes = HTML(string=html_content).write_pdf()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"comprehensive_report_{ts}.pdf"
    return pdf_bytes, filename


def _risk_label(risk: str | None) -> str:
    if not risk:
        return "N/A"
    return {"low": "低", "medium": "中", "high": "高"}.get(risk, risk)


def _risk_color(risk: str | None) -> str:
    if not risk:
        return "#999"
    return {"low": "#52c41a", "medium": "#faad14", "high": "#ff4d4f"}.get(risk, "#999")


COMPARE_TEMPLATE_VAR = "_x"


COMPARE_PDF_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4;
    margin: 2cm;
    @bottom-center { content: "vLLM Ascend Dashboard — Page " counter(page) " of " counter(pages); font-size: 9px; color: #999; }
    @top-right { content: "comprehensive report"; font-size: 8px; color: #bbb; }
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: "Noto Sans CJK SC", "WenQuanYi Micro Hei", sans-serif; font-size: 11px; color: #333; }
  .cover { page-break-after: always; text-align: center; padding-top: 40mm; }
  .cover h1 { font-size: 28px; color: #1a1a2e; border-bottom: 4px solid #722ed1; padding-bottom: 14px; }
  .cover .meta { margin-top: 18px; color: #666; font-size: 13px; line-height: 2; }
  .cover .meta strong { color: #722ed1; }
  .cover .badge { display: inline-block; padding: 4px 14px; border-radius: 12px; font-size: 12px; margin-top: 10px; color: white; }
  h2 { font-size: 15px; margin: 20px 0 10px; color: #1a1a2e; border-left: 4px solid #722ed1; padding-left: 10px; }
  h3 { font-size: 13px; margin: 14px 0 8px; color: #444; }
  .stats-grid { display: flex; gap: 12px; margin: 14px 0; flex-wrap: wrap; }
  .stat-card { flex: 1; min-width: 120px; background: linear-gradient(135deg, #f5f0ff 0%, #ebe4ff 100%); border-radius: 8px; padding: 14px; text-align: center; border: 1px solid #e0d8f5; }
  .stat-card .num { font-size: 26px; font-weight: bold; color: #722ed1; }
  .stat-card .label { font-size: 11px; color: #666; margin-top: 4px; }
  .stat-card .subval { font-size: 10px; color: #999; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 10px; }
  th { background: #722ed1; color: white; padding: 6px 5px; text-align: center; font-weight: bold; }
  td { padding: 5px 4px; border: 1px solid #e8e8e8; text-align: center; }
  td.left { text-align: left; padding-left: 8px; }
  tr:nth-child(even) { background: #faf9fd; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 9px; color: white; font-weight: bold; }
  .tag-INCENTIVE { background: #52c41a; }
  .tag-IMPROVE { background: #faad14; }
  .tag-ENCOURAGE { background: #1890ff; }
  .tag-REPLACE { background: #ff4d4f; }
  .tag-unknown { background: #999; }
  .quadrant-row { display: flex; gap: 8px; margin: 10px 0; }
  .quadrant-bar { flex: 1; background: #f5f5f5; border-radius: 4px; height: 28px; position: relative; overflow: hidden; }
  .quadrant-bar-fill { position: absolute; top: 0; left: 0; height: 100%; transition: width 0.3s; }
  .quadrant-bar-label { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 10px; font-weight: bold; color: white; text-shadow: 0 1px 2px rgba(0,0,0,0.5); }
  .eval-card { border: 1px solid #e8e8e8; border-radius: 6px; padding: 10px; margin: 8px 0; background: #fcfcfc; }
  .eval-card .eval-header { font-weight: bold; font-size: 12px; color: #333; margin-bottom: 4px; }
  .eval-card .eval-body { font-size: 11px; color: #555; line-height: 1.6; }
  .rec-item { display: flex; gap: 10px; align-items: flex-start; padding: 10px; background: #f5f0ff; border-radius: 6px; margin: 8px 0; }
  .rec-num { width: 24px; height: 24px; border-radius: 50%; background: linear-gradient(135deg, #722ed1, #b745d3); color: white; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: bold; flex-shrink: 0; }
  .rec-text { flex: 1; font-size: 11px; line-height: 1.6; }
  .footer { margin-top: 24px; text-align: center; color: #999; font-size: 9px; border-top: 1px solid #eee; padding-top: 8px; }
  .page-break { page-break-before: always; }
</style>
</head>
<body>
<!-- 封面 -->
<div class="cover">
  <h1>{{ title }}</h1>
  <div class="meta">
    仓库: <strong>{{ repo_name or '全部' }}</strong><br/>
    时间范围: <strong>{{ period_desc }}</strong><br/>
    生成时间: {{ generated_at }}
  </div>
  {% if risk_color %}
  <div>
    <span class="badge" style="background: {{ risk_color }};">风险等级: {{ risk_level }}</span>
    {% if confidence_overall %}
    <span class="badge" style="background: #722ed1; margin-left: 8px;">置信度: {{ confidence_overall }}%</span>
    {% endif %}
  </div>
  {% endif %}
</div>

<!-- 第 2 页: 统计摘要 + 四象限分布 -->
<h2>统计摘要</h2>
<div class="stats-grid">
  <div class="stat-card"><div class="num">{{ total }}</div><div class="label">贡献者总数</div></div>
  <div class="stat-card"><div class="num">{{ "%.1f"|format(score_avg|default(0)) }}</div><div class="label">平均分</div><div class="subval">最高 {{ "%.1f"|format(score_max|default(0)) }} / 最低 {{ "%.1f"|format(score_min|default(0)) }}</div></div>
  {% set qd = quadrant_dist %}
  {% for q in ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE'] %}
    {% if qd.get(q) %}
    <div class="stat-card"><div class="num">{{ qd[q] }}</div><div class="label">{{ quadrant_labels.get(q, q) }}</div></div>
    {% endif %}
  {% endfor %}
</div>

{% if dim_averages %}
<h3>7 维平均指标（全体贡献者）</h3>
<div class="stats-grid">
  {% for (key, label) in dimension_items %}
    {% if dim_averages.get(key) is not none %}
    <div class="stat-card">
      <div class="num" style="font-size:18px;">{{ "%.1f"|format(dim_averages[key]) if dim_averages[key] is number else dim_averages[key] }}</div>
      <div class="label">{{ label }}</div>
    </div>
    {% endif %}
  {% endfor %}
</div>
{% endif %}

<h2>四象限分布</h2>
{% set qd = quadrant_dist %}
{% set total_q = qd.values()|list|sum or 1 %}
<div class="quadrant-row">
  {% for q in ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE'] %}
  {% set c = qd.get(q, 0) %}
  <div class="quadrant-bar" style="flex: {{ (c or 1) }};">
    <div class="quadrant-bar-fill" style="width: {{ (c / total_q * 100)|int }}%; background: {% if q == 'INCENTIVE' %}#52c41a{% elif q == 'ENCOURAGE' %}#1890ff{% elif q == 'IMPROVE' %}#faad14{% else %}#ff4d4f{% endif %};"></div>
    <div class="quadrant-bar-label">{{ quadrant_labels.get(q, q) }}: {{ c }}人</div>
  </div>
  {% endfor %}
</div>

<!-- 第 3 页: Top N 明细 + AI 逐人评价 -->
<div class="page-break"></div>
<h2>Top {{ top_n|length }} 贡献者明细</h2>
<table>
  <thead>
    <tr>
      <th>排名</th><th>用户</th><th>分数</th><th>角色</th><th>象限</th>
      <th>提交</th><th>代码文件</th><th>PR</th>
      <th>活跃(90d)</th><th>持续期</th><th>检视</th>
    </tr>
  </thead>
  <tbody>
    {% for p in top_n %}
    <tr>
      <td style="font-weight: bold; {% if loop.index <= 3 %}color:#fa8c16;{% endif %}">
        #{{ loop.index }}
      </td>
      <td class="left">{{ p.login }}</td>
      <td style="font-weight: bold;">{{ "%.1f"|format(p.score|default(0)) }}</td>
      <td>{{ p.role|default('developer') }}</td>
      <td><span class="tag tag-{{ p.tier|default('unknown') }}">{{ quadrant_labels.get(p.tier, '未知') }}</span></td>
      <td>{{ p.commit_count|default(0) }}</td>
      <td>{{ p.code_files|default(0) }}</td>
      <td>{{ p.pr_merged|default(0) }}/{{ p.pr_total|default(0) }}</td>
      <td>{{ p.active_days_90|default(0) }}</td>
      <td>{{ p.span_days|default(0) }}</td>
      <td>{{ p.review_count|default(0) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<h2>AI 逐人客观评价</h2>
{% if ai_evaluations %}
  {% for p in top_n %}
    {% if ai_evaluations.get(p.login) %}
    <div class="eval-card">
      <div class="eval-header">{{ loop.index }}. {{ p.login }} ({{ "%.1f"|format(p.score|default(0)) }} 分)</div>
      <div class="eval-body">{{ ai_evaluations[p.login] }}</div>
    </div>
    {% endif %}
  {% endfor %}
{% else %}
  <p style="color:#999;font-size:11px;">（未启用 AI 评价）</p>
{% endif %}

<!-- 第 4 页: 决策建议 -->
<div class="page-break"></div>
<h2>决策建议</h2>
{% if recommendations %}
  {% for rec in recommendations %}
  <div class="rec-item">
    <div class="rec-num">{{ loop.index }}</div>
    <div class="rec-text">{{ rec }}</div>
  </div>
  {% endfor %}
{% else %}
  <p style="color:#999;font-size:11px;">（暂无决策建议）</p>
{% endif %}

<div class="footer">
  本报告由 vLLM Ascend Dashboard 自动生成 | 数据来源: GitHub API | 评分模型: 7 维熵权 + LLM 辅助
</div>
</body>
</html>
"""
