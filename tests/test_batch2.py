# -*- coding: utf-8 -*-
"""批2 回归测试：GH Archive 聚合/同步游标、观察榜、收藏刷新里程碑。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import Database
from app.core.models import Repo
from app.services import gha_board, watchlist
from app.sources import gharchive


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def make_repo(name="a/b", stars=100, **kw):
    d = dict(name=name, url=f"https://github.com/{name}", stars=stars,
             forks=10, language="C++", description="demo",
             created="2026-01-01", pushed="2026-11-01", domain="开发工具")
    d.update(kw)
    return Repo.from_dict(d)


# ---------------- GH Archive ----------------
class TestGhArchive:
    def test_hour_day_roundtrip(self, db):
        db.gha_save_hour("2026-11-08-05", {"a/b": 3, "c/d": 1})
        assert db.gha_get_hour("2026-11-08-05") == {"a/b": 3, "c/d": 1}
        assert db.gha_get_hour("2026-11-08-06") is None
        db.gha_save_day("2026-11-07", {"x/y": 9})
        assert db.gha_get_day("2026-11-07") == {"x/y": 9}
        assert "2026-11-07" in db.gha_days()

    def test_cursor(self, db):
        assert db.gha_get_cursor() == ""
        db.gha_set_cursor("2026-11-08-05")
        assert db.gha_get_cursor() == "2026-11-08-05"

    def test_merge_24h_into_day(self, db):
        day = "2026-11-05"
        for h in range(24):
            db.gha_save_hour(f"{day}-{h:02d}", {"a/b": 1, "c/d": h % 2})
        merged = gha_board.merge_days(db)
        assert merged == 1
        assert db.gha_get_day(day) == {"a/b": 24, "c/d": 12}
        assert db.gha_hours_by_day() == {}

    def test_merge_skips_incomplete_day(self, db):
        db.gha_save_hour("2026-11-05-03", {"a/b": 1})
        assert gha_board.merge_days(db) == 0
        assert db.gha_get_day("2026-11-05") is None

    def test_merge_drops_redundant_hours(self, db):
        day = "2026-11-05"
        db.gha_save_day(day, {"a/b": 5})
        db.gha_save_hour(f"{day}-10", {"a/b": 1})   # 已被天级覆盖的小时条目
        assert gha_board.merge_days(db) == 0
        assert db.gha_hours_by_day() == {}

    def test_catch_up_with_mock(self, db, monkeypatch):
        latest = gharchive.latest_done_hour()
        # 造 3 小时前的游标
        cursor = (datetime.strptime(latest, "%Y-%m-%d-%H") - timedelta(hours=2)
                  ).strftime("%Y-%m-%d-%H")
        db.gha_set_cursor(cursor)
        monkeypatch.setattr(gharchive, "download_hour",
                            lambda day, h, cancel=None: {"a/b": h})
        done, caught = gha_board.catch_up(db, max_hours=6, log=lambda *_: None)
        assert done == 2 and caught
        assert db.gha_get_cursor() == latest

    def test_catch_up_failure_keeps_cursor(self, db, monkeypatch):
        latest = gharchive.latest_done_hour()
        cursor = (datetime.strptime(latest, "%Y-%m-%d-%H") - timedelta(hours=1)
                  ).strftime("%Y-%m-%d-%H")
        db.gha_set_cursor(cursor)
        def boom(day, h, cancel=None):
            raise ConnectionError("模拟断网")
        monkeypatch.setattr(gharchive, "download_hour", boom)
        done, caught = gha_board.catch_up(db, log=lambda *_: None)
        assert done == 0 and not caught
        assert db.gha_get_cursor() == cursor      # 失败不推进游标

    def test_window_counts_and_board(self, db):
        latest = datetime.strptime(
            gharchive.latest_done_hour(), "%Y-%m-%d-%H")
        for i in range(3):
            day = (latest - timedelta(days=i)).strftime("%Y-%m-%d")
            db.gha_save_day(day, {"a/b": 5, "c/d": i + 1, "noise/x": 0})
        counts = gha_board.window_counts(db, 7)
        assert counts["a/b"] == 15 and counts["c/d"] == 6
        repos, period = gha_board.build_board(db, 7)
        assert repos[0].name == "a/b"
        # 只有 3 天覆盖（72/169 小时）：原始增量保留，7 天增长不外推
        assert repos[0].growth_value == 15
        assert repos[0].growth_7d is None
        assert "覆盖" in period and "72/" in repos[0].coverage
        assert all(r.name != "noise/x" for r in repos)   # 窗口总量 <2 被过滤

    def test_board_empty_raises(self, db):
        with pytest.raises(RuntimeError):
            gha_board.build_board(db, 7)


# ---------------- 观察榜 ----------------
class TestWatchlist:
    def test_sample_and_daily_growth(self, db):
        db.watch_add("a/b", manual=False)
        base = datetime.now() - timedelta(days=6)
        for i in range(4):
            d = (base + timedelta(days=i * 2)).strftime("%Y-%m-%d")
            db.watch_sample("a/b", 100 + i * 20, d)
        gd, span = watchlist.daily_growth(db, "a/b")
        assert gd == 10.0 and span == 6

    def test_insufficient_samples(self, db):
        db.watch_add("a/b")
        db.watch_sample("a/b", 100)
        assert watchlist.daily_growth(db, "a/b") == (0.0, 0)

    def test_board_rows_sorted(self, db):
        today = datetime.now().strftime("%Y-%m-%d")
        yest = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        for name, s0, s1 in (("a/b", 100, 150), ("c/d", 200, 210)):
            db.watch_add(name, manual=True)
            db.watch_sample(name, s0, yest)
            db.watch_sample(name, s1, today)
        rows = watchlist.board_rows(db)
        assert rows[0].name == "a/b"               # 日均 10 > 2
        assert rows[0].growth_daily == 10.0
        assert rows[0].stars == 150                # 最新采样值

    def test_manual_flag_sticky(self, db):
        db.watch_add("a/b", manual=False)
        db.watch_add("a/b", manual=True)           # 手动后不因自动收录降级
        db.watch_add("a/b", manual=False)
        w = [x for x in db.watch_list() if x["name"] == "a/b"][0]
        assert w["manual"] == 1

    def test_add_manual_validation(self, db):
        with pytest.raises(ValueError):
            watchlist.add_manual(db, "不是合法格式")

    def test_record_samples_skips_none_stars(self, db):
        watchlist.record_samples(db, [make_repo("a/b", None)])
        assert db.watch_list() == []

    def test_remove_cascades(self, db):
        db.watch_add("a/b")
        db.watch_sample("a/b", 1)
        db.watch_remove("a/b")
        assert db.watch_list() == [] and db.watch_samples("a/b") == {}


# ---------------- UI（观察榜/同步线程） ----------------
class TestUIBatch2:
    @pytest.fixture()
    def win(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        w = MainWindow(db, start_sync=False)     # 测试不起网络线程
        yield w
        w._gha = None
        w.close()

    def test_watch_board_renders(self, win, db):
        today = datetime.now().strftime("%Y-%m-%d")
        yest = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
        db.watch_add("a/b", desc="demo", manual=True)
        db.watch_sample("a/b", 100, yest)
        db.watch_sample("a/b", 140, today)
        win._set_board("watch")
        assert win._rendered == 1
        assert win.board_btns["watch"].isChecked()
        assert not win.board_btns["hot"].isChecked()

    def test_three_boards_switch(self, win):
        for key in ("fav", "watch", "hot"):
            win._set_board(key)
            assert win.board == key
            assert win.board_btns[key].isChecked()

    def test_milestone_baseline_reset(self, db):
        r = make_repo("a/b", 6000)
        db.add_favorite(r)
        db.reset_favorite_baseline("a/b", 6000)
        assert db.favorite_meta("a/b")["saved_stars"] == 6000

    def test_milestone_upsert_keeps_note(self, db):
        db.add_favorite(make_repo("a/b", 100))
        db.set_favorite_note("a/b", "备注内容", ["t"])
        db.set_milestone("a/b", 1000)
        meta = db.favorite_meta("a/b")
        assert meta["note"] == "备注内容" and meta["milestone"] == 1000
