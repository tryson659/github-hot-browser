# -*- coding: utf-8 -*-
"""批3 回归测试：导入解析、导出、翻译缓存、更新检测、历史回看、入口加固。"""
import json
import os
from datetime import datetime, timedelta

import pytest

from app.core.db import Database
from app.core.models import Repo
from app.services import exporter, importer, translate as tr


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def make_repo(name="a/b", stars=100, **kw):
    d = dict(name=name, url=f"https://github.com/{name}", stars=stars,
             forks=10, language="C++", description="demo desc",
             created="2026-01-01", pushed="2026-11-01", domain="开发工具")
    d.update(kw)
    return Repo.from_dict(d)


# ---------------- 名单解析 ----------------
class TestParseNames:
    def test_links_and_bare(self):
        text = ("https://github.com/ggml-org/llama.cpp\n"
                "torvalds/linux\n"
                "看这个项目 github.com/a/b 很不错")
        assert importer.parse_repo_names(text) == \
            ["ggml-org/llama.cpp", "torvalds/linux", "a/b"]

    def test_dedupe_keep_order(self):
        assert importer.parse_repo_names("a/b a/b c/d") == ["a/b", "c/d"]

    def test_strip_git_and_dot(self):
        assert importer.parse_repo_names("a/b.git, c/d.") == ["a/b", "c/d"]

    def test_no_false_positive_on_deep_path(self):
        # 裸 a/b/c 三级路径不应漏出假项目 a/b（R07）
        assert importer.parse_repo_names("a/b/c") == []
        # GitHub 链接的深层路径则归一到仓库根（tree/issues 等子路径）
        assert importer.parse_repo_names("github.com/a/b/c") == ["a/b"]
        assert importer.parse_repo_names(
            "https://github.com/a/b/tree/main/src") == ["a/b"]
        assert importer.parse_repo_names("https://gitlab.com/a/b") == []

    def test_cap(self, db, monkeypatch):
        monkeypatch.setattr(importer, "eco_repo",
                            lambda n: {"stars": 1, "language": "-",
                                       "description": "", "pushed": "",
                                       "archived": False})
        with pytest.raises(ValueError):
            importer.import_names(db, [f"r/{i}" for i in range(501)])

    def test_import_flow(self, db, monkeypatch):
        monkeypatch.setattr(importer, "eco_repo",
                            lambda n: {"stars": 42, "language": "C++",
                                       "description": "d", "pushed": "",
                                       "archived": False})
        added, skipped, failed = importer.import_names(db, ["a/b", "c/d"])
        assert (added, skipped, failed) == (2, 0, 0)
        added2, skipped2, _ = importer.import_names(db, ["a/b"])
        assert (added2, skipped2) == (0, 1)
        w = [x for x in db.watch_list() if x["name"] == "a/b"][0]
        assert w["manual"] == 1
        assert db.watch_samples("a/b")  # 首个采样点已记录


# ---------------- 导出 ----------------
class TestExport:
    def test_markdown(self):
        md = exporter.to_markdown([make_repo(growth_7d=5)], "本周热门", 7,
                                  "近7天增长")
        assert "[a/b](https://github.com/a/b)" in md
        assert "增长指标" in md and "来源" in md

    def test_csv_utf8_sig(self, tmp_path):
        p = str(tmp_path / "out.csv")
        exporter.to_csv([make_repo()], p)
        with open(p, "rb") as f:
            assert f.read(3) == b"\xef\xbb\xbf"     # Excel 中文不乱码

    def test_html_escapes(self):
        r = make_repo(description="<script>alert(1)</script>")
        out = exporter.to_html([r], "b", 7, "s")
        assert "<script>alert" not in out
        assert "&lt;script&gt;" in out


# ---------------- 翻译 ----------------
class TestTranslate:
    def test_cjk_skip(self, db):
        assert tr.translate("这是中文", db) == "这是中文"
        assert tr.has_cjk("这是中文") and not tr.has_cjk("hello")

    def test_cache_hit_no_network(self, db):
        db.save_translation("hello world", "你好世界")
        assert tr.translate("hello world", db) == "你好世界"

    def test_network_mock_and_cache(self, db, monkeypatch):
        class FakeResp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return json.dumps({"responseData":
                                   {"translatedText": "你好"}}).encode()
        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
        assert tr.translate("hello", db) == "你好"
        assert db.get_translation("hello") == "你好"
        # 第二次走缓存（撤掉 mock 也应成功）
        monkeypatch.undo()
        assert tr.translate("hello", db) == "你好"


# ---------------- 更新检测 ----------------
class TestUpdater:
    def test_baseline_roundtrip(self, db):
        assert db.update_baseline_get("a/b") is None
        db.update_baseline_set("a/b", 100, "2026-11-01")
        base = db.update_baseline_get("a/b")
        assert base["stars"] == 100 and base["pushed"] == "2026-11-01"

    def test_check_due(self, db):
        from app.services.updater import check_due
        assert check_due(db, 12)                       # 从未检查 → 到期
        db.set_setting("__last_update_check__",
                       __import__("time").time())
        assert not check_due(db, 12)

    def test_worker_detects_changes(self, db, monkeypatch):
        from app.services.updater import UpdateCheckWorker
        db.add_favorite(make_repo("a/b", 100))
        db.update_baseline_set("a/b", 90, "2026-10-01")
        monkeypatch.setattr(
            "app.services.updater.github_search.fetch_repo",
            lambda n: {"stars": 120, "forks": 1, "pushed": "2026-11-08",
                       "license": "-", "archived": False,
                       "description": "", "language": "C++"})
        monkeypatch.setattr("time.sleep", lambda *_: None)
        got = []
        w = UpdateCheckWorker(db)
        w.changes_found.connect(got.extend)
        w.run()
        assert got == [("a/b", "⭐ +30，📦 新提交 2026-11-08")]
        assert db.update_baseline_get("a/b")["stars"] == 120


# ---------------- UI 批3 ----------------
class TestUIBatch3:
    @pytest.fixture()
    def win(self, db):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        db.set_setting("hot_source", "search")   # 这些用例直接写 HOT_KEY 缓存
        w = MainWindow(db, start_sync=False)
        yield w
        w.close()

    def test_rank_badges(self, win, db):
        ch = "🔥本周热门"
        old = [make_repo("x/1", 300), make_repo("x/2", 200)]
        new = [make_repo("x/2", 500), make_repo("x/3", 400), make_repo("x/1", 100)]
        db.save_history(ch, 7, old,
                        taken_at=(datetime.now() - timedelta(hours=2)
                                  ).strftime("%Y-%m-%d %H:%M:%S"))
        db.save_history(ch, 7, new)      # 最新快照
        db.save_cache(ch, 7, new)
        win._pools.clear()
        win._refresh_view()
        assert win._rank_badges.get("x/3") == "🆕"
        assert win._rank_badges.get("x/2") == "▲1"
        assert win._rank_badges.get("x/1") == "▼2"

    def test_snapshot_override(self, win, db):
        ch = "🔥本周热门"
        db.save_cache(ch, 7, [make_repo("cur/1")])
        db.save_history(ch, 7, [make_repo("old/1")])
        hid = db.list_history(ch, 7)[0][0]
        win._pools.clear()
        win._snapshot_override = db.load_history_by_id(hid)
        assert [r.name for r in win._current_pool()] == ["old/1"]
        win._back_to_latest()
        assert [r.name for r in win._current_pool()] == ["cur/1"]

    def test_export_writes_file(self, win, db, tmp_path):
        db.save_cache("🔥本周热门", 7, [make_repo()])
        win._pools.clear()
        p = tmp_path / "out.md"
        exporter.to_markdown(win._filtered(), "本周热门", 7, "近7天增长")
        p.write_text(exporter.to_markdown(win._filtered(), "b", 7, "s"),
                     encoding="utf-8")
        assert "a/b" in p.read_text(encoding="utf-8")

    def test_card_translate_api(self, win, db):
        from app.ui.repo_card import RepoCard
        card = RepoCard(1, make_repo(), win.C)
        card.set_translation("这是译文")
        assert card.desc_lbl.text() == "这是译文"
