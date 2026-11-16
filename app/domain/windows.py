# -*- coding: utf-8 -*-
"""窗口计算（业务定义报告 2.3、技术路线 6.1/6.2）。

- 总量快照：取"不晚于边界"的最近成功样本，保留负值，记录边界偏移；
  偏移超出容差标记 PARTIAL，不进入严格可比榜。
- 官方日桶：只汇总完整日桶；缺桶不填零，返回覆盖缺口；未结束日不进入窗口。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app.domain.metrics import Completeness, Metric, WindowSpec


@dataclass(frozen=True)
class WindowResult:
    value: int | None
    metric: Metric
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    completeness: Completeness
    quality_ok: bool
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.value is not None and self.quality_ok


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def net_growth(samples: list[tuple[datetime, int]],
               spec: WindowSpec,
               tolerance: timedelta = timedelta(hours=24)) -> WindowResult:
    """总量采样净增长；samples 为 (采样时刻, stars)。

    起点或终点缺测返回 value=None（MISSING），不得用 0 补齐。
    """
    if spec.metric is not Metric.NET_STARS:
        raise ValueError("net_growth 只接受 net_stars 窗口")
    if spec.start is None or spec.end is None:
        raise ValueError("窗口必须包含 start/end")
    start, end = _utc(spec.start), _utc(spec.end)
    if start >= end:
        raise ValueError("窗口开始必须早于结束")

    ordered = sorted(((_utc(t), int(v)) for t, v in samples), key=lambda x: x[0])
    before_end = [s for s in ordered if s[0] <= end]
    before_start = [s for s in ordered if s[0] <= start]
    end_sample = before_end[-1] if before_end else None
    start_sample = before_start[-1] if before_start else None

    if end_sample is None or start_sample is None:
        return WindowResult(
            value=None, metric=spec.metric, requested_start=start,
            requested_end=end,
            actual_start=start_sample[0] if start_sample else None,
            actual_end=end_sample[0] if end_sample else None,
            completeness=Completeness.MISSING, quality_ok=False,
            reason="起点或终点无成功采样")

    actual_span = end_sample[0] - start_sample[0]
    if actual_span <= timedelta(0):
        return WindowResult(
            value=None, metric=spec.metric, requested_start=start,
            requested_end=end, actual_start=start_sample[0],
            actual_end=end_sample[0], completeness=Completeness.MISSING,
            quality_ok=False, reason="起止命中同一采样，无法计算差值")

    offset_start = abs(start_sample[0] - start)
    offset_end = abs(end_sample[0] - end)
    valid = offset_start <= tolerance and offset_end <= tolerance
    reasons = []
    if offset_start > tolerance:
        reasons.append(f"起点样本偏移 {offset_start}")
    if offset_end > tolerance:
        reasons.append(f"终点样本偏移 {offset_end}")
    return WindowResult(
        value=end_sample[1] - start_sample[1], metric=spec.metric,
        requested_start=start, requested_end=end,
        actual_start=start_sample[0], actual_end=end_sample[0],
        completeness=Completeness.VALID if valid else Completeness.PARTIAL,
        quality_ok=valid, reason="；".join(reasons))


def history_growth(buckets: dict[date, int],
                   spec: WindowSpec) -> WindowResult:
    """官方日桶窗口汇总；只接受完整日，缺桶返回 PARTIAL 并给出缺口数。"""
    if spec.metric is not Metric.GITHUB_HISTORY_STARS:
        raise ValueError("history_growth 只接受 github_history_stars 窗口")
    if spec.start is None or spec.end is None:
        raise ValueError("窗口必须包含 start/end")
    start, end = _utc(spec.start), _utc(spec.end)
    if start >= end:
        raise ValueError("窗口开始必须早于结束")

    first = start.date()
    last = (end - timedelta(days=1)).date()
    wanted = [first + timedelta(days=i)
              for i in range((last - first).days + 1)]
    present = [d for d in wanted if d in buckets]
    missing = [d for d in wanted if d not in buckets]

    if not present:
        return WindowResult(
            value=None, metric=spec.metric, requested_start=start,
            requested_end=end, actual_start=None, actual_end=None,
            completeness=Completeness.MISSING, quality_ok=False,
            reason="窗口内无日桶数据")

    value = sum(int(buckets[d]) for d in present)
    if missing:
        completeness = Completeness.PARTIAL
        reason = f"缺 {len(missing)}/{len(wanted)} 个日桶"
    else:
        completeness = Completeness.VALID
        reason = ""
    return WindowResult(
        value=value, metric=spec.metric, requested_start=start,
        requested_end=end,
        actual_start=datetime.combine(present[0], time.min, tzinfo=timezone.utc),
        actual_end=datetime.combine(present[-1] + timedelta(days=1), time.min,
                                    tzinfo=timezone.utc),
        completeness=completeness,
        quality_ok=completeness is Completeness.VALID, reason=reason)
