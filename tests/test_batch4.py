# -*- coding: utf-8 -*-
"""批4 回归测试：topics 缓存/筛选、定时抓取、兴趣自定义、Release、设置项。"""
import time

import pytest

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.services import topics as topics_svc


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


# ---------------- topics ----------------
class TestTopics:
    def test_cache_roundtrip_and_expiry(self, db):
        assert db.get_topics("a/b") is None
        db.save_topics("a/b", ["game", "engine"])
        assert db.get_topics("a/b") == ["game", "engine"]
        # 手工改老 → 过期
        db._write(lambda c: c.execute(
            "UPDATE repo_topics SET at=? WHERE name=?",
            (time.time() - 8 * 86400, "a/b")))
        assert db.get_topics("a/b") is None

    def test_missing(self, db):
        db.save_topics("a/b", ["x"])
        assert db.topics_missing(["a/b", "c/d"]) == ["c/d"]

    def test_candidates_top_n(self):
        pool = [make_repo(f"r/{i}", topics=["game", f"t{i}"]) for i in range(3)]
        pool.append(make_repo("r/x", topics=["game"]))
        cands = topics_svc.candidates(pool)
        assert cands[0] == "game"          # 频率最高
        long_tag = "t" * 30
        pool.append(make_repo("r/y", topics=[long_tag]))
        assert long_tag not in topics_svc.candidates(pool)   # 超长过滤

    def test_enrich_mock(self, db, monkeypatch):
        monkeypatch.setattr(topics_svc, "eco_topics", lambda n: ["a", "b"])
        n = topics_svc.enrich(db, ["x/y", "z/w"], log=lambda *_: None)
        assert n == 2
        assert db.get_topics("x/y") == ["a", "b"]
        # 有缓存不再请求
        n2 = topics_svc.enrich(db, ["x/y"], log=lambda *_: None)
        assert n2 == 0


# ---------------- Release 缓存 ----------------
class TestRelease:
    def test_roundtrip(self, db):
        assert db.get_release("a/b") is None
        db.save_release("a/b", "v1.2.3", "2026-11-01")
        assert db.get_release("a/b") == {"tag": "v1.2.3", "date": "2026-11-01"}


# ---------------- UI 批4 ----------------
class TestUIBatch4:
    @pytest.fixture()
    def win(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        db.set_setting("hot_source", "search")   # 这些用例直接写 HOT_KEY 缓存
        w = MainWindow(db, start_sync=False)
        yield w
        w.close()

    def test_tag_filter(self, win, db):
        db.save_cache("🔥本周热门", 7, [
            make_repo("a/b", topics=["vulkan", "engine"]),
            make_repo("c/d", topics=["web"]),
        ])
        win._pools.clear()
        win._refresh_view()
        assert win._rendered == 2
        win._pick_tag("vulkan")
        assert win._rendered == 1
        assert win.tag_filter == "vulkan"
        win._clear_filters()
        assert win.tag_filter is None and win._rendered == 2

    def test_tag_filter_uses_db_cache(self, win, db):
        # 卡片 topics 为空但 DB 缓存有标签 → 也能筛中
        db.save_cache("🔥本周热门", 7, [make_repo("a/b", topics=[])])
        db.save_topics("a/b", ["ecs"])
        win._pools.clear()
        win._refresh_view()
        win.tag_filter = "ecs"
        assert len(win._filtered()) == 1

    def test_tag_combo_candidates(self, win, db):
        db.save_cache("🔥本周热门", 7, [
            make_repo("a/b", topics=["game", "engine"]),
            make_repo("c/d", topics=["game"]),
        ])
        win._pools.clear()
        win._refresh_view()
        texts = [win.tag_combo.itemText(i)
                 for i in range(win.tag_combo.count())]
        assert "game" in texts and "engine" in texts

    def test_scheduler_disabled_by_default(self, win, db):
        win._maybe_auto_fetch()
        assert win._worker is None           # 默认 0=关闭，不触发

    def test_scheduler_triggers_when_due(self, win, db, monkeypatch):
        db.set_setting("fetch_interval_hours", 2)
        db.set_setting("__last_fetch__", time.time() - 3 * 3600)
        called = []
        monkeypatch.setattr(win, "_start_fetch", lambda only: called.append(only))
        win._maybe_auto_fetch()
        assert called == [None]

    def test_scheduler_not_due(self, win, db, monkeypatch):
        db.set_setting("fetch_interval_hours", 2)
        db.set_setting("__last_fetch__", time.time())
        called = []
        monkeypatch.setattr(win, "_start_fetch", lambda only: called.append(only))
        win._maybe_auto_fetch()
        assert called == []

    def test_release_button_api(self, win):
        from app.ui.repo_card import RepoCard
        card = RepoCard(1, make_repo(), win.C)
        card.set_release("v2.0", "2026-11-09")
        assert card.rel_btn.text() == "🚀 v2.0"


# ---------------- 设置：兴趣自定义 ----------------
class TestInterestsSettings:
    def test_parse_lines(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.dialogs import SettingsDialog
        dlg = SettingsDialog(db)
        dlg.interests_edit.setPlainText(
            "图形: vulkan, metal\n\n服务端: grpc, asio\n无效行没有冒号")
        dlg._save()
        interests = db.get_setting("interests")
        assert interests == {"图形": ["vulkan", "metal"],
                             "服务端": ["grpc", "asio"]}

    def test_empty_restores_builtin(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.dialogs import SettingsDialog
        dlg = SettingsDialog(db)
        dlg.interests_edit.setPlainText("")
        dlg._save()
        assert db.get_setting("interests") == config.INTEREST_KEYWORDS

    def test_intervals_saved(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.dialogs import SettingsDialog
        dlg = SettingsDialog(db)
        dlg.fetch_spin.setValue(6)
        dlg.upd_spin.setValue(24)
        dlg._save()
        assert db.get_setting("fetch_interval_hours") == 6
        assert db.get_setting("update_interval_hours") == 24
