# -*- coding: utf-8 -*-
"""用户数据导入导出（批次 4）。

两条边界：
- 追踪状态/备注/标签往返：版本化 JSON，默认 merge，不静默覆盖备注；
- 历史总量采样可选包含；不导出设置、Token、翻译凭证或任何密钥。

写入使用同目录临时文件 + 原子替换，失败不会留下半个文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from app.core.db import Database
from app.domain.identity import normalize_repo_name
from app.storage.store import Store

SCHEMA_VERSION = 1
DEFAULT_INTERVAL_HOURS = 6.0
MODES = ("merge", "skip", "replace")


class UserDataError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise UserDataError(f"{field} 必须是 ISO 时间字符串")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise UserDataError(f"{field} 时间非法：{value}") from e


def _bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise UserDataError(f"{field} 必须是布尔值")


def _interval(value) -> float:
    try:
        hours = float(value)
    except (TypeError, ValueError) as e:
        raise UserDataError("interval_hours 必须是数字") from e
    if not 1 <= hours <= 168:
        raise UserDataError("interval_hours 必须在 1~168 之间")
    return hours


def _tags(raw, field: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise UserDataError(f"{field} 必须是字符串数组")
    out = []
    for tag in raw:
        if not isinstance(tag, str) or not tag.strip():
            raise UserDataError(f"{field} 含非法标签")
        out.append(tag.strip())
    return out


def export_user_data(db: Database, *, include_history: bool = False,
                     store: Store | None = None,
                     now: datetime | None = None) -> dict:
    """导出追踪/收藏/备注/标签；不含设置、Token 或凭证。"""
    store = store or Store(db)
    now = now or _now()
    items: list[dict] = []
    seen: set[str] = set()

    for row in store.list_tracking(enabled_only=False, include_paused=True,
                                   include_removed=True):
        repo = store.repository(row["name"])
        if repo is None:
            continue
        tag_rows = store.tags(repo["id"])
        tags = {"user": [], "github_topic": []}
        for tag in tag_rows:
            tags.setdefault(tag["origin"], []).append(tag["tag"])
        items.append({
            "name": repo["full_name"],
            "url": repo.get("url") or "",
            "favorite": bool(row["favorite"]),
            "enabled": bool(row["enabled"]),
            "paused": bool(row["paused"]),
            "removed": bool(row.get("removed")),
            "note": row.get("note") or "",
            "interval_hours": float(row.get("sample_interval_hours")
                                    or DEFAULT_INTERVAL_HOURS),
            "next_due_at": row.get("next_due_at") or "",
            "last_sampled_at": row.get("last_sampled_at") or "",
            "tags": tags,
        })
        seen.add(repo["full_name"].lower())

    # 旧收藏未迁移到 tracking：保留收藏身份与备注，不自动开启周期采样
    for fav in db.list_favorites():
        key = fav.name.lower()
        if key in seen:
            continue
        meta = db.favorite_meta(fav.name)
        items.append({
            "name": fav.name,
            "url": fav.url,
            "favorite": True,
            "enabled": False,
            "paused": False,
            "removed": False,
            "note": meta.get("note") or "",
            "interval_hours": DEFAULT_INTERVAL_HOURS,
            "next_due_at": "",
            "last_sampled_at": "",
            "tags": {"user": list(meta.get("tags") or []), "github_topic": []},
        })
        seen.add(key)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "application": "github-hot-browser",
        "exported_at": _iso(now),
        "items": items,
    }
    if include_history:
        history = {}
        for item in items:
            repo = store.repository(item["name"])
            if repo is None:
                continue
            since = now - timedelta(days=365)
            samples = store.star_samples(repo["id"], since=since)
            if samples:
                history[item["name"]] = [[_iso(t), stars] for t, stars in samples]
        payload["history"] = {"samples": history}
    return payload


def _validate_item(raw, index: int) -> dict:
    if not isinstance(raw, dict):
        raise UserDataError(f"第 {index} 项不是对象")
    try:
        name = normalize_repo_name(raw.get("name", ""))
    except ValueError as e:
        raise UserDataError(f"第 {index} 项仓库名非法：{e}") from e
    tags = raw.get("tags") or {}
    if not isinstance(tags, dict):
        raise UserDataError(f"第 {index} 项 tags 必须是对象")
    note = raw.get("note") or ""
    if not isinstance(note, str) or len(note) > 1000:
        raise UserDataError(f"第 {index} 项备注非法或过长")
    return {
        "name": name,
        "url": raw.get("url") if isinstance(raw.get("url"), str) else "",
        "favorite": _bool(raw.get("favorite", False), "favorite"),
        "enabled": _bool(raw.get("enabled", True), "enabled"),
        "paused": _bool(raw.get("paused", False), "paused"),
        "removed": _bool(raw.get("removed", False), "removed"),
        "note": note.strip(),
        "interval_hours": _interval(raw.get("interval_hours",
                                            DEFAULT_INTERVAL_HOURS)),
        "next_due_at": _parse(raw.get("next_due_at"), "next_due_at"),
        "last_sampled_at": _parse(raw.get("last_sampled_at"),
                                  "last_sampled_at"),
        "tags_user": _tags(tags.get("user"), "tags.user"),
        "tags_topic": _tags(tags.get("github_topic"), "tags.github_topic"),
    }


def import_user_data(db: Database, payload: dict, *, mode: str = "merge",
                     store: Store | None = None) -> dict:
    """导入追踪状态；mode=merge/skip/replace，逐项报错不中断整批。"""
    if mode not in MODES:
        raise UserDataError(f"未知导入模式：{mode}")
    if not isinstance(payload, dict):
        raise UserDataError("导入内容必须是 JSON 对象")
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise UserDataError(f"不支持的用户数据版本：{version!r}")
    items = payload.get("items")
    if not isinstance(items, list):
        raise UserDataError("items 必须是数组")

    store = store or Store(db)
    stats = {"added": 0, "updated": 0, "skipped": 0, "unchanged": 0,
             "errors": 0, "conflicts": []}
    for index, raw in enumerate(items, 1):
        try:
            item = _validate_item(raw, index)
            repo = store.repository(item["name"])
            existing = store.tracking(repo["id"]) if repo else None
            if mode == "skip" and existing is not None:
                stats["skipped"] += 1
                continue

            repo_id = store.upsert_repository(item["name"], url=item["url"])
            if existing is None:
                store.ensure_tracking(
                    repo_id, favorite=item["favorite"],
                    enabled=item["enabled"], paused=item["paused"],
                    note=item["note"], interval_hours=item["interval_hours"],
                    next_due_at=item["next_due_at"])
                if item["last_sampled_at"]:
                    store.update_tracking(
                        repo_id, last_sampled_at=item["last_sampled_at"])
                if item["removed"]:
                    store.update_tracking(repo_id, removed=True, enabled=False,
                                          paused=True)
                store.set_tags(repo_id, item["tags_user"], "user")
                store.set_tags(repo_id, item["tags_topic"], "github_topic")
                stats["added"] += 1
                continue

            if mode == "replace":
                store.update_tracking(
                    repo_id, favorite=item["favorite"], enabled=item["enabled"],
                    paused=item["paused"], removed=item["removed"],
                    note=item["note"],
                    sample_interval_hours=item["interval_hours"],
                    next_due_at=item["next_due_at"] or existing.get("next_due_at", ""),
                    last_sampled_at=item["last_sampled_at"] or
                    existing.get("last_sampled_at", ""))
                store.set_tags(repo_id, item["tags_user"], "user")
                store.set_tags(repo_id, item["tags_topic"], "github_topic")
                stats["updated"] += 1
                continue

            # merge：不覆盖非空备注、不重新启用已暂停/停用项，标签取并集
            changes = {}
            if item["favorite"] and not existing["favorite"]:
                changes["favorite"] = True
            if item["note"] and not (existing.get("note") or ""):
                changes["note"] = item["note"]
            elif item["note"] and item["note"] != existing.get("note"):
                stats["conflicts"].append(
                    {"name": item["name"], "field": "note",
                     "kept": existing.get("note")})
            if changes:
                store.update_tracking(repo_id, **changes)
            current_user = [t["tag"] for t in store.tags(repo_id)
                            if t["origin"] == "user"]
            merged_user = list(dict.fromkeys(current_user + item["tags_user"]))
            if merged_user != current_user:
                store.set_tags(repo_id, merged_user, "user")
                changes["tags"] = True
            current_topic = [t["tag"] for t in store.tags(repo_id)
                             if t["origin"] == "github_topic"]
            merged_topic = list(dict.fromkeys(current_topic + item["tags_topic"]))
            if merged_topic != current_topic:
                store.set_tags(repo_id, merged_topic, "github_topic")
                changes["topics"] = True
            stats["updated" if changes else "unchanged"] += 1
        except (UserDataError, ValueError) as e:
            stats["errors"] += 1
            stats["conflicts"].append({"index": index, "error": str(e)})

    history = payload.get("history")
    if isinstance(history, dict):
        stats["samples_imported"] = _import_samples(store, history, stats)
    return stats


def _import_samples(store: Store, history: dict, stats: dict) -> int:
    samples = history.get("samples")
    if not isinstance(samples, dict):
        return 0
    imported = 0
    for name, rows in samples.items():
        repo = store.repository(name)
        if repo is None or not isinstance(rows, list):
            continue
        for row in rows:
            try:
                at, stars = row
                at = _parse(at, "history.samples")
                if at is None or not isinstance(stars, int) or stars < 0:
                    raise UserDataError("采样格式非法")
                store.save_star_sample(repo["id"], stars, at)
                imported += 1
            except (UserDataError, ValueError, TypeError):
                stats["errors"] += 1
    return imported


def write_json(path: str, payload: dict):
    """同目录临时文件 + 原子替换；失败不留下半个导出文件。"""
    tmp = f"{path}.tmp"
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用户追踪数据导入/导出")
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("path")
    ex.add_argument("--history", action="store_true")
    im = sub.add_parser("import")
    im.add_argument("path")
    im.add_argument("--mode", choices=MODES, default="merge")
    args = parser.parse_args(argv)

    db = Database()
    try:
        if args.command == "export":
            payload = export_user_data(db, include_history=args.history)
            write_json(args.path, payload)
            print(f"已导出 {len(payload['items'])} 项 → {args.path}")
            return 0
        payload = load_json(args.path)
        stats = import_user_data(db, payload, mode=args.mode)
        print(f"导入完成：{stats}")
        return 0 if stats["errors"] == 0 else 1
    except Exception as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_main())
