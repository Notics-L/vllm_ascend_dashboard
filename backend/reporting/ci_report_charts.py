"""CI 每日综合报错报告图表渲染。

用 matplotlib (Agg) 将聚合后的 report_data 渲染为一组 PNG 图表（base64），
供 PDF 报告内嵌与前端展示。重点围绕「失败」：失败分类、按分支/硬件/模型的
失败分布、失败处理状态、成功率趋势、失败 Job 耗时。全绿时输出「全绿」横幅。

每个图表函数独立，单个失败不影响其他；全部输出为 ``data:image/png;base64,``
字符串，便于直接塞进 HTML/Excel。
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 非交互式后端，必须在 import pyplot 之前

import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

logger = logging.getLogger(__name__)

# --- 与前端 Recharts 一致的色板 ---
BLUE = "#1890ff"
GREEN = "#2fc25b"
YELLOW = "#f59e0b"
RED = "#dc2626"
GREY = "#6b7280"
DARK = "#1e293b"
BG = "#ffffff"

CN_FONT = "Microsoft YaHei"
_FALLBACK_FONTS = ["SimHei", "Noto Sans CJK SC", "Microsoft YaHei", "sans-serif"]

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": [CN_FONT] + _FALLBACK_FONTS,
    "axes.unicode_minus": False,
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.15,
    "figure.facecolor": BG,
    "axes.facecolor": BG,
})

CATEGORY_COLORS = {
    "基础设施": RED,
    "测试用例": YELLOW,
    "开发代码": "#2563eb",
    "其他": GREY,
    "未定界": "#9ca3af",
}
STATUS_COLORS = {
    "未处理": RED,
    "处理中": YELLOW,
    "已修复": GREEN,
    "已关闭": GREY,
}


def _fig_to_b64(fig: plt.Figure) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _palette(items: list[str], base: dict[str, str]) -> list[str]:
    """Pick a color per label: known label -> palette, otherwise cycle."""
    extras = ["#0ea5e9", "#8b5cf6", "#14b8a6", "#f97316", "#6366f1", "#a855f7"]
    extra_cycle = 0
    colors = []
    for label in items:
        if label in base:
            colors.append(base[label])
        else:
            colors.append(extras[extra_cycle % len(extras)])
            extra_cycle += 1
    return colors


def _empty(label: str) -> str:
    fig, ax = plt.subplots(figsize=(6, 2.4))
    ax.text(0.5, 0.5, label, ha="center", va="center", fontsize=12, color=GREY, transform=ax.transAxes)
    ax.axis("off")
    return _fig_to_b64(fig)


# ---------------------------------------------------------------------------
# 单图渲染
# ---------------------------------------------------------------------------

def render_all_green_banner(window_label: str = "") -> str:
    fig, ax = plt.subplots(figsize=(7, 2.2))
    ax.text(
        0.5, 0.62, "🟢 流水线全绿",
        ha="center", va="center", fontsize=22, fontweight="bold", color=GREEN, transform=ax.transAxes,
    )
    if window_label:
        ax.text(0.5, 0.22, window_label, ha="center", va="center", fontsize=11, color=DARK, transform=ax.transAxes)
    ax.axis("off")
    return _fig_to_b64(fig)


def render_result_distribution(summary: dict[str, Any]) -> str:
    """整体运行结果分布（饼图）：成功 / 失败 / 取消 / 其他。"""
    passed = summary.get("passed_runs", 0)
    failed = summary.get("failed_runs", 0)
    skipped = summary.get("skipped_runs", 0)
    other = max(summary.get("other_runs", 0) - skipped, 0) if summary.get("other_runs") else 0
    labels = [f"成功 {passed}", f"失败 {failed}", f"取消 {skipped}", f"其他 {other}"]
    values = [passed, failed, skipped, other]
    colors = [GREEN, RED, YELLOW, GREY]
    if sum(values) == 0:
        return _empty("无运行记录")
    visible = [(l, v, c) for l, v, c in zip(labels, values, colors, strict=False) if v > 0]
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    ax.pie(
        [v for _, v, _ in visible],
        labels=[l for l, _, _ in visible],
        colors=[c for _, _, c in visible],
        startangle=90,
        counterclock=False,
        textprops={"fontsize": 11, "color": DARK},
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        autopct=lambda p: f"{p:.0f}%" if p >= 8 else "",
    )
    ax.set_title("整体运行结果分布", fontsize=13, fontweight="bold", color=DARK, pad=14)
    return _fig_to_b64(fig)


def render_failure_by_category(failure_by_category: dict[str, int], total_failed: int) -> str:
    """失败按初步定界问题范畴分布（横向柱状图，主色按类别）。"""
    if not failure_by_category:
        return _empty("无失败记录")
    items = list(failure_by_category.items())
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    colors = _palette(labels, CATEGORY_COLORS)
    fig, ax = plt.subplots(figsize=(6.4, max(2.6, len(labels) * 0.5 + 1.2)))
    y = range(len(labels))
    bars = ax.barh(y, values, color=colors, alpha=0.9, height=0.6, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    for bar, v in zip(bars, values, strict=False):
        pct = (v / total_failed * 100) if total_failed else 0
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                f"{v}  ({pct:.0f}%)", va="center", fontsize=10, fontweight="bold", color=DARK)
    ax.set_xlim(0, max(values) * 1.18 if max(values) else 1)
    ax.set_xlabel("失败 Job 数", fontsize=10, color=DARK)
    ax.set_title("失败 · 问题范畴分布", fontsize=13, fontweight="bold", color=DARK, pad=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return _fig_to_b64(fig)


def render_failure_by_branch(failure_by_branch: dict[str, int], total_runs: int) -> str:
    """失败按来源分支分布（main / release，横向堆叠失败+总数）。"""
    if not failure_by_branch:
        return _empty("无失败记录")
    items = sorted(failure_by_branch.items(), key=lambda kv: kv[1], reverse=True)
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    colors = _palette(labels, {"main": BLUE, } if False else {})
    # main=blue, release-like=teal, else cycle
    def _branch_color(name: str, idx: int) -> str:
        n = (name or "").lower()
        if n.startswith("main") or n.startswith("master"):
            return BLUE
        if "release" in n:
            return "#0d9488"
        return ["#8b5cf6", "#a855f7", "#6366f1", "#14b8a6"][idx % 4]

    colors = [_branch_color(l, i) for i, l in enumerate(labels)]
    fig, ax = plt.subplots(figsize=(6.4, max(2.6, len(labels) * 0.6 + 1.2)))
    y = range(len(labels))
    bars = ax.barh(y, values, color=colors, alpha=0.9, height=0.6, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    for bar, v in zip(bars, values, strict=False):
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                str(v), va="center", fontsize=10, fontweight="bold", color=DARK)
    ax.set_xlim(0, max(values) * 1.15 if max(values) else 1)
    ax.set_xlabel("失败 Job 数", fontsize=10, color=DARK)
    ax.set_title("失败 · 来源分支分布", fontsize=13, fontweight="bold", color=DARK, pad=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return _fig_to_b64(fig)


def render_failure_by_hardware(failure_by_hardware: dict[str, int]) -> str:
    """失败按硬件分布。"""
    if not failure_by_hardware:
        return _empty("无失败记录")
    items = sorted(failure_by_hardware.items(), key=lambda kv: kv[1], reverse=True)
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    hw_colors = {"A2": "#15be53", "A3": BLUE, "310P": YELLOW}
    colors = [hw_colors.get(l, "#6b7280") for l in labels]
    fig, ax = plt.subplots(figsize=(6, max(2.4, len(labels) * 0.6 + 1)))
    y = range(len(labels))
    bars = ax.barh(y, values, color=colors, alpha=0.9, height=0.6, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    for bar, v in zip(bars, values, strict=False):
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                str(v), va="center", fontsize=10, fontweight="bold", color=DARK)
    ax.set_xlim(0, max(values) * 1.15 if max(values) else 1)
    ax.set_xlabel("失败 Job 数", fontsize=10, color=DARK)
    ax.set_title("失败 · 硬件分布", fontsize=13, fontweight="bold", color=DARK, pad=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return _fig_to_b64(fig)


def render_failure_by_model(failure_by_model: dict[str, int], top_n: int = 10) -> str:
    """哪些模型/用例失败了（横向柱状图，Top N）。"""
    if not failure_by_model:
        return _empty("无失败记录")
    items = sorted(failure_by_model.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    colors = _palette(labels, {})
    fig, ax = plt.subplots(figsize=(6.8, max(2.8, len(labels) * 0.5 + 1.2)))
    y = range(len(labels))
    bars = ax.barh(y, values, color=colors, alpha=0.9, height=0.6, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    for bar, v in zip(bars, values, strict=False):
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                str(v), va="center", fontsize=9, fontweight="bold", color=DARK)
    ax.set_xlim(0, max(values) * 1.15 if max(values) else 1)
    ax.set_xlabel("失败次数", fontsize=10, color=DARK)
    ax.set_title("失败 · 模型 / 用例 Top%d" % top_n, fontsize=13, fontweight="bold", color=DARK, pad=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return _fig_to_b64(fig)


def render_analysis_status(analysis_distribution: dict[str, int]) -> str:
    """AI 分析状态横向柱状图（替代原处理状态饼图）。"""
    if not analysis_distribution:
        return _empty("无失败记录")
    items = sorted(analysis_distribution.items(), key=lambda kv: kv[1], reverse=True)
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    as_colors = {
        "未分析": "#9ca3af",
        "pending": "#f59e0b",
        "analyzing": "#3b82f6",
        "completed": "#2fc25b",
        "reused": "#06b6d4",
        "failed": "#dc2626",
        "degraded": "#2563eb",
        "cancelled": "#6b7280",
        "raw_log_collected": "#8b5cf6",
    }
    colors = [as_colors.get(l, GREY) for l in labels]
    fig, ax = plt.subplots(figsize=(6.4, max(2.6, len(labels) * 0.5 + 1.2)))
    y = range(len(labels))
    bars = ax.barh(y, values, color=colors, alpha=0.9, height=0.6, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    for bar, v in zip(bars, values, strict=False):
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                str(v), va="center", fontsize=10, fontweight="bold", color=DARK)
    ax.set_xlim(0, max(values) * 1.18 if max(values) else 1)
    ax.set_xlabel("失败 Job 数", fontsize=10, color=DARK)
    ax.set_title("失败 · AI 分析状态", fontsize=13, fontweight="bold", color=DARK, pad=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return _fig_to_b64(fig)


def render_success_rate_trend(trends: list[dict[str, Any]]) -> str:
    """近 N 天成功率趋势（双轴：柱=运行数，线=成功率）。"""
    if not trends or all(t["total"] == 0 for t in trends):
        return _empty("暂无趋势数据")
    labels = [t["label"] for t in trends]
    rates = [t["success_rate"] for t in trends]
    failed = [t["failed"] for t in trends]
    totals = [t["total"] for t in trends]

    fig, ax1 = plt.subplots(figsize=(7, 3.4))
    colors = [GREEN if (f == 0 and t > 0) else (RED if f > 0 else "#cbd5e1") for f, t in zip(failed, totals, strict=False)]
    ax1.bar(range(len(labels)), totals, color=colors, alpha=0.85, width=0.55, zorder=2)
    ax1.set_ylabel("运行数", color=DARK, fontsize=10)
    ax1.set_xticks(range(len(labels)))
    ax1.set_xticklabels(labels, fontsize=9, rotation=45)
    if totals:
        ax1.set_ylim(0, max(totals) * 1.35)

    ax2 = ax1.twinx()
    ax2.plot(range(len(labels)), rates, color=BLUE, marker="o", linewidth=2, markersize=5, zorder=4)
    ax2.set_ylabel("成功率 (%)", color=BLUE, fontsize=10)
    ax2.tick_params(axis="y", labelcolor=BLUE)
    ax2.set_ylim(0, 105)
    ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    for i, r in enumerate(rates):
        if totals[i] > 0:
            ax2.text(i, r + 2.5, f"{r:.0f}", ha="center", fontsize=8, color=BLUE, fontweight="bold")

    ax1.set_title(f"成功率趋势（近 {len(trends)} 天）", fontsize=13, fontweight="bold", color=DARK, pad=12)
    ax1.grid(axis="y", alpha=0.3, zorder=0)
    ax1.set_axisbelow(True)
    fig.tight_layout()
    return _fig_to_b64(fig)


def render_failed_job_durations(failures: list[dict[str, Any]], top_n: int = 10) -> str:
    """失败 Job 耗时 Top N（横向柱状图）。"""
    items = [f for f in failures if f.get("duration_seconds") is not None]
    if not items:
        return _empty("无失败 Job 耗时数据")
    top = sorted(items, key=lambda f: f["duration_seconds"], reverse=True)[:top_n]
    labels = [
        (f["workflow_name"][:16] + "…" if len(f["workflow_name"] or "") > 18 else (f["workflow_name"] or "-"))
        for f in top
    ]
    durations = [f["duration_seconds"] for f in top]
    hours = [d / 3600 for d in durations]
    colors = [RED if d >= 3600 else "#ef4444" for d in durations]
    fig, ax = plt.subplots(figsize=(7, max(2.8, len(top) * 0.5 + 1.2)))
    y = range(len(top))
    bars = ax.barh(y, hours, color=colors, alpha=0.85, height=0.6, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    for bar, d in zip(bars, durations, strict=False):
        h = int(d // 3600)
        m = int((d % 3600) // 60)
        label = f"{h}h{m}m" if h else f"{m}m"
        ax.text(bar.get_width() + (max(hours) * 0.02 if max(hours) else 0.02),
                bar.get_y() + bar.get_height() / 2, label, va="center",
                fontsize=9, fontweight="bold", color=DARK)
    ax.set_xlim(0, max(hours) * 1.15 if max(hours) else 1)
    ax.set_xlabel("耗时（小时）", fontsize=10, color=DARK)
    ax.set_title(f"失败 Job 耗时 Top {len(top)}", fontsize=13, fontweight="bold", color=DARK, pad=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return _fig_to_b64(fig)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def render_report_charts(report_data: dict[str, Any]) -> dict[str, str]:
    """渲染一组 base64 PNG 图表。

    Returns:
        dict[str, str]，键为图表名，值为 ``data:image/png;base64,`` 字符串。
        全绿时输出 ``all_green`` 横幅；否则输出失败相关图表。
    """
    charts: dict[str, str] = {}
    summary = report_data.get("summary", {})
    failures = report_data.get("failures", [])
    window_label = report_data.get("window", {}).get("start_beijing", "")
    if window_label:
        window_label = (
            f"{window_label} ~ {report_data.get('window', {}).get('end_beijing', '')}"
        )

    def _safe(key: str, fn) -> None:
        try:
            out = fn()
            if out:
                charts[key] = out
        except Exception:
            logger.warning("Failed to render chart %s", key, exc_info=True)

    if report_data.get("all_green"):
        _safe("all_green", lambda: render_all_green_banner(window_label))
        _safe("result_distribution", lambda: render_result_distribution(summary))
        _safe("success_rate_trend", lambda: render_success_rate_trend(report_data.get("trends", [])))
        return charts

    _safe("result_distribution", lambda: render_result_distribution(summary))
    _safe("failure_by_category", lambda: render_failure_by_category(
        summary.get("failure_by_category", {}), summary.get("total_failed", 0)))
    _safe("failure_by_branch", lambda: render_failure_by_branch(
        summary.get("failure_by_branch", {}), summary.get("total_runs", 0)))
    _safe("failure_by_hardware", lambda: render_failure_by_hardware(
        summary.get("failure_by_hardware", {})))
    _safe("failure_by_model", lambda: render_failure_by_model(
        summary.get("failure_by_model", {})))
    _safe("analysis_status", lambda: render_analysis_status(
        summary.get("analysis_distribution", {})))
    _safe("success_rate_trend", lambda: render_success_rate_trend(report_data.get("trends", [])))
    _safe("failed_job_durations", lambda: render_failed_job_durations(failures))
    return charts
