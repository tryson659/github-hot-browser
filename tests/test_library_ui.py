# -*- coding: utf-8 -*-
"""工具库 Qt 入口与 GH Archive 回填提示。"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from app.core.db import Database
from app.core.models import Repo
from app.services import library, tracking
from app.storage.store import Store
from app.ui.main_window import MainWindow


def _repo(name="a/b", **kw):
    data = dict(name=name, url=f"https://github.com/{name}", stars=10,
                forks=2, language="C++", description="demo", pushed="2026-11-01")
    data.update(kw)
    return Repo.from_dict(data)


@pytest.fixture()
def win(_keep_qapp_alive, tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.set_setting("hot_source", "search")
    w = MainWindow(db, start_sync=False)
    yield w
    w.close()
    db.close()


class _FakeAddDialog:
    def __init__(self, db, repos, selected_ids=None, parent=None):
        self.repos = repos
        self.ids = list(selected_ids or [])

    def setWindowTitle(self, _):
        pass

    def exec(self):
        return QDialog.Accepted

    def selected_ids(self):
        return self.ids


class TestLibraryBoard:
    def test_fourth_board_and_empty_hint(self, win):
        assert "lib" in win.board_btns
        win._set_board("lib")
        assert win.board == "lib"
        texts = [win.lang_list.item(i).text() for i in range(win.lang_list.count())]
        assert any("全部工具" in t for t in texts)
        assert any("待分类" in t for t in texts)
        assert not win.collect_btn.isVisible()   # 工具库内不显示“收录当前列表”

    def test_collect_current_uses_dialog(self, win, monkeypatch):
        win.db.save_cache("🔥本周热门", 7, [_repo("a/one"), _repo("b/two")])
        win._pools.clear()
        win._set_board("hot")
        cid = library.categories(win.db)[0]["id"]

        class D(_FakeAddDialog):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.ids = [cid]

        monkeypatch.setattr("app.ui.main_window.LibraryAddDialog", D)
        win._collect_current()
        rows = library.entries(win.db)
        assert {r["name"] for r in rows} == {"a/one", "b/two"}
        assert all(r["categories"] for r in rows)

    def test_collect_one_then_edit_and_remove(self, win, monkeypatch):
        win.db.save_cache("🔥本周热门", 7, [_repo("a/b")])
        win._pools.clear()
        win._set_board("hot")
        cats = library.categories(win.db)
        cid = cats[0]["id"]

        class D(_FakeAddDialog):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.ids = [cid]

        monkeypatch.setattr("app.ui.main_window.LibraryAddDialog", D)
        win._collect_one("a/b")
        assert library.entry(win.db, "a/b")["categories"][0]["id"] == cid

        win._set_board("lib")
        win._refresh_view()
        assert win._rendered == 1
        assert win._lib_cat_map["a/b"] == [cats[0]["name"]]

        # 移出工具库，收藏/追踪保留
        repo = _repo("a/b")
        win.db.add_favorite(repo)
        tracking.add_tracking(win.db, "a/b", store=Store(win.db))
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **kw: QMessageBox.Yes))
        win._remove_from_library("a/b")
        assert library.entry(win.db, "a/b") is None
        assert win.db.is_favorite("a/b")
        repo_row = Store(win.db).repository("a/b")
        assert Store(win.db).tracking(repo_row["id"]) is not None

    def test_category_filter_and_manager_signal(self, win):
        cid = library.categories(win.db)[0]["id"]
        library.add_repo(win.db, _repo("a/b"), categories=[cid])
        win._set_board("lib")
        win._refresh_view()
        win.library_category = cid
        win._refresh_view()
        assert [r.name for r in win._filtered()] == ["a/b"]
        win.library_category = "__uncat__"
        win._refresh_view()
        assert win._filtered() == []

    def test_manual_url_add(self, win, monkeypatch):
        from PySide6.QtWidgets import QInputDialog
        cid = library.categories(win.db)[1]["id"]
        monkeypatch.setattr(
            QInputDialog, "getText",
            staticmethod(lambda *a, **kw: (
                "see https://github.com/a/b/tree/main and c/d", True)))

        class D(_FakeAddDialog):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.ids = [cid]

        monkeypatch.setattr("app.ui.main_window.LibraryAddDialog", D)
        win._add_library_by_name()
        assert {r["name"] for r in library.entries(win.db)} == {"a/b", "c/d"}
        assert all(r["categories"][0]["id"] == cid
                   for r in library.entries(win.db))

    def test_batch_category_and_remove(self, win, monkeypatch):
        cid = library.categories(win.db)[2]["id"]
        library.add_repo(win.db, _repo("a/one"))
        library.add_repo(win.db, _repo("b/two"))
        win._set_board("lib")
        win._refresh_view()

        class D(_FakeAddDialog):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.ids = [cid]

        monkeypatch.setattr("app.ui.main_window.LibraryAddDialog", D)
        win._batch_library_categories()
        assert all(r["categories"][0]["id"] == cid
                   for r in library.entries(win.db))

        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **kw: QMessageBox.Yes))
        win._batch_remove_library()
        assert library.entries(win.db) == []

    def test_manager_delete_moves_entries(self, win, monkeypatch):
        from app.ui.library_dialogs import LibraryManagerDialog
        from PySide6.QtWidgets import QInputDialog
        cats = library.categories(win.db)
        source, target = cats[0], cats[1]
        library.add_repo(win.db, _repo("a/b"), categories=[source["id"]])
        dlg = LibraryManagerDialog(win.db, win)
        dlg._refresh(source["id"])
        monkeypatch.setattr(
            QInputDialog, "getItem",
            staticmethod(lambda *a, **kw: (target["name"], True)))
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **kw: QMessageBox.Yes))
        dlg._delete()
        assert [c["id"] for c in library.entry(win.db, "a/b")["categories"]] == [
            target["id"]]
        dlg.close()


class TestGhaEntry:
    def test_source_switch_shows_backfill_and_coverage(self, win):
        idx = win.source_combo.findData("gharchive")
        win.source_combo.setCurrentIndex(idx)
        win._on_source(idx)
        assert not win.gha_btn.isHidden()
        assert "GH Archive" in win.gha_btn.text()
        assert "账本" in win._gha_coverage_text()
        win._set_board("lib")
        assert win.gha_btn.isHidden()

    def test_backfill_done_starts_rebuild(self, win, monkeypatch):
        called = []
        monkeypatch.setattr(win, "_start_fetch", lambda only: called.append(only))
        win.days = 7
        idx = win.source_combo.findData("gharchive")
        win.source_combo.setCurrentIndex(idx)
        win.db.set_setting("hot_source", "gharchive")
        win.board = "hot"
        win._on_gha_backfill_done({"downloaded": 3, "skipped": 1,
                                   "failed": 0, "days": 7})
        assert called == ["🔥本周热门"]
        assert "回填完成" in win.status_lbl.text()
