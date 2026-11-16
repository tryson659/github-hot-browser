# -*- coding: utf-8 -*-
"""GitHub API 源默认禁用：计划、自动源与 UI 下拉。"""
import pytest
from PySide6.QtWidgets import QApplication

from app.core import config
from app.core.db import Database
from app.services.fetcher import FetchWorker
from app.services import hot_source
from app.ui.main_window import MainWindow


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def test_api_disabled_by_default_plan(db):
    assert db.get_setting("github_api_enabled", False) is False
    w = FetchWorker(db)
    assert w._api_enabled() is False
    assert w._plan() == [("hot", config.HOT_KEY)]
    # 显式语言通道在禁用时也不能抓
    assert FetchWorker(db, only="Python")._plan() == []


def test_api_enabled_plan_includes_channels(db):
    db.set_setting("github_api_enabled", True)
    plan = FetchWorker(db)._plan()
    assert ("hot", config.HOT_KEY) in plan
    assert any(k == "lang" for k, _ in plan)
    assert FetchWorker(db, only="Python")._plan() == [("lang", "Python")]


def test_auto_ignores_opengithubs_when_disabled(db):
    db.save_cache(config.RANK_WEEK_KEY, 7, [])
    db.set_setting("__hot_active_7d__", "opengithubs")
    assert hot_source.channel_for(db, 7, "auto") == config.TREND_KEY
    db.set_setting("github_api_enabled", True)
    assert hot_source.channel_for(db, 7, "auto") == config.RANK_WEEK_KEY


def test_source_combo_blocks_api_items(db, _keep_qapp_alive):
    w = MainWindow(db, start_sync=False)
    try:
        for key in ("opengithubs", "search"):
            idx = w.source_combo.findData(key)
            assert idx >= 0
            assert not w.source_combo.model().item(idx).isEnabled()
        idx = w.source_combo.findData("search")
        w.source_combo.setCurrentIndex(idx)   # 程序化设置也走 _on_source 防守
        w._on_source(idx)
        assert db.get_setting("hot_source", "auto") != "search"
    finally:
        w.close()
