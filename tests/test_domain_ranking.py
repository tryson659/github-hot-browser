# -*- coding: utf-8 -*-
"""批次 2：排序契约（可取指标优先、缺失置后、同分稳定）。"""
from app.domain.metrics import Completeness, Metric
from app.domain.ranking import RankCandidate, rank_candidates

NET = Metric.NET_STARS


def cand(key, value, metric=NET, days=7, completeness=Completeness.VALID,
         stale=False, tie=""):
    return RankCandidate(repo_key=key, value=value, metric=metric,
                         window_days=days, completeness=completeness,
                         stale=stale, tie_break=tie)


def test_orders_desc_and_missing_last():
    items = [cand("z", -5), cand("a", 10), cand("m", 0), cand("n", None),
             cand("b", 10)]
    ranked = rank_candidates(items)
    assert [c.repo_key for c in ranked] == ["a", "b", "m", "z", "n"]


def test_tie_break_overrides_repo_key():
    items = [cand("b", 10, tie="01"), cand("a", 10, tie="02")]
    assert [c.repo_key for c in rank_candidates(items)] == ["b", "a"]


def test_filters_by_metric_and_window():
    items = [cand("a", 10),
             cand("b", 20, metric=Metric.PROVIDER_REPORTED, days=30),
             cand("c", 30, days=30)]
    assert [c.repo_key for c in rank_candidates(items, metric=NET, days=7)] \
        == ["a"]
    assert [c.repo_key for c in rank_candidates(
        items, metric=Metric.PROVIDER_REPORTED, days=30)] == ["b"]


def test_partial_excluded_by_default_but_listed_last():
    items = [cand("a", 10),
             cand("b", 99, completeness=Completeness.PARTIAL)]
    assert [c.repo_key for c in rank_candidates(items)] == ["a", "b"]
    strict = rank_candidates(items, include_missing=False)
    assert [c.repo_key for c in strict] == ["a"]
    lenient = rank_candidates(items, include_partial=True)
    assert [c.repo_key for c in lenient][:2] == ["b", "a"]


def test_stale_goes_to_missing_group():
    items = [cand("a", 10), cand("old", 999, stale=True)]
    assert [c.repo_key for c in rank_candidates(items)] == ["a", "old"]
