# -*- coding: utf-8 -*-
"""批8：GHA 404 热循环 / 状态栏覆盖 / GH Archive 卡片可读性 回归测试。"""
import io
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.db import Database
from app.core.models import Repo
from app.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: 0))
    db = Database(str(tmp_path / "t.db"))
    w = MainWindow(db, start_sync=False)
    yield w
    db.close()


def _404():
    import urllib.error
    return urllib.error.HTTPError("https://x", 404, "Not Found", None,
                                  io.BytesIO(b"404"))


class TestGha404:
    def test_404_treated_as_caught_up(self, tmp_path, monkeypatch):
        """未发布的小时包（404）→ 追平处理，不再热循环。"""
        from app.services import gha_board
        from app.sources import gharchive
        db = Database(str(tmp_path / "t.db"))
        db.gha_set_cursor(gharchive.latest_done_hour())   # 游标 = latest
        # 游标回拨一小时，使 catch_up 尝试下载 latest（404）
        from datetime import datetime, timedelta
        cur = datetime.strptime(db.gha_get_cursor(), "%Y-%m-%d-%H")
        db.gha_set_cursor((cur - timedelta(hours=1)).strftime("%Y-%m-%d-%H"))
        calls = []
        monkeypatch.setattr(
            gharchive, "download_hour",
            lambda day, hour, cancel=None: (calls.append(hour),
                                            (_ for _ in ()).throw(_404()))[1])
        done, caught = gha_board.catch_up(db, log=lambda m: None)
        assert caught is True            # 视为已追平 → 正常休眠而非死循环
        assert len(calls) == 1           # 只试一次
        db.close()

    def test_real_failure_not_caught(self, tmp_path, monkeypatch):
        from app.services import gha_board
        from app.sources import gharchive
        db = Database(str(tmp_path / "t.db"))
        from datetime import datetime, timedelta
        cur = datetime.strptime(gharchive.latest_done_hour(), "%Y-%m-%d-%H")
        db.gha_set_cursor((cur - timedelta(hours=1)).strftime("%Y-%m-%d-%H"))
        monkeypatch.setattr(
            gharchive, "download_hour",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")))
        done, caught = gha_board.catch_up(db, log=lambda m: None)
        assert caught is False
        db.close()


class TestGhaCardReadability:
    def test_build_board_has_description(self, win, monkeypatch):
        from app.services import gha_board
        # 造最近 3 小时的本地数据（用当前 UTC 日期，避免窗口过期）
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        for i in range(3, 0, -1):
            hour = now - timedelta(hours=i)
            win.db.gha_save_hour(hour.strftime("%Y-%m-%d-%H"),
                                 {"o/a": 3, "o/b": 2})
        repos, period = gha_board.build_board(win.db, 7, 10, None)
        assert repos
        for r in repos:
            assert "GH Archive" in r.description   # 不再是空描述
            assert r.stars is None
            assert r.coverage and "小时" in r.coverage


class TestStatusBarPriority:
    def test_gha_status_muted_during_fetch(self, win):
        win._worker = object()              # 抓取进行中
        win.status_lbl.setText("抓取结果重要信息")
        win._on_gha_status("🗄 GH Archive 已追平")
        assert win.status_lbl.text() == "抓取结果重要信息"
        win._worker = None
        win._on_gha_status("🗄 GH Archive 已追平")
        assert win.status_lbl.text() == "🗄 GH Archive 已追平"


class TestRateLimitMessage:
    def test_unknown_reset_message_helpful(self):
        from app.sources import http
        e = http.RateLimitError(None)
        assert "1~5 分钟" in str(e)
