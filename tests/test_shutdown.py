# -*- coding: utf-8 -*-
"""R02：关闭窗口须等全部工作线程结束再关闭数据库。

没有 PySide6 的环境跳过（以导入检查覆盖语法与依赖方向）。
"""
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread                      # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

from app.core.db import Database                        # noqa: E402
from app.ui.main_window import MainWindow               # noqa: E402


class SlowWorker(QThread):
    def __init__(self, seconds=0.3, parent=None):
        super().__init__(parent)
        self.seconds = seconds
        self.done = False

    def run(self):
        time.sleep(self.seconds)
        self.done = True


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(qapp, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)


def test_close_waits_for_running_worker(qapp, tmp_path):
    db = Database(str(tmp_path / "t.db"))
    win = MainWindow(db, start_sync=False)
    worker = SlowWorker(0.4)
    win._tasks.track(worker)
    worker.start()

    win.close()
    assert worker.isRunning() and not db.is_closed   # 线程未结束，库不能关
    _pump(qapp)
    assert worker.done and not worker.isRunning()
    assert db.is_closed                              # 全部结束后才关库
    assert not win.isVisible()


def test_close_without_workers_closes_immediately(qapp, tmp_path):
    db = Database(str(tmp_path / "t.db"))
    win = MainWindow(db, start_sync=False)
    win.close()
    assert db.is_closed
