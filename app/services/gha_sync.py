# -*- coding: utf-8 -*-
"""GH Archive 后台自适应增量同步线程。

门控逻辑（与旧版一致）：每分钟检查游标是否落后于最近已结束小时，
落后即追平（每轮最多 6 个小时包）；追平后检查间隔按 10→60 分钟逐轮拉长。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from app.core.db import Database
from app.services import gha_board
from app.sources import gharchive
from app.storage.store import Store


class GhaBackfillThread(QThread):
    """用户触发的近 7/30 天回填；不改变自动同步的自适应节奏。"""
    status = Signal(str)
    finished_stats = Signal(object)

    def __init__(self, db: Database, days: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.days = days
        self.cancel = threading.Event()

    def run(self):
        store = Store(self.db)
        run_id = store.start_source_run(gha_board.ARCHIVE_SOURCE)
        try:
            stats = gha_board.backfill(
                self.db, self.days, log=lambda m: self.status.emit(m),
                cancel=self.cancel)
            store.finish_source_run(
                run_id, "partial" if stats["failed"] else "success",
                ok_count=stats["downloaded"], fail_count=stats["failed"])
            if stats["downloaded"]:
                self.db.gha_cleanup()
            self.finished_stats.emit(stats)
        except Exception as e:
            store.finish_source_run(run_id, "failed", error=str(e))
            self.finished_stats.emit({"error": str(e), "days": self.days})

    def stop(self):
        self.cancel.set()


class GhaSyncThread(QThread):
    status = Signal(str)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.cancel = threading.Event()
        self._idle_minutes = 10      # 追平后的检查间隔，逐轮拉长到 60

    def run(self):
        while not self.cancel.is_set():
            if not self.db.get_setting("gha_hourly", True):
                self._sleep(60)
                continue
            latest = gharchive.latest_done_hour()
            if self.db.gha_get_cursor() >= latest:
                # 已追平：按自适应间隔休眠
                self._sleep(self._idle_minutes * 60)
                self._idle_minutes = min(60, self._idle_minutes + 10)
                continue
            self._idle_minutes = 10   # 有活干，重置间隔
            store = Store(self.db)
            run_id = store.start_source_run(gha_board.ARCHIVE_SOURCE)
            try:
                done, caught = gha_board.catch_up(
                    self.db, max_hours=6,
                    log=lambda m: self.status.emit(m), cancel=self.cancel)
                failed = len(store.archive_hours(
                    gha_board.ARCHIVE_SOURCE, status="failed"))
                store.finish_source_run(
                    run_id, "partial" if failed else "success",
                    ok_count=done, fail_count=failed)
                if done:
                    self.db.gha_cleanup()
                if caught:
                    self.status.emit("🗄 GH Archive 已追平")
                elif done == 0:
                    # 一个包都没拿到（网络异常等）：兜底休眠，防热循环
                    self._sleep(120)
            except Exception as e:
                store.finish_source_run(run_id, "failed", error=str(e))
                self.status.emit(f"🗄 同步异常：{e}，3 分钟后重试")
                self._sleep(180)

    def _sleep(self, seconds: float):
        """可中断睡眠。"""
        self.cancel.wait(seconds)

    def stop(self):
        self.cancel.set()
