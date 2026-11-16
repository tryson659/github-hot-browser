# -*- coding: utf-8 -*-
"""报告导出：当前筛选结果 → Markdown / CSV / HTML（R09）。

导出必须与视图使用同一份指标：来源、指标名、窗口、数值、样本覆盖率都单独
列出；GH Archive 月榜的窗口增量不再因为"近7天增长"一列而丢失。
未知值导出为空，不伪装成 0。
"""
from __future__ import annotations

import csv
import html
from datetime import datetime

from app.core.models import Repo
from app.domain.metrics import metric_label


def metric_name(r: Repo) -> str:
    """该行增长的指标口径；新数据优先使用显式 metric，旧数据兼容推断。"""
    if r.metric:
        base = metric_label(r.metric)
        return f"{base}（{r.growth_window}）" if r.growth_window else base
    if r.growth_value is not None:
        return f"窗口增量（{r.growth_window}）" if r.growth_window else "窗口增量"
    if r.growth_7d is not None:
        return "近7天增长"
    return ""


def metric_value(r: Repo) -> int | None:
    """该行增长数值：优先榜单原始增量，其次近 7 天快照差值。"""
    if r.growth_value is not None:
        return r.growth_value
    return r.growth_7d


def _fmt_signed(value: int | None) -> str:
    return f"+{value}" if value is not None else "-"


def _csv_safe(value):
    """防止不可信文本在 Excel 中被当作公式执行（CSV 注入）。"""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _scope(r: Repo) -> str:
    parts = [p for p in (r.growth_window, r.coverage) if p]
    if r.window_start or r.window_end:
        parts.append(f"{r.window_start or '?'}~{r.window_end or '?'}")
    if r.completeness:
        parts.append(f"完整性:{r.completeness}")
    return " · ".join(parts) or "-"


def _meta_line(board: str, days: int, sort: str, repos: list[Repo]) -> str:
    sources = sorted({r.source for r in repos if r.source})
    metrics = sorted({metric_name(r) for r in repos if metric_name(r)})
    parts = [f"榜单：{board}", f"时间档：近{days}天", f"排序：{sort}",
             f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    if sources:
        parts.append("数据源：" + "、".join(sources))
    if metrics:
        parts.append("增长指标：" + "、".join(metrics))
    return " | ".join(parts)


def to_markdown(repos: list[Repo], board: str, days: int, sort: str) -> str:
    lines = ["# GitHub 热点项目导出", "",
             f"> {_meta_line(board, days, sort, repos)}", "",
             "| # | 项目 | ⭐ | 来源 | 指标 | 数值 | 窗口 / 覆盖 | 语言 | 领域 | 描述 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(repos, 1):
        desc = (r.description or "").replace("|", "\\|")[:80]
        lines.append(
            f"| {i} | [{r.name}]({r.url}) "
            f"| {r.stars if r.stars is not None else '?'} "
            f"| {r.source or '-'} | {metric_name(r) or '-'} "
            f"| {_fmt_signed(metric_value(r))} | {_scope(r)} "
            f"| {r.language} | {r.domain} | {desc} |")
    return "\n".join(lines) + "\n"


def to_csv(repos: list[Repo], path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["排名", "项目", "URL", "来源", "Stars", "Forks",
                    "增长指标", "指标类型", "增长窗口", "窗口开始", "窗口结束",
                    "完整性", "增长值", "近7天增长", "日均增长",
                    "覆盖率", "语言", "领域", "许可证", "已归档",
                    "创建时间", "最近推送", "描述"])
        for i, r in enumerate(repos, 1):
            row = [
                i, r.name, r.url, r.source,
                r.stars if r.stars is not None else "",
                r.forks if r.forks is not None else "",
                metric_name(r), r.metric, r.growth_window,
                r.window_start, r.window_end, r.completeness,
                r.growth_value if r.growth_value is not None else "",
                r.growth_7d if r.growth_7d is not None else "",
                r.growth_daily, r.coverage, r.language, r.domain,
                r.license, "是" if r.archived else "", r.created, r.pushed,
                r.description]
            w.writerow([_csv_safe(value) for value in row])


def to_html(repos: list[Repo], board: str, days: int, sort: str) -> str:
    rows = []
    for i, r in enumerate(repos, 1):
        rows.append(
            f"<tr><td>{i}</td>"
            f"<td><a href='{html.escape(r.url)}'>{html.escape(r.name)}</a></td>"
            f"<td>{r.stars if r.stars is not None else '?'}</td>"
            f"<td>{html.escape(r.source or '-')}</td>"
            f"<td>{html.escape(metric_name(r) or '-')}</td>"
            f"<td>{_fmt_signed(metric_value(r))}</td>"
            f"<td>{html.escape(_scope(r))}</td>"
            f"<td>{html.escape(r.language)}</td>"
            f"<td>{html.escape(r.domain)}</td>"
            f"<td>{html.escape((r.description or '')[:120])}</td></tr>")
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>GitHub 热点项目导出</title>
<style>
body {{ font-family: "Microsoft YaHei UI", sans-serif; background: #0d1117;
       color: #e6edf3; margin: 32px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #30363d; padding: 6px 10px; font-size: 13px; }}
th {{ background: #161b22; }}
a {{ color: #58a6ff; text-decoration: none; }}
.meta {{ color: #8b949e; font-size: 12px; }}
</style></head><body>
<h1>GitHub 热点项目导出</h1>
<p class="meta">{html.escape(_meta_line(board, days, sort, repos))}</p>
<table><tr><th>#</th><th>项目</th><th>⭐</th><th>来源</th><th>指标</th>
<th>数值</th><th>窗口 / 覆盖</th><th>语言</th><th>领域</th><th>描述</th></tr>
{''.join(rows)}
</table></body></html>"""
