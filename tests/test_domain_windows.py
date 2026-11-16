# -*- coding: utf-8 -*-
"""批次 2：窗口计算契约（净增长/官方日桶）。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.metrics import (
    Completeness,
    Granularity,
    Metric,
    WindowSpec,
    rolling_window,
)
from app.domain.windows import history_growth, net_growth

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def _spec(days=7, metric=Metric.NET_STARS):
    return rolling_window(NOW, days, metric=metric,
                          granularity=Granularity.SAMPLE)


class TestNetGrowth:
    def test_basic_delta_and_range(self):
        samples = [(NOW - timedelta(days=7), 100),
                   (NOW - timedelta(hours=2), 170)]
        r = net_growth(samples, _spec())
        assert r.value == 70 and r.completeness is Completeness.VALID
        assert r.quality_ok
        assert r.actual_start == NOW - timedelta(days=7)
        assert r.actual_end == NOW - timedelta(hours=2)

    def test_negative_growth_kept(self):
        r = net_growth([(NOW - timedelta(days=7), 200),
                        (NOW, 150)], _spec())
        assert r.value == -50 and r.completeness is Completeness.VALID

    def test_missing_start_not_zero(self):
        r = net_growth([(NOW - timedelta(days=2), 170)], _spec())
        assert r.value is None
        assert r.completeness is Completeness.MISSING
        assert "无成功采样" in r.reason

    def test_offset_beyond_tolerance_is_partial(self):
        r = net_growth([(NOW - timedelta(days=10), 100),
                        (NOW - timedelta(hours=1), 150)],
                       _spec(), tolerance=timedelta(hours=6))
        assert r.value == 50
        assert r.completeness is Completeness.PARTIAL
        assert not r.quality_ok and "起点样本偏移" in r.reason

    def test_same_sample_cannot_produce_delta(self):
        r = net_growth([(NOW - timedelta(days=10), 100)], _spec())
        assert r.value is None and r.completeness is Completeness.MISSING

    def test_rejects_non_net_metric(self):
        with pytest.raises(ValueError):
            net_growth([], _spec(metric=Metric.PROVIDER_REPORTED))


class TestHistoryGrowth:
    def _spec(self, days=7):
        return rolling_window(NOW, days,
                              metric=Metric.GITHUB_HISTORY_STARS,
                              granularity=Granularity.DAILY)

    def _days(self, n=7):
        end = NOW.date()
        return [end - timedelta(days=i) for i in range(n, 0, -1)]

    def test_complete_week(self):
        buckets = {d: 10 for d in self._days()}
        r = history_growth(buckets, self._spec())
        assert r.value == 70 and r.completeness is Completeness.VALID
        assert r.quality_ok

    def test_missing_day_is_partial_not_zero(self):
        days = self._days()
        buckets = {d: 10 for d in days[:-1]}
        r = history_growth(buckets, self._spec())
        assert r.value == 60
        assert r.completeness is Completeness.PARTIAL
        assert "缺 1/7" in r.reason
        assert not r.quality_ok

    def test_no_buckets_is_missing(self):
        r = history_growth({}, self._spec())
        assert r.value is None
        assert r.completeness is Completeness.MISSING

    def test_zero_bucket_counts_as_present(self):
        buckets = {d: 0 for d in self._days()}
        r = history_growth(buckets, self._spec())
        assert r.value == 0
        assert r.completeness is Completeness.VALID

    def test_unfinished_day_excluded(self):
        days = self._days()
        buckets = {d: 10 for d in days}
        buckets[NOW.date()] = 999          # 未结束日不在 [start, end)
        r = history_growth(buckets, self._spec())
        assert r.value == 70

    def test_rejects_wrong_metric(self):
        with pytest.raises(ValueError):
            history_growth({}, _spec(metric=Metric.NET_STARS))


def test_window_spec_validation():
    with pytest.raises(ValueError):
        WindowSpec(metric=Metric.NET_STARS, days=0)
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        WindowSpec(metric=Metric.NET_STARS, days=7, start=start, end=start)
