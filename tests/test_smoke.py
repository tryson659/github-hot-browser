# -*- coding: utf-8 -*-
"""批1 回归测试：存储层 / 分类 / 迁移 / UI 冒烟。

运行：.venv/Scripts/python -m pytest tests/ -q
"""
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from app.core.db import Database
from app.core.models import Repo, sort_key
from app.services.classify import classify_domain, match_interest


def make_repo(name="a/b", stars=100, **kw):
    d = dict(name=name, url=f"https://github.com/{name}", stars=stars,
             forks=10, language="C++", description="demo",
             created="2026-01-01", pushed="2026-11-01", domain="开发工具")
    d.update(kw)
    return Repo.from_dict(d)


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


# ---------------- 存储层 ----------------
class TestDatabase:
    def test_settings_roundtrip(self, db):
        db.set_setting("theme", "light")
        assert db.get_setting("theme") == "light"
        assert db.get_setting("pool_size") == 50          # 默认值已注入
        db.set_setting("languages", ["Rust"])
        assert db.all_settings()["languages"] == ["Rust"]

    def test_cache_roundtrip(self, db):
        repos = [make_repo("a/b", 100), make_repo("c/d", 200)]
        db.save_cache("C++", 7, repos, "测试期")
        got, ts, period = db.load_cache("C++", 7)
        assert [r.name for r in got] == ["a/b", "c/d"]
        assert got[1].stars == 200
        assert period == "测试期" and ts > 0
        assert db.load_cache("不存在", 7) == ([], 0.0, "")

    def test_history_baseline_window(self, db):
        r = make_repo("a/b", 100)
        old = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        db.save_history("C++", 7, [r], taken_at=old)
        db.save_history("C++", 7, [make_repo("a/b", 150)], taken_at=recent)
        base = db.baseline("C++", 7)
        assert base == {"a/b": 100}           # 只用 6~8 天窗口快照
        assert db.baseline("C++", 30) == {}   # 不同档互不影响

    def test_baseline_empty_without_window(self, db):
        db.save_history("C++", 7, [make_repo()],
                        taken_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        assert db.baseline("C++", 7) == {}

    def test_favorites_and_tombstone(self, db):
        r = make_repo("a/b", 500)
        db.add_favorite(r)
        assert db.is_favorite("a/b")
        meta = db.favorite_meta("a/b")
        assert meta["saved_stars"] == 500
        db.set_favorite_note("a/b", "值得跟进", ["引擎", "底层"])
        db.remove_favorite("a/b")              # 取消收藏
        assert not db.is_favorite("a/b")
        db.add_favorite(r)                     # 再收藏
        meta = db.favorite_meta("a/b")
        assert meta["note"] == "值得跟进"       # 墓碑保留
        assert meta["tags"] == ["引擎", "底层"]

    def test_thread_safety_smoke(self, db):
        import threading
        def worker(i):
            for j in range(20):
                db.save_cache(f"ch{i}", 7, [make_repo(f"r/{i}-{j}")])
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(db.cached_channels()) == 4


# ---------------- 分类 ----------------
class TestClassify:
    def test_domain(self):
        assert classify_domain("llama.cpp", "LLM inference in C") == "AI / Agent"
        assert classify_domain("x", "a react component library") == "Web / 前端"
        assert classify_domain("y", "nothing special") == "其他"

    def test_interest(self):
        tags, hits = match_interest(
            "my-vulkan-engine", "render engine",
            {"示例方向": ["vulkan", "render"]})
        assert "示例方向" in tags and hits >= 2
        assert match_interest("plain", "nothing")[1] == 0


# ---------------- 排序 ----------------
class TestSort:
    def test_none_last(self):
        a = make_repo("a", growth_7d=None)
        b = make_repo("b", growth_7d=5)
        rs = sorted([a, b], key=sort_key("近7天增长"), reverse=True)
        assert rs[0].name == "b"
        with pytest.raises(ValueError):
            sort_key("不存在")


# ---------------- 迁移 ----------------
class TestMigrate:
    def test_full_migrate(self, db, tmp_path):
        old = tmp_path / "old"
        old.mkdir()
        (old / "settings.json").write_text(json.dumps(
            {"languages": ["Rust"], "pool_size": 80, "theme": "light"}),
            encoding="utf-8")
        (old / "favorites.json").write_text(json.dumps(
            {"a/b": make_repo("a/b", 300).to_dict() | {"saved_stars": 250}}),
            encoding="utf-8")
        (old / "fav_meta.json").write_text(json.dumps(
            {"a/b": {"note": "旧备注", "tags": ["t1"], "milestone": 1000}}),
            encoding="utf-8")
        (old / "trending_cache_v4.json").write_text(json.dumps(
            {"7": {"C++": {"repos": [make_repo("c/d", 9).to_dict()], "period": ""}}}),
            encoding="utf-8")
        hdir = old / "history"
        hdir.mkdir()
        old_snap = (datetime.now() - timedelta(days=7))
        fn = f"7d_C++_{old_snap.strftime('%Y%m%d_%H%M%S')}.json"
        (hdir / fn).write_text(json.dumps(
            {"repos": [make_repo("c/d", 5).to_dict()]}), encoding="utf-8")

        from app.services.migrate import migrate
        stats = migrate(str(old), db, log=lambda *_: None)

        assert db.get_setting("languages") == ["Rust"]
        assert db.get_setting("pool_size") == 80
        assert db.is_favorite("a/b")
        meta = db.favorite_meta("a/b")
        assert meta["note"] == "旧备注" and meta["saved_stars"] == 250
        repos, _, _ = db.load_cache("C++", 7)
        assert repos and repos[0].name == "c/d"
        assert db.baseline("C++", 7) == {"c/d": 5}
        assert stats["favorites"] == 1 and stats["history"] == 1

        # 幂等：重复迁移不重复导入
        stats2 = migrate(str(old), db, log=lambda *_: None)
        assert stats2["favorites"] == 0


# ---------------- UI 冒烟 ----------------
class TestUI:
    @pytest.fixture()
    def win(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        db.set_setting("hot_source", "search")   # 这些用例直接写 HOT_KEY 缓存
        w = MainWindow(db, start_sync=False)     # 测试不起 GH Archive 网络线程
        yield w
        w.close()

    def test_window_builds(self, win):
        assert win.windowTitle().startswith("GitHub 热点")
        assert win.days == 7 and win.board == "hot"

    def test_render_cards_and_filter(self, win, db):
        db.save_cache("🔥本周热门", 7, [
            make_repo("a/b", 100, language="C++", domain="开发工具"),
            make_repo("e/f", 200, language="Rust", domain="其他",
                      description="uniquekeyword"),
        ])
        win._pools.clear()
        win._refresh_view()
        assert win._rendered == 2
        win.kw_edit.setText("uniquekeyword")
        assert win._rendered == 1
        win.kw_edit.clear()
        win.lang_filter = "Rust"
        assert len(win._filtered()) == 1

    def test_favorite_toggle_flow(self, win, db):
        db.save_cache("🔥本周热门", 7, [make_repo("a/b", 100)])
        win._pools.clear()
        win._refresh_view()
        win._toggle_favorite("a/b")
        assert db.is_favorite("a/b")
        win._set_board("fav")
        assert win._rendered == 1
        win._toggle_favorite("a/b")
        assert not db.is_favorite("a/b")

    def test_theme_switch(self, win, db):
        assert db.get_setting("theme") == "dark"
        win._toggle_theme()
        assert db.get_setting("theme") == "light"
        win._toggle_theme()

    def test_card_animation_api(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.repo_card import RepoCard, SkeletonCard
        from app.ui.theme import THEMES
        card = RepoCard(1, make_repo(), THEMES["dark"])
        card.enterEvent(None)
        card.leaveEvent(None)
        card.fade_in()
        card.set_favorite(True)
        sk = SkeletonCard(THEMES["dark"])
        sk.stop()
