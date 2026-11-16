# -*- coding: utf-8 -*-
"""增长窗口计算（纯领域逻辑，无数据库/Qt 依赖）。

观察榜的"近 N 天增长"必须按请求窗口选择基线（R03）：
- 基线优先取窗口起点当天或之前的最近一次采样；
- 窗口内样本不足时，用现有最早样本计算，并标记为估计值；
- 返回真实采样跨度，调用方不得把长期平均外推成"周增长"。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Growth:
    """一次增长计算的结果（daily 已按四舍五入到 0.1）。"""

    daily: float
    span_days: int
    delta: int
    baseline_date: str
    latest_date: str
    estimated: bool        # 采样跨度不等于请求窗口 → 估计值
    coverage: float        # 有效覆盖比例（span / window，上限 1）
    stale: bool            # 最新采样明显滞后（>2 天）
    label: str             # 面向界面的口径说明

    @property
    def exact(self) -> bool:
        return not self.estimated and not self.stale


def _parse(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def compute_growth(samples: dict[str, int], window: int = 7,
                   today: date | None = None) -> Growth | None:
    """按请求窗口计算真实采样跨度的日均增长；样本不足返回 None。"""
    today = today or date.today()
    parsed: dict[date, int] = {}
    for day_s, stars in (samples or {}).items():
        d = _parse(day_s)
        if d is None or stars is None:
            continue
        parsed[d] = int(stars)
    days = sorted(parsed)
    if len(days) < 2:
        return None

    latest = days[-1]
    cutoff = today - timedelta(days=window)
    before = [d for d in days if d <= cutoff]
    # 窗口起点之前有样本 → 用它作基线（可能比起点更旧）；否则用窗口内最早样本
    baseline = before[-1] if before else days[0]
    span = (latest - baseline).days
    if span <= 0:
        return None

    delta = parsed[latest] - parsed[baseline]
    daily = round(delta / span, 1)
    estimated = baseline != cutoff
    stale = (today - latest).days > 2
    coverage = min(span / window, 1.0) if window > 0 else 0.0

    if span == window:
        label = f"近{window}天"
    elif span < window:
        label = f"仅{span}天样本（不足{window}天）"
    else:
        label = f"{span}天跨度（非{window}天）"
    if stale:
        label += f"·最新采样滞后{(today - latest).days}天"

    return Growth(daily=daily, span_days=span, delta=delta,
                  baseline_date=baseline.isoformat(),
                  latest_date=latest.isoformat(),
                  estimated=estimated, coverage=coverage, stale=stale,
                  label=label)
