# -*- coding: utf-8 -*-
"""批6：侧栏常驻 / 组合筛选 / 空态提示 回归测试。

修复点：
1. 语言/领域列表常驻（⭐收藏 + 全部 + 各跟踪通道），池空也不消失；
2. 列表重建回声抑制——恢复选中不再误清筛选；
3. 语言×领域可叠加（不再互斥）；
4. 领域筛选在收藏/观察板同样生效；
5. 空数据显示引导提示而非空白。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_repo(name="a/b", lang="C++", domain="开发工具", stars=100, desc="d"):
    return Repo(name=name, url=f"https://github.com/{name}", description=desc,
                language=lang, domain=domain, stars=stars, forks=10,
                growth_7d=5, growth_daily=1.0, pushed="2026-11-09")


@pytest.fixture
def win(qapp, tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.set_setting("hot_source", "search")   # 这些用例直接写 HOT_KEY 缓存
    w = MainWindow(db, start_sync=False)
    yield w
    db.close()


class TestSidebarResident:
    def test_lists_present_with_empty_pool(self, win):
        """池空时语言/领域列表仍在（设置里跟踪的通道）。"""
        win._refresh_view()
        langs = [win.lang_list.item(i).text() for i in range(win.lang_list.count())]
        doms = [win.dom_list.item(i).text() for i in range(win.dom_list.count())]
        assert any("我的收藏" in t for t in langs)
        assert any("全部语言" in t for t in langs)
        assert any(t.startswith("C++") for t in langs)   # 默认跟踪 C++/C
        assert any("全部领域" in t for t in doms)
        assert any("AI / Agent" in t for t in doms)

    def test_counts_from_current_hot_pool(self, win):
        """语言行数量取自当前热门池；不再显示未抓取语言通道的 0。"""
        win.db.save_cache(config.HOT_KEY, 7, [
            make_repo("a/one", lang="C++"),
            make_repo("b/two", lang="C++"),
            make_repo("c/three", lang="Python"),
        ], "")
        win._pools.clear()
        win._refresh_view()
        texts = [win.lang_list.item(i).text()
                 for i in range(win.lang_list.count())]
        cpp = next(t for t in texts if t.startswith("C++"))
        total = next(t for t in texts if t.startswith("全部语言"))
        assert cpp.rsplit("  ", 1)[1] == "2"
        assert total.rsplit("  ", 1)[1] == "3"

    def test_fav_row_click_switches_board(self, win):
        win._refresh_view()
        win._on_lang(win.lang_list.item(0))        # ⭐ 我的收藏
        assert win.board == "fav"

    def test_tag_combo_uses_db_cache(self, win):
        """补齐后的 topics 只存在 DB，也必须进入标签下拉并可筛选。"""
        win.db.save_cache(config.HOT_KEY, 7, [
            make_repo("a/b", lang="C++"),
            make_repo("c/d", lang="C++"),
        ], "")
        win.db.save_topics("a/b", ["ecs", "vulkan"])
        win._pools.clear()
        win._refresh_view()
        texts = [win.tag_combo.itemText(i)
                 for i in range(win.tag_combo.count())]
        assert "ecs" in texts
        idx = win.tag_combo.findData("ecs")
        win.tag_combo.setCurrentIndex(idx)
        assert [r.name for r in win._filtered()] == ["a/b"]


class TestEchoSuppression:
    @staticmethod
    def _row(win, key):
        for i in range(win.lang_list.count()):
            it = win.lang_list.item(i)
            if it.data(Qt.UserRole) == key:
                return it
        return None

    def test_selection_restore_keeps_filter(self, win):
        """选中语言后刷新视图，重建列表的选中恢复不得清除筛选。"""
        win.db.save_cache(config.HOT_KEY, 7,
                          [make_repo(lang="Python")], "")
        win._pools.clear()
        win._refresh_view()
        it = self._row(win, "Python")
        assert it is not None, "侧栏没有 Python 行"
        win._on_lang(it)                           # 模拟点击
        assert win.lang_filter == "Python"
        win._refresh_view()                        # 重建列表 + 恢复选中
        assert win.lang_filter == "Python"         # 未被回声清除
        assert [r.name for r in win._filtered()] == ["a/b"]

    def test_toggle_deselect(self, win):
        win.lang_filter = "C++"
        win._refresh_view()
        it = self._row(win, "C++")
        win._on_lang(it)                           # 再点一次 = 取消
        assert win.lang_filter is None


class TestComboFilter:
    def test_lang_and_domain_stack(self, win):
        """语言×领域可叠加，不再互斥清除。"""
        r1 = make_repo("a/x", lang="C++", domain="开发工具")
        r2 = make_repo("a/y", lang="C++", domain="AI / Agent")
        win.db.save_cache(config.HOT_KEY, 7, [r1, r2], "")
        win._pools.clear()
        win.lang_filter = "C++"
        win.domain_filter = "AI / Agent"
        win._refresh_view()
        assert win.lang_filter == "C++" and win.domain_filter == "AI / Agent"
        assert [r.name for r in win._filtered()] == ["a/y"]

    def test_domain_filter_on_fav_board(self, win):
        win.db.add_favorite(make_repo("a/x", domain="开发工具"))
        win.db.add_favorite(make_repo("a/y", domain="AI / Agent"))
        win._set_board("fav")
        win.domain_filter = "AI / Agent"
        assert [r.name for r in win._filtered()] == ["a/y"]


class TestEmptyHint:
    def _hints(self, win):
        return [win.cards_lay.itemAt(i).widget()
                for i in range(win.cards_lay.count())
                if isinstance(win.cards_lay.itemAt(i).widget(), QLabel)
                and win.cards_lay.itemAt(i).widget().objectName() == "emptyHint"]

    def test_hint_when_source_empty(self, win):
        win.db.set_setting("hot_source", "trending")   # 无缓存的源
        win._set_board("hot")
        hints = self._hints(win)
        assert hints and "抓取" in hints[0].text()

    def test_filter_hint_when_filtered_out(self, win):
        win.db.save_cache(config.HOT_KEY, 7, [make_repo()], "")
        win._pools.clear()
        win.lang_filter = "Rust"     # 池里没有 Rust
        win._refresh_view()
        hints = self._hints(win)
        assert hints and "筛选" in hints[0].text()

    def test_no_hint_when_data(self, win):
        win.db.save_cache(config.HOT_KEY, 7, [make_repo()], "")
        win._pools.clear()
        win._refresh_view()
        assert not self._hints(win)
