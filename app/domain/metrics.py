# -*- coding: utf-8 -*-
"""指标与窗口的领域定义（业务定义报告 2.2/2.3）。

指标不能混用：
- net_stars：两个实际采样时刻的总 Star 差值，可为负；
- github_history_stars：官方 Star 历史接口的日桶计数；
- observed_star_events：事件源实际捕获的 WatchEvent 数；
- provider_reported：第三方报告声明的增长值。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


class Metric(str, Enum):
    NET_STARS = "net_stars"
    GITHUB_HISTORY_STARS = "github_history_stars"
    OBSERVED_STAR_EVENTS = "observed_star_events"
    PROVIDER_REPORTED = "provider_reported"


METRIC_LABELS = {
    Metric.NET_STARS: "净增长（采样差值）",
    Metric.GITHUB_HISTORY_STARS: "官方历史日桶",
    Metric.OBSERVED_STAR_EVENTS: "事件捕获数",
    Metric.PROVIDER_REPORTED: "来源报告增长",
}


class Granularity(str, Enum):
    SAMPLE = "sample"       # 实际采样时刻
    DAILY = "daily"         # 日桶
    HOURLY = "hourly"       # 小时事件
    REPORT = "report"       # 报告期


class Completeness(str, Enum):
    VALID = "valid"         # 通过该来源契约校验
    PARTIAL = "partial"     # 有数据但覆盖/边界不完整
    MISSING = "missing"     # 缺测，不能用 0 代替


def metric_label(metric: Metric | str) -> str:
    try:
        key = metric if isinstance(metric, Metric) else Metric(str(metric))
    except ValueError:
        return str(metric)
    return METRIC_LABELS[key]


@dataclass(frozen=True)
class WindowSpec:
    """一次统计请求：指标 + 窗口 + 粒度 + 边界策略。"""

    metric: Metric
    days: int
    start: datetime | None = None
    end: datetime | None = None
    granularity: Granularity = Granularity.SAMPLE
    boundary_policy: str = ""

    def __post_init__(self):
        if self.days <= 0:
            raise ValueError("窗口天数必须为正")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("窗口开始必须早于结束")

    @property
    def label(self) -> str:
        return f"{metric_label(self.metric)}·近{self.days}天"


def rolling_window(now: datetime, days: int,
                   metric: Metric = Metric.NET_STARS,
                   granularity: Granularity = Granularity.SAMPLE,
                   boundary_policy: str = "半开区间 [T-days, T)") -> WindowSpec:
    """构造滚动窗口；T 为统一截止时刻（UTC）。"""
    end = now.astimezone(timezone.utc) if now.tzinfo else now.replace(
        tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    return WindowSpec(metric=metric, days=days, start=start, end=end,
                      granularity=granularity, boundary_policy=boundary_policy)
