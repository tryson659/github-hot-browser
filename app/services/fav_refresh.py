# -*- coding: utf-8 -*-
"""收藏数据刷新：core API 逐项目拉最新状态（60 次/时限额，1.2s 节流）。

顺带：观察名单采样 + Star 里程碑跨档检测（每档只提醒一次）。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal

from app.core.config import MILESTONES
from app.core.db import Database
from app.services import watchlist
from app.sources import github_search


class FavRefreshWorker(QThread):
    progress = Signal(str)
    milestone_hit = Signal(str, int, int)      # (name, 跨过的档位, 最新stars)
    finished_all = Signal(int, int)            # (成功数, 失败数)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.cancel = threading.Event()

    def run(self):
        favs = self.db.list_favorites()
        ok = fail = 0
        for i, repo in enumerate(favs, 1):
            if self.cancel.is_set():
                break
            try:
                info = github_search.fetch_repo(repo.name)
            except Exception as e:
                fail += 1
                self.progress.emit(f"  ⚠️ {repo.name} 刷新失败：{e}")
                continue
            old_stars = repo.stars or 0
            repo.stars = info["stars"]
            repo.forks = info["forks"]
            repo.pushed = info["pushed"]
            repo.license = info["license"]
            repo.archived = info["archived"]
            if info["description"]:
                repo.description = info["description"]
            self.db.update_favorite_repo(repo)
            watchlist.record_samples(self.db, [repo])
            ok += 1
            self.progress.emit(f"  [{i}/{len(favs)}] {repo.name} ⭐{info['stars']}")

            # 里程碑跨档：超过已提醒档位才发信号
            reminded = self.db.favorite_meta(repo.name).get("milestone", 0)
            crossed = [m for m in MILESTONES
                       if old_stars < m <= info["stars"] and m > reminded]
            if crossed:
                top = max(crossed)
                self.db.set_milestone(repo.name, top)
                self.milestone_hit.emit(repo.name, top, info["stars"])
            if i < len(favs) and not self.cancel.is_set():
                time.sleep(1.2)
        self.finished_all.emit(ok, fail)
