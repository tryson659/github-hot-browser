# -*- coding: utf-8 -*-
"""R10：GH Archive 覆盖率口径，冷启动不按整个窗口算日均。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import Database
from app.services import gha_board
from app.sources import gharchive


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def _recent_hour(hours_ago=2):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago))


def test_single_hour_sample_marked_insufficient(db):
    hour = _recent_hour(2)
    db.gha_save_hour(hour.strftime("%Y-%m-%d-%H"), {"a/b": 5, "c/d": 3})
    covered, expected = gha_board.window_coverage(db, 7)
    assert covered == 1 and expected > 24

    repos, period = gha_board.build_board(db, 7, 10, None)
    r = repos[0]
    assert r.growth_daily == 0.0          # 不足 24 小时不折算日均
    assert r.growth_7d is None
    assert r.growth_value == 5            # 原始事件数保留
    assert "样本不足" in period
    assert r.coverage.startswith("1/")


def test_three_days_partial_average(db):
    latest = datetime.strptime(gharchive.latest_done_hour(), "%Y-%m-%d-%H").date()
    for i in range(3):
        day = (latest - timedelta(days=i)).isoformat()
        db.gha_save_day(day, {"a/b": 5})
    covered, expected = gha_board.window_coverage(db, 7)
    assert covered == 72
    repos, period = gha_board.build_board(db, 7, 10, None)
    r = repos[0]
    assert r.growth_value == 15
    assert r.growth_daily == 5.0          # 15 / 3 天有效覆盖，而非 15 / 7
    assert r.growth_7d is None
    assert "覆盖" in period


def test_full_week_sample_is_comparable(db):
    today = datetime.now(timezone.utc).date()
    total = 0
    for i in range(7):
        day = (today - timedelta(days=i)).isoformat()
        db.gha_save_day(day, {"a/b": 3})
        total += 3
    covered, expected = gha_board.window_coverage(db, 7)
    assert covered == expected
    repos, period = gha_board.build_board(db, 7, 10, None)
    r = repos[0]
    assert r.growth_7d == total
    assert "样本不足" not in period


def test_backfill_hours_used_only_without_cursor(db, monkeypatch):
    """首次同步的回填范围由用户设置决定（默认 0 = 只取最新小时）。"""
    calls: list[str] = []
    monkeypatch.setattr(
        gha_board.gharchive, "download_hour",
        lambda day, hour, cancel=None: (calls.append(f"{day}-{hour}"),
                                        {"a/b": 1})[1])
    db.set_setting("gha_backfill_hours", 3)
    done, caught = gha_board.catch_up(db, max_hours=10, log=lambda *_: None)
    assert done == 4 and caught              # latest 往前 3 小时 + latest 本身
    assert len(calls) == 4
    # 已有游标后不再回填
    calls.clear()
    done2, _ = gha_board.catch_up(db, max_hours=10, log=lambda *_: None)
    assert done2 == 0 and calls == []
