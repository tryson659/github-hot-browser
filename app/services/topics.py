# -*- coding: utf-8 -*-
"""topics 标签补齐：经 ecosyste.ms（5000 次/时）批量查询并缓存 7 天。"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

from app.core.db import Database


def eco_topics(name: str) -> list[str]:
    url = ("https://repos.ecosyste.ms/api/v1/hosts/GitHub/repositories/"
           + urllib.parse.quote(name, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": "github-hot-browser/2.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    return d.get("topics") or []


def enrich(db: Database, names: list[str], log=print,
           cancel: threading.Event | None = None,
           cap: int = 30) -> int:
    """为 names 中无有效缓存的项目补齐 topics。返回补齐数量。"""
    missing = db.topics_missing(names)[:cap]
    done = 0
    for name in missing:
        if cancel is not None and cancel.is_set():
            break
        try:
            db.save_topics(name, eco_topics(name))
            done += 1
        except Exception as e:
            log(f"  ⚠️ topics {name}：{e}")
        time.sleep(0.3)
    return done


def candidates(pool, top: int = 50, db=None) -> list[str]:
    """当前池的 topics 频率前 N（可合并 DB 缓存；过滤超长标签防撑宽）。"""
    from collections import Counter
    cnt: Counter = Counter()
    for r in pool:
        topics = list(r.topics or [])
        if not topics and db is not None:
            topics = db.get_topics(r.name) or []
        for t in topics:
            if len(t) <= 26:
                cnt[t] += 1
    return [t for t, _ in cnt.most_common(top)]
