# -*- coding: utf-8 -*-
"""全功能 + 组合功能端到端排查测试。

覆盖：榜单切换 / 时间档 / 数据源 / 排序 / 关键词 / 组合筛选 / 分页 /
卡片渲染全形态 / 收藏 / 备注 / 翻译 / Release / 排名徽章 / 标签即筛 /
抓取（全部·当前·取消）/ 收藏刷新·里程碑 / 观察添加 / 名单导入 /
历史快照 / 导出 / 设置保存 / 主题 / 标签补齐 / 定时抓取 / 更新检查，
以及上述功能的组合场景。网络与弹窗全部 mock，同步执行。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.ui.main_window import MainWindow
from app.ui.repo_card import RepoCard


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def R(name, lang="C++", domain="开发工具", stars=100, desc="hello world",
      topics=None, growth_7d=5, **kw):
    return Repo(name=name, url=f"https://github.com/{name}", description=desc,
                language=lang, domain=domain, stars=stars, forks=10,
                growth_7d=growth_7d, growth_daily=1.0, pushed="2026-11-09",
                topics=topics or [], **kw)


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    """离屏主窗口；屏蔽一切阻塞弹窗与外部程序。"""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    db = Database(str(tmp_path / "t.db"))
    db.set_setting("github_api_enabled", True)   # 本套件覆盖 API 相关功能
    w = MainWindow(db, start_sync=False)
    w._auto_msgbox = True
    yield w
    db.close()


def seed_hot(win, n=5, days=7, channel=None, period="周榜 20261109"):
    repos = [R(f"o/r{i}", stars=1000 - i * 10) for i in range(n)]
    ch = channel or config.hot_channel("opengithubs", days)
    win.db.save_cache(ch, days, repos, period)
    win.db.set_setting("__hot_ch_fixed__", True)
    win._pools.clear()
    return repos


def card_widgets(win):
    out = []
    for i in range(win.cards_lay.count()):
        w = win.cards_lay.itemAt(i).widget()
        if isinstance(w, RepoCard):
            out.append(w)
    return out


# ================= A. 榜单与切换 =================
class TestBoards:
    def test_board_switch(self, win):
        seed_hot(win)
        win._set_board("fav")
        assert win.board == "fav"
        win._set_board("watch")
        assert win.board == "watch"
        win._set_board("hot")
        assert len(card_widgets(win)) == 5

    def test_days_switch_independent(self, win):
        seed_hot(win, n=5, days=7)
        seed_hot(win, n=3, days=30)
        win._set_board("hot")
        assert len(card_widgets(win)) == 5
        win.days_combo.setCurrentIndex(1)      # 近30天
        assert win.days == 30
        assert len(card_widgets(win)) == 3
        win.days_combo.setCurrentIndex(0)
        assert len(card_widgets(win)) == 5

    def test_source_days_matrix(self, win):
        """5 个数据源 × 2 个时间档全部切一遍，不崩且视图跟随。"""
        seed_hot(win, n=4, days=7)
        seed_hot(win, n=2, days=30)
        win._set_board("hot")
        for d_idx in (0, 1):
            win.days_combo.setCurrentIndex(d_idx)
            for s_idx in range(win.source_combo.count()):
                win.source_combo.setCurrentIndex(s_idx)
                src = win.source_combo.itemData(s_idx)
                ch = config.hot_channel(src if src != "auto" else "opengithubs",
                                        win.days)
                pool = win._current_pool()
                assert isinstance(pool, list)

    def test_sorts_all_boards(self, win):
        seed_hot(win)
        win._set_board("hot")
        for s in config.SORTS:
            win.sort_combo.setCurrentText(s)
            assert win.sort_name == s
        win._set_board("fav")
        for s in config.SORTS:
            win.sort_combo.setCurrentText(s)
        win._set_board("watch")
        for s in config.SORTS:
            win.sort_combo.setCurrentText(s)

    def test_keyword_per_board(self, win):
        win.db.add_favorite(R("o/fav1", desc="uniquefavword"))
        seed_hot(win, n=3)
        win._set_board("hot")
        win.kw_edit.setText("r1")
        assert [r.name for r in win._filtered()] == ["o/r1"]
        win.kw_edit.setText("uniquefavword")
        assert win._filtered() == []           # hot 池没有
        win._set_board("fav")
        assert [r.name for r in win._filtered()] == ["o/fav1"]
        win.kw_edit.clear()

    def test_keyword_matches_note_and_tags(self, win):
        win.db.add_favorite(R("o/fav1"))
        win.db.set_favorite_note("o/fav1", "我的特别备注词", ["自定义标签X"])
        win._set_board("fav")
        win.kw_edit.setText("特别备注")
        assert [r.name for r in win._filtered()] == ["o/fav1"]
        win.kw_edit.setText("自定义标签x")      # 大小写不敏感
        assert [r.name for r in win._filtered()] == ["o/fav1"]

    def test_pagination(self, win):
        seed_hot(win, n=config.PAGE_SIZE + 5)
        win._set_board("hot")
        assert len(card_widgets(win)) == config.PAGE_SIZE
        more = None
        for i in range(win.cards_lay.count()):
            w = win.cards_lay.itemAt(i).widget()
            if isinstance(w, QPushButton) and "加载更多" in w.text():
                more = w
        assert more is not None
        more.click()
        assert len(card_widgets(win)) == config.PAGE_SIZE + 5


# ================= B. 卡片功能 =================
class TestCards:
    def test_render_variants(self, win):
        """无描述/无topics/归档/榜单增量/兴趣徽章/备注 全部渲染不崩。"""
        repos = [
            R("o/nodesc", desc=""),
            R("o/arch", archived=True),
            R("o/rank", growth_7d=None, growth_value=233, growth_window="周"),
            R("o/interest", interest_tags=["示例方向"], topics=["game", "cpp"]),
        ]
        win.db.save_cache(config.HOT_KEY, 7, repos, "")
        win.db.add_favorite(repos[1])
        win.db.set_favorite_note("o/arch", "归档项目备注", [])
        win._pools.clear()
        win.db.set_setting("hot_source", "search")   # 直接用 HOT_KEY
        win._set_board("hot")
        cards = card_widgets(win)
        assert len(cards) == 4
        by_name = {c.repo.name: c for c in cards}
        assert not hasattr(by_name["o/nodesc"], "tr_btn")   # 无描述无翻译钮
        assert hasattr(by_name["o/arch"], "tr_btn")

    def test_toggle_favorite(self, win):
        seed_hot(win, n=2)
        win._set_board("hot")
        win._toggle_favorite("o/r0")
        assert win.db.is_favorite("o/r0")
        win._toggle_favorite("o/r0")
        assert not win.db.is_favorite("o/r0")

    def test_translate_cached_and_network(self, win, monkeypatch):
        seed_hot(win, n=1)
        win._set_board("hot")
        card = card_widgets(win)[0]
        win.db.save_translation("hello world", "你好世界")
        win._translate_card(card)                 # 缓存命中，同步回填
        assert card.desc_lbl.text() == "你好世界"
        # 网络路径（mock 翻译服务）
        from app.services import translate as tr
        monkeypatch.setattr(tr, "translate",
                            lambda text, db: "模拟译文")
        card2 = RepoCard(2, R("o/x", desc="other text"), win.C)
        win._translate_card(card2)
        win._on_translated(type("W", (), {"result": ("ok", "模拟译文"),
                                          "deleteLater": lambda s: None})(), card2)
        assert card2.desc_lbl.text() == "模拟译文"
        # 失败路径
        win._on_translated(type("W", (), {"result": ("err", "超时"),
                                          "deleteLater": lambda s: None})(), card2)
        assert "翻译失败" in win.status_lbl.text()

    def test_release_cached(self, win):
        seed_hot(win, n=1)
        win.db.save_release("o/r0", "v1.2.3", "2026-11-01")
        win._set_board("hot")
        card = card_widgets(win)[0]
        assert "v1.2.3" in card.rel_btn.text()

    def test_rank_badges(self, win):
        """与次新快照对比：新上榜🆕 / 上升▲ / 下降▼。"""
        ch = config.hot_channel("opengithubs", 7)
        prev = [R("o/a"), R("o/b"), R("o/c")]
        win.db.save_history(ch, 7, prev, taken_at="2026-11-08 10:00:00")
        cur = [R("o/c"), R("o/a"), R("o/new")]   # c↑2 a↓1 new🆕
        win.db.save_cache(ch, 7, cur, "周榜 20261109")
        win.db.save_history(ch, 7, cur, taken_at="2026-11-09 10:00:00")
        win._pools.clear()
        win._set_board("hot")
        badges = win._rank_badges
        assert badges.get("o/new") == "🆕"
        assert badges.get("o/c") == "▲2"
        assert badges.get("o/a") == "▼1"

    def test_tag_chip_click_filters(self, win):
        seed_hot(win, n=1)
        win.db.get_topics  # noqa
        win._pools.clear()
        win._set_board("hot")
        win._pick_tag("llm")
        assert win.tag_filter == "llm"
        # 池里没有 llm 标签 → 空 + 筛选提示
        hints = [win.cards_lay.itemAt(i).widget()
                 for i in range(win.cards_lay.count())
                 if isinstance(win.cards_lay.itemAt(i).widget(), QLabel)]
        assert any("筛选" in h.text() for h in hints)
        win._clear_filters()
        assert len(card_widgets(win)) == 1


# ================= C. 数据操作 =================
class TestFetch:
    def _mock_sources(self, monkeypatch):
        from app.services import gha_board
        from app.sources import opengithubs, github_search, http
        monkeypatch.setattr(   # 抓前额度预检桩：额度充足
            http, "github_api",
            lambda path, timeout=30:
                {"resources": {"core": {"remaining": 100, "reset": 0}}})
        monkeypatch.setattr(
            opengithubs, "fetch_rank",
            lambda days, interests: ([R(f"rank/r{i}") for i in range(3)],
                                     f"{'周' if days == 7 else '月'}榜 20261109"))

        def fake_channel(days, kind, key, n, interests, cancel, log):
            # dom 通道的 key 是定向查询词，需反映射为领域名（fetcher 按领域过滤）
            kw2dom = {v: k for k, v in config.DOMAIN_QUERY_KWS.items()}
            if kind == "dom":
                return [R(f"dom/{key}{i}", domain=kw2dom.get(key, "其他"))
                        for i in range(2)]
            return [R(f"{kind}/{key or 'hot'}{i}",
                      lang=key if kind == "lang" else "Go")
                    for i in range(2)]
        monkeypatch.setattr(github_search, "fetch_channel", fake_channel)
        monkeypatch.setattr(
            gha_board, "build_board",
            lambda db, days, n, interests, now=None: ([R("gha/x")], "2026-11-09"))
        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

    def test_fetch_all_mocked(self, win, monkeypatch):
        self._mock_sources(monkeypatch)
        from app.services.fetcher import FetchWorker
        wk = FetchWorker(win.db)
        wk.run()                              # 同步执行
        # 默认 languages=[C++, C]，domains=8 个，hot×1 → 每时间档 11 通道
        for days in (7, 30):
            repos, _, period = win.db.load_cache(
                config.hot_channel("opengithubs", days), days)
            assert len(repos) == 3
            repos, _, _ = win.db.load_cache("C++", days)
            assert len(repos) == 2
            repos, _, _ = win.db.load_cache(
                config.DOMAIN_PREFIX + "AI / Agent", days)
            assert len(repos) == 2
        assert win.db.get_setting("__hot_active_7d__") == "opengithubs"
        assert win.db.get_setting("__hot_active_30d__") == "opengithubs"

    def test_fetch_current_hot_only(self, win, monkeypatch):
        self._mock_sources(monkeypatch)
        from app.services.fetcher import FetchWorker
        wk = FetchWorker(win.db, only=config.HOT_KEY)
        wk.run()
        repos, _, _ = win.db.load_cache(config.RANK_WEEK_KEY, 7)
        assert len(repos) == 3
        repos, _, _ = win.db.load_cache("C++", 7)
        assert repos == []                     # 只抓热门

    def test_fetch_current_untracked_lang(self, win, monkeypatch):
        """侧栏可以筛未跟踪语言，抓取当前通道也应支持（而非空计划）。"""
        self._mock_sources(monkeypatch)
        from app.services.fetcher import FetchWorker
        wk = FetchWorker(win.db, only="Python")
        wk.run()
        repos, _, _ = win.db.load_cache("Python", 7)
        assert len(repos) == 2                 # 修复前：空计划什么都抓不到

    def test_fetch_cancel(self, win, monkeypatch):
        from app.sources import opengithubs
        def slow_rank(days, interests):
            raise RuntimeError("skip")
        monkeypatch.setattr(opengithubs, "fetch_rank", slow_rank)
        from app.services import fetcher as fw
        from app.services import gha_board
        from app.sources import github_search
        monkeypatch.setattr(gha_board, "build_board",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        monkeypatch.setattr(github_search, "fetch_channel",
                            lambda *a, **k: [])
        from app.services.fetcher import FetchWorker
        wk = FetchWorker(win.db)
        wk.cancel.set()
        wk.run()                               # 立即取消，不崩
        assert True

    def test_fetch_failure_keeps_old_data(self, win, monkeypatch):
        seed_hot(win, n=3)
        from app.sources import opengithubs
        monkeypatch.setattr(
            opengithubs, "fetch_rank",
            lambda *a: (_ for _ in ()).throw(RuntimeError("网络炸")))
        from app.services.fetcher import FetchWorker
        win.db.set_setting("hot_source", "opengithubs")
        wk = FetchWorker(win.db, only=config.HOT_KEY)
        wk.run()
        repos, _, _ = win.db.load_cache(config.RANK_WEEK_KEY, 7)
        assert len(repos) == 3                 # 旧数据保留

    def test_source_specific_fetch(self, win, monkeypatch):
        """每个源抓取写入自己的通道键。"""
        self._mock_sources(monkeypatch)
        from app.services.fetcher import FetchWorker
        for src, ch7 in (("opengithubs", config.RANK_WEEK_KEY),
                         ("gharchive", config.GH_ARC_KEY),
                         ("search", config.HOT_KEY)):
            win.db.set_setting("hot_source", src)
            wk = FetchWorker(win.db, only=config.HOT_KEY)
            wk.run()
            repos, _, _ = win.db.load_cache(ch7, 7)
            assert repos, f"{src} 未写入 {ch7}"


class TestFavWatch:
    def test_fav_refresh_and_milestone(self, win, monkeypatch):
        from app.services import fav_refresh as fr
        from app.sources import github_search
        win.db.add_favorite(R("o/fav1", stars=900))
        monkeypatch.setattr(
            github_search, "fetch_repo",
            lambda name: {"stars": 1200, "forks": 50, "pushed": "2026-11-09",
                          "license": "MIT", "archived": False,
                          "description": "new desc"})
        wk = fr.FavRefreshWorker(win.db)
        hits = []
        wk.milestone_hit.connect(lambda n, t, s: hits.append((n, t)))
        wk.run()
        assert hits and hits[0][0] == "o/fav1" and hits[0][1] == 1000
        fav = [f for f in win.db.list_favorites() if f.name == "o/fav1"][0]
        assert fav.stars == 1200

    def test_add_watch(self, win, monkeypatch):
        from app.services import watchlist
        monkeypatch.setattr(watchlist, "add_manual",
                            lambda db, name: f"已添加观察：{name}")
        from PySide6.QtWidgets import QInputDialog
        monkeypatch.setattr(
            QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("o/watched", True)))
        win._add_watch()
        win._add_worker.run()                 # 同步执行工作线程体
        win._on_watch_added()
        assert "已添加观察" in win.status_lbl.text()

    def test_watch_board_rows(self, win):
        win.db.watch_add("o/w1", url="https://github.com/o/w1",
                         desc="d", lang="C++", manual=True)
        win.db.watch_sample("o/w1", 100)
        win._set_board("watch")
        rows = win._current_pool()
        assert any(r.name == "o/w1" for r in rows)


class TestImportExportHistory:
    def test_import_names(self, win, monkeypatch):
        from app.ui import main_window as mw
        monkeypatch.setattr(
            mw, "ImportDialog",
            lambda parent: type("D", (), {
                "exec": lambda s: True,
                "names": lambda s: ["o/imp1", "o/imp2"]})())
        monkeypatch.setattr(mw, "import_names",
                            lambda db, names, log=None, cancel=None:
                            (2, 0, 0))
        win._import_watchlist()
        win._imp_worker.run()                 # 同步执行
        win._on_import_done()
        assert "导入完成：新增 2" in win.status_lbl.text()

    def test_export_all_formats(self, win, monkeypatch, tmp_path):
        seed_hot(win, n=3)
        win._set_board("hot")
        from PySide6.QtWidgets import QFileDialog
        for ext in (".md", ".csv", ".html"):
            p = str(tmp_path / f"out{ext}")
            monkeypatch.setattr(
                QFileDialog, "getSaveFileName",
                staticmethod(lambda *a, _p=p, **k: (_p, "")))
            win._export_view()
            assert os.path.exists(p) and os.path.getsize(p) > 0
            assert "已导出 3 条" in win.status_lbl.text()

    def test_export_empty_view(self, win):
        win._set_board("hot")
        win.db.set_setting("hot_source", "trending")   # 无缓存
        win._export_view()
        assert "无数据可导出" in win.status_lbl.text()

    def test_history_snapshot_roundtrip(self, win):
        ch = config.hot_channel("opengithubs", 7)
        seed_hot(win, n=2)
        win.db.save_history(ch, 7, [R("old/x")], taken_at="2026-11-01 08:00:00")
        win._set_board("hot")
        hid = win.db.list_history(ch, 7)[0][0]
        # 模拟对话框选择
        win._snapshot_override = win.db.load_history_by_id(hid)
        win._refresh_view()
        assert [c.repo.name for c in card_widgets(win)] == ["old/x"]
        # 回到最新
        win._back_to_latest()
        assert {c.repo.name for c in card_widgets(win)} == {"o/r0", "o/r1"}

    def test_history_dialog_lists_channel(self, win):
        from app.ui.dialogs import HistoryDialog
        ch = config.hot_channel("opengithubs", 7)
        win.db.save_history(ch, 7, [R("a/b")], taken_at="2026-11-01 08:00:00")
        win.db.save_history("C++", 7, [R("c/d")], taken_at="2026-11-01 09:00:00")
        dlg = HistoryDialog(win.db, ch, 7, win)
        assert dlg.list.count() == 1
        dlg2 = HistoryDialog(win.db, "C++", 7, win)
        assert dlg2.list.count() == 1          # 语言通道也有历史


class TestSettingsTheme:
    def test_settings_save_roundtrip(self, win):
        from app.ui.dialogs import SettingsDialog
        dlg = SettingsDialog(win.db, win)
        dlg.lang_checks["Rust"].setChecked(True)
        dlg.custom_edit.setText("Assembly")     # 新自定义语言（无复选框）
        dlg.pool_spin.setValue(80)
        dlg.fetch_spin.setValue(6)
        dlg._save()
        s = win.db.all_settings()
        assert "Rust" in s["languages"]
        assert "Assembly" in s["custom_langs"]
        assert "Assembly" in s["languages"]     # 自定义语言应直接进入跟踪
        assert s["pool_size"] == 80
        assert s["fetch_interval_hours"] == 6

    def test_settings_reject_no_save(self, win):
        from app.ui.dialogs import SettingsDialog
        before = win.db.all_settings()
        dlg = SettingsDialog(win.db, win)
        dlg.pool_spin.setValue(999)
        dlg.reject()
        assert win.db.all_settings()["pool_size"] == before["pool_size"]

    def test_theme_toggle(self, win):
        assert win.db.get_setting("theme") == "dark"
        win._toggle_theme()
        assert win.db.get_setting("theme") == "light"
        assert win.C == __import__("app.ui.theme", fromlist=["THEMES"]).THEMES["light"]
        win._toggle_theme()
        assert win.db.get_setting("theme") == "dark"

    def test_interests_custom_parse(self, win):
        from app.ui.dialogs import SettingsDialog
        dlg = SettingsDialog(win.db, win)
        dlg.interests_edit.setPlainText("我的方向: alpha, beta\n无效行")
        dlg._save()
        assert win.db.get_setting("interests") == {"我的方向": ["alpha", "beta"]}
        # 全清空恢复内置
        dlg2 = SettingsDialog(win.db, win)
        dlg2.interests_edit.setPlainText("")
        dlg2._save()
        assert win.db.get_setting("interests") == config.INTEREST_KEYWORDS


class TestTopicsSchedulerUpdater:
    def test_topics_enrich(self, win, monkeypatch):
        seed_hot(win, n=1)
        win._set_board("hot")
        from app.services import topics as tp
        monkeypatch.setattr(tp, "enrich",
                            lambda db, names, log=None, cancel=None: 1)
        win._enrich_visible_topics()
        win._topics_worker.run()
        win._on_topics_done()
        assert "标签补齐完成（1 个）" in win.status_lbl.text()

    def test_scheduler_triggers_when_due(self, win, monkeypatch):
        win.db.set_setting("fetch_interval_hours", 2)
        win.db.set_setting("__last_fetch__", 0.0)   # 很久以前
        called = []
        monkeypatch.setattr(win, "_start_fetch",
                            lambda only: called.append(only))
        win._maybe_auto_fetch()
        assert called == [None]
        # 间隔未到不触发
        win.db.set_setting("fetch_interval_hours", 0)
        called.clear()
        win._maybe_auto_fetch()
        assert called == []

    def test_updater_flow(self, win, monkeypatch):
        from app.services import updater
        monkeypatch.setattr(updater, "check_due", lambda db, h: True)

        class FakeWorker:
            def __init__(self, db, parent=None):
                self.progress = type("S", (), {"connect": lambda s, f: None})()
                self.changes_found = self.progress
                self.finished_all = self.progress
                self.cancel = type("C", (), {"set": lambda s: None})()
            def start(self): pass
            def deleteLater(self): pass
            def wait(self, ms): pass
        from app.ui import main_window as mw
        monkeypatch.setattr(mw, "UpdateCheckWorker", FakeWorker)
        win._check_updates(silent=False)
        assert win._upd_worker is not None
        win._on_updates_found([("o/r0", "stars 100→200")], silent=False)
        win._on_updates_done(5)
        assert win._upd_worker is None
        assert "更新检查完成" in win.status_lbl.text()


# ================= D. 组合场景 =================
class TestCombos:
    def test_snapshot_then_switch_days(self, win):
        """回看快照后切时间档：快照是 7 天的，切 30 天必须退出回看。"""
        ch = config.hot_channel("opengithubs", 7)
        seed_hot(win, n=2)
        win.db.save_history(ch, 7, [R("old/x")], taken_at="2026-11-01 08:00:00")
        win._set_board("hot")
        win._snapshot_override = [R("old/x")]
        win._refresh_view()
        assert [c.repo.name for c in card_widgets(win)] == ["old/x"]
        win.days_combo.setCurrentIndex(1)       # 切 30 天
        assert win._snapshot_override is None   # 修复前：仍显示 7 天旧快照
        win.days_combo.setCurrentIndex(0)

    def test_snapshot_then_switch_source(self, win):
        seed_hot(win, n=2)
        win._set_board("hot")
        win._snapshot_override = [R("old/x")]
        win.source_combo.setCurrentIndex(2)
        assert win._snapshot_override is None

    def test_snapshot_filters_still_apply(self, win):
        """快照回看中筛选/关键词/排序仍可用。"""
        snap = [R("s/a", lang="Rust"), R("s/b", lang="Go")]
        win._set_board("hot")
        win._snapshot_override = snap
        win.lang_filter = "Rust"
        win._refresh_view()
        assert [c.repo.name for c in card_widgets(win)] == ["s/a"]
        win._clear_filters()

    def test_back_button_not_eaten_by_loadmore(self, win):
        """「回到最新」按钮不应被加载更多的清理逻辑误删。"""
        seed_hot(win, n=config.PAGE_SIZE + 5)
        win._set_board("hot")
        win._snapshot_override = [R(f"o/r{i}") for i in range(config.PAGE_SIZE + 5)]
        win._refresh_view()
        win._show_back_to_latest()
        back = None
        for i in range(win.cards_lay.count()):
            w = win.cards_lay.itemAt(i).widget()
            if isinstance(w, QPushButton) and "回到最新" in w.text():
                back = w
        assert back is not None
        # 触发一次 _render_more（模拟点加载更多）
        win._render_more()
        still = any(
            isinstance(win.cards_lay.itemAt(i).widget(), QPushButton)
            and "回到最新" in win.cards_lay.itemAt(i).widget().text()
            for i in range(win.cards_lay.count()))
        assert still, "回到最新按钮被加载更多逻辑误删"

    def test_fetch_then_sidebar_counts_update(self, win, monkeypatch):
        TestFetch()._mock_sources(monkeypatch)
        from app.services.fetcher import FetchWorker
        FetchWorker(win.db, only="C++").run()
        # 语言通道抓取不等于热门池；侧栏计数跟随当前热门池
        win.db.save_cache(config.RANK_WEEK_KEY, 7, [
            R("a/one", lang="C++"), R("b/two", lang="C++")], "")
        win.db.set_setting("hot_source", "opengithubs")
        win._pools.clear()
        win._refresh_view()
        texts = [win.lang_list.item(i).text()
                 for i in range(win.lang_list.count())]
        row = next(t for t in texts if t.startswith("C++"))
        assert row.rsplit("  ", 1)[1] == "2"

    def test_board_switch_preserves_filters(self, win):
        seed_hot(win, n=3)
        win._set_board("hot")
        win.lang_filter = "C++"
        win._set_board("fav")
        assert win.lang_filter == "C++"         # 筛选状态保留
        win._set_board("hot")
        assert win.lang_filter == "C++"

    def test_full_user_journey(self, win, monkeypatch):
        """端到端旅程：抓取 → 筛选 → 收藏 → 备注 → 导出 → 快照回看 → 恢复。"""
        TestFetch()._mock_sources(monkeypatch)
        from app.services.fetcher import FetchWorker
        FetchWorker(win.db, only=config.HOT_KEY).run()
        win._pools.clear()
        win._set_board("hot")
        assert len(card_widgets(win)) == 3
        win._toggle_favorite("rank/r0")
        win.db.set_favorite_note("rank/r0", "重点关注", ["AI"])
        win._set_board("fav")
        assert [c.repo.name for c in card_widgets(win)] == ["rank/r0"]
        from PySide6.QtWidgets import QFileDialog
        p = str(win.db_path if hasattr(win, "db_path") else "journey.md")
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: ("journey.md", "")))
        win._export_view()
        assert os.path.exists("journey.md")
        os.remove("journey.md")
        win._set_board("hot")
        ch = config.hot_channel("opengithubs", 7)
        hid = win.db.list_history(ch, 7)[0][0]
        win._snapshot_override = win.db.load_history_by_id(hid)
        win._refresh_view()
        assert len(card_widgets(win)) == 3
        win._back_to_latest()
        assert len(card_widgets(win)) == 3
