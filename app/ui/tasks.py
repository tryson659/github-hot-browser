# -*- coding: utf-8 -*-
"""后台任务登记（R02）。

主窗口创建的 QThread 全部登记到这里；关闭窗口时先协作取消，再等所有
线程真正结束才关闭数据库。QThread 由窗口 parent 持有，窗口只在没有
活动线程时才接受关闭，避免"父对象销毁时线程仍在运行"的生命周期风险。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Signal


class TaskRegistry(QObject):
    all_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._threads: set[QThread] = set()

    def track(self, thread: QThread):
        if not isinstance(thread, QThread):
            # 测试替身或非 Qt 任务没有 QThread 生命周期，不参与关闭等待。
            # 生产代码中的后台任务全部是 QThread。
            return thread
        self._threads.add(thread)
        thread.finished.connect(self._on_finished)
        return thread

    def _on_finished(self):
        sender = self.sender()
        if sender in self._threads:
            self._threads.discard(sender)
        if not self._threads:
            self.all_finished.emit()

    def active(self) -> list[QThread]:
        return [t for t in self._threads if t.isRunning()]

    def has_active(self) -> bool:
        return any(t.isRunning() for t in self._threads)

    def cancel_all(self):
        """尽力协作取消：cancel 事件优先，其次 stop()；不调用 terminate。"""
        for thread in list(self._threads):
            event = getattr(thread, "cancel", None)
            if isinstance(event, threading.Event):
                event.set()
            stop = getattr(thread, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
