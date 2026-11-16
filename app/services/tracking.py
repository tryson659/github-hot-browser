# -*- coding: utf-8 -*-
"""个人追踪服务（批次 4）。

追踪与榜单刷新解耦：收藏/导入的仓库即使跌出榜单，也按自己的 next_due 采样。
应用关闭期间不采样；重启后按持久化的 next_due 继续，不补造历史时刻。
暂停/删除后自动发现只允许"确保存在"，不得悄悄重新启用。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.db import Database
from app.services.observations import record_sample as record_star_sample
from app.storage.store import Store

DEFAULT_INTERVAL_HOURS = 6.0


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def next_due(last_sampled_at: datetime | None, interval_hours: float,
             now: datetime | None = None, *, enabled: bool = True,
             paused: bool = False) -> datetime | None:
    """纯调度规则：暂停/停用不排期；无历史采样则从当前时刻起算。"""
    if not enabled or paused:
        return None
    base = last_sampled_at or _now(now)
    return base + timedelta(hours=max(1.0, float(interval_hours)))


def ensure_tracked(db: Database, repo_name: str, *,
                   favorite: bool = False, interval_hours: float = DEFAULT_INTERVAL_HOURS,
                   note: str = "", store: Store | None = None) -> int:
    """自动发现/导入用：只创建缺失的追踪行，不修改已有暂停/启用/备注。"""
    store = store or Store(db)
    repo_id = store.upsert_repository(repo_name)
    store.ensure_tracking(
        repo_id, favorite=favorite, enabled=True, paused=False, note=note,
        interval_hours=interval_hours)
    return repo_id


def add_tracking(db: Database, repo_name: str, *, favorite: bool = True,
                 note: str = "", tags: list[str] | None = None,
                 interval_hours: float = DEFAULT_INTERVAL_HOURS,
                 store: Store | None = None) -> int:
    """用户显式添加/恢复追踪：允许启用已暂停项；备注仅在提供时覆盖。"""
    store = store or Store(db)
    repo_id = ensure_tracked(db, repo_name, favorite=favorite,
                             interval_hours=interval_hours, store=store)
    store.update_tracking(repo_id, enabled=True, paused=False, removed=False,
                          favorite=favorite)
    if note:
        store.set_note(repo_id, note)
    if tags is not None:
        store.set_tags(repo_id, tags)
    return repo_id


def set_paused(db: Database, repo_name: str, paused: bool,
               store: Store | None = None) -> bool:
    store = store or Store(db)
    repo = store.repository(repo_name)
    row = store.tracking(repo["id"]) if repo else None
    if row is None or row.get("removed"):
        return False
    store.set_paused(repo["id"], paused)
    if not paused:
        # 恢复时重新排期，避免暂停期间堆积的立即到期
        store.update_tracking(repo["id"], next_due_at=_now())
    return True


def remove_tracking(db: Database, repo_name: str,
                    store: Store | None = None) -> bool:
    """停止追踪并删除追踪状态；仓库身份/观测/标签墓碑保留。"""
    store = store or Store(db)
    repo = store.repository(repo_name)
    if repo is None or store.tracking(repo["id"]) is None:
        return False
    store.delete_tracking(repo["id"])
    return True


def set_note(db: Database, repo_name: str, note: str,
             store: Store | None = None) -> bool:
    store = store or Store(db)
    repo = store.repository(repo_name)
    if repo is None or store.tracking(repo["id"]) is None:
        return False
    store.set_note(repo["id"], note)
    return True


def set_tags(db: Database, repo_name: str, tags: list[str],
             store: Store | None = None) -> bool:
    store = store or Store(db)
    repo = store.repository(repo_name)
    if repo is None:
        return False
    store.set_tags(repo["id"], tags, origin="user")
    return True


def tags(db: Database, repo_name: str,
         store: Store | None = None) -> dict[str, list[str]]:
    store = store or Store(db)
    repo = store.repository(repo_name)
    if repo is None:
        return {}
    out: dict[str, list[str]] = {"user": [], "github_topic": []}
    for row in store.tags(repo["id"]):
        out.setdefault(row["origin"], []).append(row["tag"])
    return out


def due_tracking(db: Database, now: datetime | None = None,
                 store: Store | None = None) -> list[dict]:
    """返回启用、未暂停且到期的追踪项，按到期时间排序。"""
    store = store or Store(db)
    now = _now(now)
    due: list[dict] = []
    for row in store.list_tracking(enabled_only=True, include_paused=False):
        interval = float(row.get("sample_interval_hours") or DEFAULT_INTERVAL_HOURS)
        last = _parse(row.get("last_sampled_at"))
        upcoming = _parse(row.get("next_due_at")) or next_due(last, interval, now)
        if upcoming is None or upcoming > now:
            continue
        item = dict(row)
        item["due_at"] = upcoming
        due.append(item)
    due.sort(key=lambda r: (r["due_at"], r["repo_id"]))
    return due


def record_sample(db: Database, repo_name: str, stars: int, *,
                  sampled_at: datetime | None = None,
                  now: datetime | None = None,
                  store: Store | None = None) -> bool:
    """成功采样后更新 last_sampled_at 与 next_due；失败请求不要调用。"""
    store = store or Store(db)
    repo = store.repository(repo_name)
    if repo is None:
        return False
    tracking = store.tracking(repo["id"])
    if tracking is None or tracking.get("removed"):
        return False
    sampled = sampled_at or _now(now)
    record_star_sample(db, repo_name, stars, sampled_at=sampled, store=store)
    interval = float(tracking.get("sample_interval_hours")
                     or DEFAULT_INTERVAL_HOURS)
    store.update_tracking(
        repo["id"], last_sampled_at=sampled,
        next_due_at=sampled + timedelta(hours=max(1.0, interval)))
    return True


def defer(db: Database, repo_name: str, *,
          until: datetime | None = None, minutes: int = 30,
          store: Store | None = None) -> bool:
    """采样失败时推迟下一次尝试，不写入伪造的 stars=0 采样。"""
    store = store or Store(db)
    repo = store.repository(repo_name)
    row = store.tracking(repo["id"]) if repo else None
    if row is None or row.get("removed"):
        return False
    store.update_tracking(
        repo["id"], next_due_at=until or (_now() + timedelta(minutes=minutes)))
    return True
