# -*- coding: utf-8 -*-
"""数据源：GH Archive 小时包下载与解析（只做网络与流式解析）。

下载 data.gharchive.org 小时包（约 5MB/小时，gzip NDJSON），逐条产出
WatchEvent 的 repo 名。聚合、窗口统计与建榜在 `app.services.gha_board`。

注意：CDN 拦截 Python-urllib 默认 UA，必须带 User-Agent；
2025 年中起 Star 事件严重漏报，绝对增量偏低、相对排名仍可用。
"""
from __future__ import annotations

import gzip
import json
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

from app.core.config import USER_AGENT

BASE = "https://data.gharchive.org"


def iter_watch_events_hour(day: str, hour: int,
                           cancel: threading.Event | None = None):
    """下载单个小时包，逐条产出 WatchEvent 的 repo 名。"""
    if cancel is not None and cancel.is_set():
        return
    req = urllib.request.Request(
        f"{BASE}/{day}-{hour}.json.gz", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        with gzip.GzipFile(fileobj=resp) as gz:
            for line in gz:
                if cancel is not None and cancel.is_set():
                    return
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "WatchEvent":
                    yield ev["repo"]["name"]


def download_hour(day: str, hour: int,
                  cancel: threading.Event | None = None) -> dict[str, int]:
    """下载并计数一个小时包，返回 {repo: n}。"""
    counts: dict[str, int] = {}
    for repo in iter_watch_events_hour(day, hour, cancel):
        counts[repo] = counts.get(repo, 0) + 1
    return counts


def latest_done_hour() -> str:
    """最近一个已结束小时（UTC）。"""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d-%H")


def iter_hours(start_key: str, end_key: str):
    """生成 [start_key, end_key] 逐小时 key（跨天连续）。"""
    d = datetime.strptime(start_key, "%Y-%m-%d-%H")
    e = datetime.strptime(end_key, "%Y-%m-%d-%H")
    while d <= e:
        yield d.strftime("%Y-%m-%d-%H")
        d += timedelta(hours=1)
