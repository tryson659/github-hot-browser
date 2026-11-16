# -*- coding: utf-8 -*-
"""R03：观察增长按请求窗口取基线，不再用长期样本冒充周增长。"""
from datetime import date, timedelta

import pytest

from app.core.db import Database
from app.services import growth as growth_mod
from app.services import watchlist


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def _samples(spec):
    today = date.today()
    return {(today - timedelta(days=ago)).isoformat(): stars
            for ago, stars in spec}


def test_window_baseline_is_used(db):
    """40/7/0 天样本：近 7 天 = (170-100)/7，而不是 40 天的 4.2。"""
    for day_s, stars in _samples([(40, 0), (7, 100), (0, 170)]).items():
        db.watch_sample("a/b", stars, day_s)
    gd, span = watchlist.daily_growth(db, "a/b", window=7)
    assert (gd, span) == (10.0, 7)
    g = growth_mod.compute_growth(db.watch_samples("a/b"), window=7)
    assert g.exact and g.delta == 70 and g.label.startswith("近7天")


def test_samples_older_than_window_marked_estimated():
    g = growth_mod.compute_growth(_samples([(40, 0), (2, 100), (0, 170)]),
                                  window=7)
    assert g is not None
    assert g.estimated and g.span_days == 40
    assert g.daily == pytest.approx(4.2)
    assert "非7天" in g.label or "跨度" in g.label


def test_partial_window_reports_span():
    g = growth_mod.compute_growth(_samples([(4, 100), (0, 120)]), window=7)
    assert g is not None and g.span_days == 4 and g.estimated
    assert "不足7天" in g.label
    assert 0 < g.coverage < 1


def test_single_sample_and_missing():
    assert growth_mod.compute_growth({}, window=7) is None
    today = date.today().isoformat()
    assert growth_mod.compute_growth({today: 100}, window=7) is None


def test_negative_growth_kept():
    g = growth_mod.compute_growth(_samples([(7, 200), (0, 150)]), window=7)
    assert g.daily == pytest.approx(-7.1)


def test_board_rows_do_not_extrapolate_long_average(db):
    today = date.today()
    db.watch_add("long/sample", manual=True)
    db.watch_sample("long/sample", 0, (today - timedelta(days=40)).isoformat())
    db.watch_sample("long/sample", 170, today.isoformat())
    rows = watchlist.board_rows(db)
    assert rows and rows[0].growth_7d is None          # 不再外推成周增长
    assert rows[0].growth_value == 170
    assert "40天" in rows[0].growth_window
