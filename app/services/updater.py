# -*- coding: utf-8 -*-
"""更新检测：对收藏 + 手动观察项目逐一查询，与基线对比检出变化。

检出类型：Star 增长 / 有新提交 / 被归档。基线存 update_check 表。
触发点：启动后 2 秒（静默）、每次抓取完成后（静默）、手动点击；
静默检查受 update_interval_hours（默认 12 小时）节流。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal

from app.core.db import Database
from app.services import watchlist
from app.sources import github_search


def check_due(db: Database, hours: float) -> bool:
    last = db.get_setting("__last_update_check__", 0.0) or 0.0
    return (time.time() - last) >= hours * 3600


class UpdateCheckWorker(QThread):
    progress = Signal(str)
    changes_found = Signal(list)   # [(name, 变化描述), ...]
    finished_all = Signal(int)     # 检查项目数

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.cancel = threading.Event()

    def run(self):
        names = {r.name for r in self.db.list_favorites()}
        names |= {w["name"] for w in self.db.watch_list(manual_only=True)}
        changes: list[tuple[str, str]] = []
        for i, name in enumerate(sorted(names), 1):
            if self.cancel.is_set():
                break
            try:
                info = github_search.fetch_repo(name)
            except Exception as e:
                self.progress.emit(f"  ⚠️ {name} 检查失败：{e}")
                continue
            base = self.db.update_baseline_get(name)
            descs: list[str] = []
            if base:
                if info["stars"] > base["stars"]:
                    descs.append(f"⭐ +{info['stars'] - base['stars']}")
                if info["pushed"] and info["pushed"] > (base["pushed"] or ""):
                    descs.append(f"📦 新提交 {info['pushed']}")
                if info["archived"]:
                    descs.append("⚠️ 已归档")
                if descs:
                    changes.append((name, "，".join(descs)))
            self.db.update_baseline_set(name, info["stars"], info["pushed"])
            if i % 10 == 0:
                self.progress.emit(f"  更新检查 {i}/{len(names)}")
            time.sleep(1.2)
        self.db.set_setting("__last_update_check__", time.time())
        if changes:
            self.changes_found.emit(changes)
        self.finished_all.emit(len(names))
