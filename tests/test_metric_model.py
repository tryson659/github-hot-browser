# -*- coding: utf-8 -*-
"""批次 2：Repo 窗口值、当前档排序与来源分档。"""
from app.core import config
from app.core.models import Repo, sort_key
from app.services.exporter import metric_name


def _repo(name="a/b", **kw):
    data = dict(name=name, url=f"https://github.com/{name}", stars=10)
    data.update(kw)
    return Repo.from_dict(data)


def test_provider_value_only_for_its_window():
    r = _repo(metric="provider_reported", growth_value=30, window_days=30,
              growth_window="月榜 202610")
    assert r.window_value(30) == 30
    assert r.window_value(7) is None


def test_observed_event_value_only_for_its_window():
    r = _repo(metric="observed_star_events", growth_value=12, window_days=7,
              growth_window="近7天")
    assert r.window_value(7) == 12
    assert r.window_value(30) is None


def test_net_stars_never_uses_cross_window_delta():
    r = _repo(metric="net_stars", growth_7d=None, growth_value=170,
              window_days=40, growth_window="40天跨度")
    assert r.window_value(7) is None
    r2 = _repo(metric="net_stars", growth_7d=5, growth_value=5, window_days=7)
    assert r2.window_value(7) == 5


def test_legacy_rows_still_readable():
    r = _repo(growth_value=9, growth_window="旧周榜")
    assert r.window_value(7) == 9


def test_sort_by_current_window_and_missing_last():
    rows = [_repo("a/x", metric="provider_reported", growth_value=5,
                  window_days=30),
            _repo("b/y", metric="provider_reported", growth_value=50,
                  window_days=30),
            _repo("c/z", metric="provider_reported", growth_value=None,
                  window_days=30)]
    ordered = sorted(rows, key=sort_key("近7天增长", 30), reverse=True)
    assert [r.name for r in ordered] == ["b/y", "a/x", "c/z"]


def test_exporter_uses_metric_label():
    r = _repo(metric="observed_star_events", growth_value=8, window_days=7,
              growth_window="近7天")
    assert metric_name(r).startswith("事件捕获数")


def test_auto_chain_does_not_silently_switch_metric():
    assert config.AUTO_SOURCE_CHAIN == ("opengithubs", "trending")
    assert "search" not in config.AUTO_SOURCE_CHAIN
    assert "gharchive" not in config.AUTO_SOURCE_CHAIN
