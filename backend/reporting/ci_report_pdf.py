"""CI 每日综合报错报告 PDF 导出。

使用 jinja2 渲染 HTML 模板（内嵌 base64 图表），再经 weasyprint 转为 PDF。
报告以「失败详情」为核心：全绿横幅 / 失败核心摘要 / 失败明细表（问题范畴 +
原始报错线索行）+ 图表；全量运行与耗时统计弱化置于文末。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import jinja2

from reporting.ci_daily_report_builder import CONCLUSION_ZH, format_conclusion_zh

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 图表 key：与 ci_report_charts.render_report_charts 返回的 dict 对应
CHART_KEYS = (
    "result_distribution",
    "success_rate_trend",
    "failure_by_category",
    "failure_by_branch",
    "failure_by_hardware",
    "failure_by_model",
    "processing_status",
    "all_green",
)

# AI 分析状态 -> 中文
AI_STATUS_ZH: dict[str, str] = {
    "completed": "已完成",
    "analyzing": "分析中",
    "pending": "待分析",
    "reused": "已复用",
    "failed": "分析失败",
    "degraded": "已降级",
    "cancelled": "已取消",
    "raw_log_collected": "已收集",
}
_AI_COLORS: dict[str, str] = {
    "completed": "green",
    "analyzing": "amber",
    "pending": "amber",
    "reused": "gray",
    "failed": "red",
    "degraded": "gray",
    "cancelled": "gray",
    "raw_log_collected": "gray",
}


def _ai_status_zh(status: str | None) -> str:
    if not status:
        return "未分析"
    return AI_STATUS_ZH.get(status, status)


def _ai_status_color(status: str | None) -> str:
    if not status:
        return "gray"
    return _AI_COLORS.get(status, "gray")


# 失败详情表格列（精简版：13 列，PDF 横向 A4）
FAILURE_COLUMNS: tuple[str, str] = (
    ("时间（北京时间）", "report_datetime"),
    ("分支", "source_branch"),
    ("Workflow", "workflow_name"),
    ("Job", "job_name"),
    ("模型", "model_name"),
    ("测试内容", "test_yaml"),
    ("硬件", "hardware"),
    ("结论", "conclusion_zh"),
    ("责任人", "owner"),
    ("耗时", "duration_text"),
    ("问题范畴", "problem_category"),
    ("AI 分析", "analysis_status"),
    ("原始报错线索", "error_line"),
)

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"utf-8\"/>
<title>CI 每日综合报告 {{ window.start_date }}</title>
<style>
  @page { size: A4 landscape; margin: 12mm 10mm 14mm 10mm; }
  * { box-sizing: border-box; }
  body { font-family: 'Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', sans-serif;
         font-size: 9.5px; color: #1f2937; line-height: 1.4; }
  h1 { font-size: 18px; margin: 0 0 4px 0; }
  h2 { font-size: 13px; margin: 14px 0 6px 0; color: #111827;
       border-left: 3px solid #2563eb; padding-left: 8px; }
  .muted { color: #6b7280; font-size: 10px; }
  .all-green { background: linear-gradient(90deg,#22c55e,#16a34a); color: #fff;
               border-radius: 8px; padding: 8px 12px; margin: 10px 0;
               font-size: 15px; font-weight: 600; }
  .banner { border-radius: 8px; padding: 8px 12px; margin: 10px 0; font-size: 12px; }
  .banner.red { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
  .cards { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
  .card { border: 1px solid #e5e7eb; border-radius: 6px; padding: 6px 10px;
          min-width: 80px; flex: 1 0 auto; }
  .card .k { color: #6b7280; font-size: 9.5px; }
  .card .v { font-size: 16px; font-weight: 700; margin-top: 2px; }
  .card .v.red { color: #dc2626; }
  .card .v.green { color: #16a34a; }
  .card .v.amber { color: #d97706; }
  table { width: 100%; border-collapse: collapse; margin: 6px 0; }
  th, td { border: 1px solid #e5e7eb; padding: 3px 5px; text-align: left;
           vertical-align: top; word-break: break-word; }
  th { background: #1e40af; color: #fff; font-weight: 600; font-size: 9px;
       position: sticky; top: 0; }
  tr:nth-child(even) td { background: #f8fafc; }
  .nowrap { white-space: nowrap; }
  .err { font-family: Consolas, 'Courier New', monospace; font-size: 8.5px;
         color: #991b1b; word-break: break-all; line-height: 1.4; }
  .err .brief { color: #1e40af; font-weight: 600; }
  .tag { display: inline-block; border-radius: 3px; padding: 0 4px; font-size: 9px; }
  .tag.red { background: #fee2e2; color: #991b1b; }
  .tag.green { background: #dcfce7; color: #166534; }
  .tag.amber { background: #fef3c7; color: #92400e; }
  .tag.gray { background: #f3f4f6; color: #374151; }
  .tag.blue { background: #dbeafe; color: #1e40af; }
  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 8px 0; }
  .charts-grid .chart { border: 1px solid #e5e7eb; border-radius: 6px;
                        padding: 4px; text-align: center; }
  .charts-grid .full { grid-column: 1 / -1; }
  .charts-grid img { max-width: 100%; height: auto; }
  .footer { margin-top: 14px; color: #9ca3af; font-size: 9px; text-align: center;
            border-top: 1px solid #e5e7eb; padding-top: 6px; }
</style>
</head>
<body>
  <h1>CI 每日综合报错报告</h1>
  <div class=\"muted\">
    窗口：{{ window.start_beijing }} ～ {{ window.end_beijing }}（北京时间）
    &nbsp;｜&nbsp; 生成于 {{ window.generated_at }}
    {% if window.hardware and window.hardware != 'all' %} &nbsp;｜&nbsp; 硬件：{{ window.hardware }}{% endif %}
    {% if window.branch and window.branch != 'all' %} &nbsp;｜&nbsp; 分支：{{ window.branch }}{% endif %}
  </div>

  {% if all_green %}
  <div class=\"all-green\">✓ 时间窗口内无失败 —— 流水线全绿</div>
  {% else %}
  <div class=\"banner red\">
    共 <b>{{ summary.total_failed }}</b> 条失败记录
    {% if summary.top_failure_category %}
    &nbsp;｜&nbsp; 主要问题范畴：<b>{{ summary.top_failure_category }}</b>
    （{{ summary.top_failure_category_count }} 次）{% endif %}
    &nbsp;｜&nbsp; 未处理 <b>{{ summary.unprocessed_failures }}</b> 条
    {% if charts.get('failure_banner') %}
    <img src=\"{{ charts.get('failure_banner') }}\" style=\"display:block;margin-top:8px;max-width:100%\"/>
    {% endif %}
  </div>

  <h2>失败核心摘要</h2>
  <div class=\"cards\">
    <div class=\"card\"><div class=\"k\">失败记录</div><div class=\"v red\">{{ summary.total_failed }}</div></div>
    <div class=\"card\"><div class=\"k\">主要问题范畴</div><div class=\"v amber\">{{ summary.top_failure_category or '—' }}</div></div>
    <div class=\"card\"><div class=\"k\">涉及分支</div><div class=\"v\">{{ summary.failure_by_branch|length }}</div></div>
    <div class=\"card\"><div class=\"k\">涉及硬件</div><div class=\"v\">{{ summary.failure_by_hardware|length }}</div></div>
    <div class=\"card\"><div class=\"k\">涉及模型/用例</div><div class=\"v\">{{ summary.failure_by_model|length }}</div></div>
    <div class=\"card\"><div class=\"k\">未处理</div><div class=\"v red\">{{ summary.unprocessed_failures }}</div></div>
  </div>

  <h2>失败维度分布</h2>
  <div class="charts-grid">
    {% if charts.get('failure_by_category') %}<div class="chart"><img src="{{ charts.get('failure_by_category') }}"/></div>{% endif %}
    {% if charts.get('failure_by_branch') %}<div class="chart"><img src="{{ charts.get('failure_by_branch') }}"/></div>{% endif %}
    {% if charts.get('failure_by_hardware') %}<div class="chart"><img src="{{ charts.get('failure_by_hardware') }}"/></div>{% endif %}
    {% if charts.get('failure_by_model') %}<div class="chart"><img src="{{ charts.get('failure_by_model') }}"/></div>{% endif %}
  </div>

  <h2>失败明细（{{ failures|length }}</h2>
  <table>
    <thead><tr>
      {% for label, key in failure_columns %}<th>{{ label }}</th>{% endfor %}
    </tr></thead>
    <tbody>
      {% for f in failures %}
      <tr>
        <td class="nowrap">{{ f.report_datetime or f.report_date }}</td>
        <td><span class="tag blue">{{ f.source_branch }}</span></td>
        <td class="nowrap">{{ f.workflow_name }}</td>
        <td>{{ f.job_name }}</td>
        <td>{{ f.model_name or (f.test_model or '—') }}</td>
        <td>{{ f.test_yaml or '—' }}</td>
        <td>{{ f.hardware }}</td>
        <td><span class="tag {{ 'red' if (f.conclusion or '').lower() in ('failure','timed_out','startup_failure') else 'gray' }}">{{ f.conclusion_zh or (f.conclusion or '—') }}</span></td>
        <td>{{ f.owner or '—' }}</td>
        <td class="nowrap">{{ f.duration_text or '—' }}</td>
        <td><span class="tag amber">{{ f.problem_category or '未定界' }}</span></td>
        <td>
          <span class="tag {{ ai_color(f.analysis_status) }}">{{ ai_zh(f.analysis_status) }}</span>
        </td>
        <td class="err">
          {% if f.error_brief %}
            <div class="brief">{{ f.error_brief }}</div>
          {% endif %}
          {% if f.raw_log_lines %}
            {% for ln in f.raw_log_lines[:3] %}<div>· {{ ln }}</div>{% endfor %}
            {% if f.raw_log_lines | length > 3 %}<div style="color:#8a8f98">... 共 {{ f.raw_log_lines|length }} 行</div>{% endif %}
          {% elif f.error_line %}
            {{ f.error_line }}
          {% else %}
            —
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}

  <h2>整体运行（次级信息）</h2>
  <div class=\"cards\">
    <div class=\"card\"><div class=\"k\">总运行数</div><div class=\"v\">{{ summary.total_runs }}</div></div>
    <div class=\"card\"><div class=\"k\">通过</div><div class=\"v green\">{{ summary.passed_runs }}</div></div>
    <div class=\"card\"><div class=\"k\">失败</div><div class=\"v red\">{{ summary.failed_runs }}</div></div>
    <div class=\"card\"><div class=\"k\">成功率</div><div class=\"v {{ 'green' if summary.success_rate >= 90 else 'red' }}\">{{ summary.success_rate }}%</div></div>
    <div class=\"card\"><div class=\"k\">平均耗时</div><div class=\"v\">{{ (summary.avg_duration_seconds|int) if summary.avg_duration_seconds else '—' }}s</div></div>
    <div class=\"card\"><div class=\"k\">P90 耗时</div><div class=\"v\">{{ (summary.p90_duration_seconds|int) if summary.p90_duration_seconds else '—' }}s</div></div>
  </div>

  <h2>整体运行结果</h2>
  <div class="charts-grid">
    {% if charts.get('result_distribution') %}<div class="chart"><img src="{{ charts.get('result_distribution') }}"/></div>{% endif %}
    {% if charts.get('success_rate_trend') %}<div class="chart full"><img src="{{ charts.get('success_rate_trend') }}"/></div>{% endif %}
  </div>

  <div class=\"footer\">vLLM Ascend CI Dashboard · 每日综合报错报告 · 自动聚合生成</div>
</body>
</html>
"""


@dataclass
class PdfResult:
    html: str
    css: str
    filename: str


def _load_template() -> jinja2.Template:
    loader = jinja2.BaseLoader()
    env = jinja2.Environment(
        loader=loader,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["ai_zh"] = _ai_status_zh
    env.globals["ai_color"] = _ai_status_color
    return env.from_string(_HTML_TEMPLATE)


class CIDailyReportPDF:
    """Render the CI daily comprehensive report to PDF (weasyprint)."""

    def __init__(self, report: dict):
        self.report = report

    def render(self) -> bytes:
        html = self.render_html(self.report)
        try:
            from weasyprint import HTML
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("weasyprint 未安装，无法生成 PDF") from exc
        # 必须返回真实 bytes（非 BytesIO），否则 Starlette 序列化会对内容 .encode() 报错
        return HTML(string=html).write_pdf()

    @staticmethod
    def render_html(report: dict) -> str:
        summary = report.get("summary", {})
        window = report.get("window", {})
        failures = report.get("failures", [])
        charts = report.get("charts", {}) or {}
        # 仅保留存在的图表，避免模板取空字符串
        charts = {k: charts[k] for k in CHART_KEYS if charts.get(k)}

        template = _load_template()
        return template.render(
            window=window,
            summary=summary,
            failures=failures,
            charts=charts,
            all_green=report.get("all_green", False),
            failure_columns=FAILURE_COLUMNS,
        )

    @staticmethod
    def default_filename(report: dict) -> str:
        window = report.get("window", {})
        return f"ci_daily_report_{window.get('start_date', 'unknown')}.pdf"
