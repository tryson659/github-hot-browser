# -*- coding: utf-8 -*-
"""批5 回归测试：每数据源独立快照（切源即切换）、7/30 天区分、历史遗留清理。"""
import pytest

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.services import hot_source


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.set_setting("github_api_enabled", True)   # 本套件覆盖报告源行为
    yield d
    d.close()


def make_repo(name="a/b", stars=100, **kw):
    d = dict(name=name, url=f"https://github.com/{name}", stars=stars,
             forks=10, language="C++", description="demo",
             created="2026-01-01", pushed="2026-11-01", domain="开发工具")
    d.update(kw)
    return Repo.from_dict(d)


# ---------------- 通道键映射 ----------------
class TestHotChannel:
    def test_per_source_keys(self):
        assert config.hot_channel("opengithubs", 7) == config.RANK_WEEK_KEY
        assert config.hot_channel("opengithubs", 30) == config.RANK_MONTH_KEY
        assert config.hot_channel("gharchive", 7) == config.GH_ARC_KEY
        assert config.hot_channel("gharchive", 30) == config.GH_ARC_KEY
        assert config.hot_channel("trending", 7) == config.TREND_KEY
        assert config.hot_channel("search", 30) == config.HOT_KEY

    def test_week_month_are_distinct(self):
        assert config.hot_channel("opengithubs", 7) != \
               config.hot_channel("opengithubs", 30)

    def test_needs_baseline(self):
        assert config.needs_baseline(config.HOT_KEY)
        assert config.needs_baseline("C++")
        assert config.needs_baseline("domain:AI / Agent")
        assert not config.needs_baseline(config.RANK_WEEK_KEY)
        assert not config.needs_baseline(config.GH_ARC_KEY)
        assert not config.needs_baseline(config.TREND_KEY)


# ---------------- 历史遗留清理 ----------------
class TestFixup:
    @staticmethod
    def _write_legacy(path, days, period):
        """模拟遗留库：写入 HOT_KEY 数据后重置 fixup 标记，使下次打开触发整理。"""
        d0 = Database(path)
        d0.save_cache(config.HOT_KEY, days, [make_repo()], period)
        d0.set_setting("__hot_ch_fixed__", False)
        d0.close()

    def test_rank_period_moved_to_rank_key(self, tmp_path):
        # 批1 遗留：OpenGithubs 数据写在 HOT_KEY 下
        path = str(tmp_path / "t.db")
        self._write_legacy(path, 7, "周榜 20261109")
        d0b = Database(path)          # 重新打开触发 fixup
        assert d0b.load_cache(config.HOT_KEY, 7)[0] == []
        repos, _, period = d0b.load_cache(config.RANK_WEEK_KEY, 7)
        assert repos and period == "周榜 20261109"
        d0b.close()

    def test_search_data_stays(self, tmp_path):
        path = str(tmp_path / "t.db")
        self._write_legacy(path, 7, "本地搜索近似")
        d0b = Database(path)
        assert d0b.load_cache(config.HOT_KEY, 7)[0]      # 不动
        d0b.close()

    def test_fixup_idempotent(self, tmp_path):
        path = str(tmp_path / "t.db")
        self._write_legacy(path, 30, "月榜 20261001")
        Database(path).close()        # 触发 fixup
        d = Database(path)            # 第三次打开不重复处理
        assert d.get_setting("__hot_ch_fixed__") is True
        repos, _, _ = d.load_cache(config.RANK_MONTH_KEY, 30)
        assert len(repos) == 1
        d.close()


# ---------------- 抓取落库到源专属键 ----------------
class TestFetchPerSource:
    def _run_worker(self, db, monkeypatch, source, rank_repos=None):
        from app.services import fetcher as fw
        from app.sources import opengithubs, http
        monkeypatch.setattr(
            opengithubs, "fetch_rank",
            lambda days, interests=None:
                (rank_repos or [make_repo("r/1")], f"周榜 20261109"))
        monkeypatch.setattr(   # 抓前额度预检桩：额度充足
            http, "github_api",
            lambda path, timeout=30:
                {"resources": {"core": {"remaining": 100, "reset": 0}}})
        db.set_setting("hot_source", source)
        got_channels = []
        w = fw.FetchWorker(db, only=config.HOT_KEY)
        w.channel_done.connect(lambda d, ch: got_channels.append((d, ch)))
        w.run()
        return got_channels

    def test_opengithubs_goes_to_rank_keys(self, db, monkeypatch):
        got = self._run_worker(db, monkeypatch, "opengithubs")
        assert (7, config.RANK_WEEK_KEY) in got
        assert (30, config.RANK_MONTH_KEY) in got
        assert db.load_cache(config.RANK_WEEK_KEY, 7)[0]
        assert db.load_cache(config.RANK_MONTH_KEY, 30)[0]
        assert db.load_cache(config.HOT_KEY, 7)[0] == []   # 不写错槽位

    def test_auto_records_active_source(self, db, monkeypatch):
        self._run_worker(db, monkeypatch, "auto")
        # R04：活动源按时间档分别记录
        assert hot_source.get_active(db, 7) == "opengithubs"
        assert hot_source.get_active(db, 30) == "opengithubs"
        assert db.get_setting(hot_source.active_key(7)) == "opengithubs"

    def test_auto_does_not_fall_back_across_metrics(self, db, monkeypatch):
        """auto 只在报告源之间替代；不得静默切到事件/搜索口径。"""
        from app.services import gha_board
        from app.sources import opengithubs, trending
        monkeypatch.setattr(opengithubs, "fetch_rank",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("榜单不可用")))
        monkeypatch.setattr(trending, "fetch_trending",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("趋势页不可用")))
        monkeypatch.setattr(gha_board, "build_board",
                            lambda *a, **k: ([make_repo("g/1")], "GH Archive"))
        db.set_setting("hot_source", "auto")
        from app.services import fetcher as fw
        w = fw.FetchWorker(db, only=config.HOT_KEY)
        w.run()
        assert hot_source.get_active(db, 7) == ""
        assert db.load_cache(config.GH_ARC_KEY, 7)[0] == []
        assert db.load_cache(config.RANK_WEEK_KEY, 7)[0] == []

    def test_history_per_source(self, db, monkeypatch):
        self._run_worker(db, monkeypatch, "opengithubs")
        assert db.list_history(config.RANK_WEEK_KEY, 7)
        assert db.list_history(config.RANK_WEEK_KEY, 7)[0] != []
        assert db.list_history(config.HOT_KEY, 7) == []


# ---------------- UI 切源即切快照 ----------------
class TestUISourceSwitch:
    @pytest.fixture()
    def win(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        w = MainWindow(db, start_sync=False)
        yield w
        w.close()

    def test_switch_source_switches_view(self, win, db):
        db.save_cache(config.RANK_WEEK_KEY, 7, [make_repo("rank/1")])
        db.save_cache(config.HOT_KEY, 7, [make_repo("search/1"),
                                          make_repo("search/2")])
        db.set_setting("hot_source", "opengithubs")
        win._pools.clear()
        win._refresh_view()
        assert [r.name for r in win._current_pool()] == ["rank/1"]
        # 切到本地搜索：立即显示该源快照，无需抓取
        idx = win.source_combo.findData("search")
        win.source_combo.setCurrentIndex(idx)
        assert db.get_setting("hot_source") == "search"
        assert [r.name for r in win._current_pool()] == ["search/1", "search/2"]

    def test_auto_follows_active(self, win, db):
        db.save_cache(config.RANK_WEEK_KEY, 7, [make_repo("rank/1")])
        db.save_cache(config.TREND_KEY, 7, [make_repo("trend/1")])
        db.set_setting("hot_source", "auto")
        hot_source.set_active(db, 7, "trending")
        win._pools.clear()
        assert [r.name for r in win._current_pool()] == ["trend/1"]
        hot_source.set_active(db, 7, "opengithubs")
        win._pools.clear()
        assert [r.name for r in win._current_pool()] == ["rank/1"]

    def test_auto_active_isolated_by_range(self, win, db):
        """R04：周榜/月榜跟随不同报告源，切时间档各看各的结果。"""
        db.save_cache(config.RANK_WEEK_KEY, 7, [make_repo("week/1")])
        db.save_cache(config.TREND_KEY, 30, [make_repo("month/1")])
        db.set_setting("hot_source", "auto")
        hot_source.set_active(db, 7, "opengithubs")
        hot_source.set_active(db, 30, "trending")
        win.days = 7
        win._pools.clear()
        assert [r.name for r in win._current_pool()] == ["week/1"]
        win.days = 30
        win._pools.clear()
        assert [r.name for r in win._current_pool()] == ["month/1"]

    def test_auto_fallback_first_report_cache(self, win, db):
        db.save_cache(config.TREND_KEY, 7, [make_repo("trend/1")])
        db.set_setting("hot_source", "auto")     # 无活动源
        win._pools.clear()
        assert [r.name for r in win._current_pool()] == ["trend/1"]

    def test_auto_ignores_event_cache_without_active(self, win, db):
        db.save_cache(config.GH_ARC_KEY, 7, [make_repo("gha/1")])
        db.set_setting("hot_source", "auto")
        win._pools.clear()
        assert [r.name for r in win._current_pool()] == []

    def test_7d_30d_independent(self, win, db):
        db.save_cache(config.RANK_WEEK_KEY, 7, [make_repo("w/1")])
        db.save_cache(config.RANK_MONTH_KEY, 30, [make_repo("m/1"),
                                                  make_repo("m/2")])
        db.set_setting("hot_source", "opengithubs")
        win._pools.clear()
        win._refresh_view()
        assert len(win._current_pool()) == 1
        win.days_combo.setCurrentIndex(1)        # 切 30 天
        assert [r.name for r in win._current_pool()] == ["m/1", "m/2"]
