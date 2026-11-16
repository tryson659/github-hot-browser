# -*- coding: utf-8 -*-
"""旧版 JSON 数据 → SQLite 迁移（R06：可重复执行、错误可见、不静默覆盖）。

兼容旧 program/ 目录下的：settings.json、favorites.json、fav_meta.json、
trending_cache_v4.json、watchlist.json、gharchive_*.json、history/*.json。

合并策略：
- 收藏：按名称跳过已存在项；
- 设置：同一键只在"仍是上次迁移写入的值"时重新应用；用户改过的值不覆盖；
- 备注/标签：目标为空才写入（不覆盖迁移后的手工修改），跳过数量计入统计；
- 历史：数据库 (channel, days, taken_at) 唯一，重复执行不新增；
- 损坏 JSON、目录不存在计入 errors，CLI 返回非零退出码。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from app.core.db import Database
from app.core.models import Repo

MIGRATED_SETTINGS_KEY = "__migrated_settings__"

_SETTING_KEYS = ("languages", "custom_langs", "domains", "pool_size", "theme",
                 "hot_source", "interests")


def _new_stats() -> dict:
    return {"settings": 0, "settings_skipped": 0, "favorites": 0, "fav_meta": 0,
            "fav_meta_skipped": 0, "cache": 0, "history": 0, "watchlist": 0,
            "gharc_days": 0, "gharc_hours": 0, "errors": 0, "skipped": 0,
            "error_details": []}


def _load(path: str, default, stats: dict, log):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        stats["errors"] += 1
        stats["error_details"].append(f"{os.path.basename(path)}: {e}")
        log(f"  ⚠️ 无法读取 {path}：{e}（按空数据继续）")
        return default


def migrate(old_dir: str, db: Database, log=print) -> dict:
    stats = _new_stats()
    if not os.path.isdir(old_dir):
        stats["errors"] += 1
        stats["error_details"].append(f"目录不存在：{old_dir}")
        log(f"目录不存在：{old_dir}")
        return stats
    src = os.path.abspath(old_dir)

    # ---- settings：只覆盖"仍是上次迁移写入值"的键 ----
    s = _load(os.path.join(old_dir, "settings.json"), {}, stats, log)
    applied = db.get_setting(MIGRATED_SETTINGS_KEY, {}) or {}
    if not isinstance(applied, dict):
        applied = {}
    if isinstance(s, dict):
        for k in _SETTING_KEYS:
            if k not in s:
                continue
            current = db.get_setting(k)
            if k in applied and current != applied[k]:
                stats["settings_skipped"] += 1
                log(f"  保留现有设置 {k}（迁移后已修改）")
                continue
            db.set_setting(k, s[k])
            applied[k] = s[k]
            stats["settings"] += 1
        db.set_setting(MIGRATED_SETTINGS_KEY, applied)

    # ---- favorites + fav_meta ----
    favs = _load(os.path.join(old_dir, "favorites.json"), {}, stats, log)
    metas = _load(os.path.join(old_dir, "fav_meta.json"), {}, stats, log)
    if isinstance(favs, dict):
        for name, data in favs.items():
            if db.is_favorite(name):
                stats["skipped"] += 1
                continue
            try:
                repo = Repo.from_dict(data if isinstance(data, dict) else {"name": name})
                db.add_favorite(repo)
                if isinstance(data, dict) and data.get("saved_stars") is not None:
                    db.set_favorite_baseline(
                        name, data.get("saved_stars") or 0,
                        data.get("saved_at") or "")
                stats["favorites"] += 1
            except Exception as e:
                stats["skipped"] += 1
                log(f"  跳过收藏 {name}：{e}")
    if isinstance(metas, dict):
        for name, m in metas.items():
            if not isinstance(m, dict):
                continue
            try:
                current = db.get_fav_meta(name)
                if current.get("note") or current.get("tags"):
                    stats["fav_meta_skipped"] += 1
                    log(f"  保留现有备注/标签 {name}（迁移后已修改）")
                else:
                    db.set_favorite_note(name, m.get("note") or "",
                                         m.get("tags") or [])
                    stats["fav_meta"] += 1
                if m.get("milestone") and not current.get("milestone"):
                    db.set_milestone(name, int(m["milestone"]))
            except Exception as e:
                stats["skipped"] += 1
                log(f"  跳过备注 {name}：{e}")

    # ---- trending_cache_v4.json：{days: {channel: {repos, time, period}}} ----
    cache = _load(os.path.join(old_dir, "trending_cache_v4.json"), {}, stats, log)
    if isinstance(cache, dict):
        for days_s, channels in cache.items():
            try:
                days = int(days_s)
            except (TypeError, ValueError):
                continue
            if not isinstance(channels, dict):
                continue
            for ch, entry in channels.items():
                if not isinstance(entry, dict):
                    continue
                repos_raw = entry.get("repos") or []
                try:
                    repos = [Repo.from_dict(d) for d in repos_raw
                             if isinstance(d, dict)]
                except Exception:
                    stats["skipped"] += 1
                    continue
                if not repos:
                    continue
                existing, _, _ = db.load_cache(ch, days)
                if existing:
                    stats["skipped"] += 1
                    continue
                db.save_cache(ch, days, repos, entry.get("period") or "")
                stats["cache"] += 1

    # ---- history/*.json：文件名 "<days>d_<channel>_<YYYYMMDD>_<HHMMSS>.json" ----
    hdir = os.path.join(old_dir, "history")
    if os.path.isdir(hdir):
        for fn in sorted(os.listdir(hdir)):
            if not fn.endswith(".json"):
                continue
            stem = fn[:-5]
            try:
                days = int(stem.split("d_", 1)[0])
                taken = datetime.strptime(stem[-15:], "%Y%m%d_%H%M%S").strftime(
                    "%Y-%m-%d %H:%M:%S")
                channel = stem[len(str(days)) + 2:-16]
            except Exception:
                stats["skipped"] += 1
                continue
            data = _load(os.path.join(hdir, fn), {}, stats, log)
            repos_raw = (data.get("repos") or []) if isinstance(data, dict) else []
            try:
                repos = [Repo.from_dict(d) for d in repos_raw if isinstance(d, dict)]
            except Exception:
                stats["skipped"] += 1
                continue
            if not repos:
                continue
            if db.save_history(channel, days, repos, taken, overwrite=False):
                stats["history"] += 1
            else:
                stats["skipped"] += 1

    # ---- watchlist.json：{name: {url, desc, lang, manual, samples: {date: stars}}} ----
    watch = _load(os.path.join(old_dir, "watchlist.json"), {}, stats, log)
    if isinstance(watch, dict):
        for name, w in watch.items():
            if not isinstance(w, dict):
                continue
            try:
                db.watch_add(name, url=w.get("url") or "",
                             desc=w.get("desc") or "",
                             lang=w.get("lang") or "-",
                             manual=bool(w.get("manual")))
                for date, stars in (w.get("samples") or {}).items():
                    db.watch_sample(name, int(stars), date)
                stats["watchlist"] += 1
            except Exception as e:
                stats["skipped"] += 1
                log(f"  跳过观察 {name}：{e}")

    # ---- gharchive_days.json：{day: {"v": 2, "counts": {...}}} ----
    gdays = _load(os.path.join(old_dir, "gharchive_days.json"), {}, stats, log)
    if isinstance(gdays, dict):
        for day, entry in gdays.items():
            if (isinstance(entry, dict) and entry.get("v") == 2
                    and db.gha_get_day(day) is None):
                db.gha_save_day(day, entry.get("counts") or {})
                stats["gharc_days"] += 1

    # ---- gharchive_hours.json：{"YYYY-MM-DD-HH": {...}, "__cursor__": "..."} ----
    ghours = _load(os.path.join(old_dir, "gharchive_hours.json"), {}, stats, log)
    if isinstance(ghours, dict):
        for key, val in ghours.items():
            if key == "__cursor__":
                if isinstance(val, str) and not db.gha_get_cursor():
                    db.gha_set_cursor(val)
                continue
            if isinstance(val, dict) and db.gha_get_hour(key) is None:
                db.gha_save_hour(key, val)
                stats["gharc_hours"] += 1

    # 迁移批次记录：便于事后核对"哪一次、从哪个目录、导入了多少"
    db.set_setting("__migrate_last__", {
        "source": src,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": {k: v for k, v in stats.items() if k != "error_details"},
    })

    if stats["errors"]:
        log(f"迁移结束（{stats['errors']} 个错误，详见上方）：{stats}")
    else:
        log(f"迁移完成：{stats}")
    return stats


def run_cli(target: str) -> int:
    """命令行迁移入口：单实例锁 + 启动备份 + 明确退出码。

    返回：0 成功；1 迁移有错误；2 参数错误；3 应用正在运行；4 备份失败。
    """
    from app import runtime

    runtime.install_crash_hook()
    lock = runtime.acquire_lock()
    if lock is None:
        print("检测到程序正在运行：请先退出应用再迁移，避免并发写库。")
        return 3
    db = Database()
    try:
        try:
            backup = runtime.backup_database(db)
            print(f"迁移前备份：{backup}")
        except Exception as e:
            print(f"备份失败，已中止迁移（上一份有效备份保留）：{e}")
            return 4
        stats = migrate(target, db)
        return 0 if stats["errors"] == 0 else 1
    finally:
        db.close()
        lock.unlock()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if not target:
        print('用法：python -m app.services.migrate "旧program目录"')
        sys.exit(2)
    sys.exit(run_cli(target))
