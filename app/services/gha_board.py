# -*- coding: utf-8 -*-
"""GH Archive 聚合与建榜（服务层，R10/R11）。

- 下载/解析小时包留在 `app.sources.gharchive`（纯数据源）；
- 小时合并、窗口统计、覆盖率与榜单构建在这里完成，只通过 Database 的
  公开方法读写，不再由数据源直接操作数据库。

R10：窗口内有数据 ≠ 覆盖整个窗口。建榜时统计有效覆盖小时数，
按覆盖时长折算日均；覆盖不足时明确标注，且不把部分窗口冒充完整周/月榜。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from app.core.classify import classify_domain, match_interest
from app.core.db import Database
from app.core.models import Repo
from app.sources import gharchive
from app.storage.store import Store

MIN_DAILY_HOURS = 24          # 不足一天样本不计算日均（避免 ×24 外推）
FULL_COVERAGE_RATIO = 0.99    # 视为完整窗口的覆盖阈值
BACKFILL_MAX_HOURS = 720      # 首次回填上限（30 天）
ARCHIVE_SOURCE = "gh_archive"
RETRY_HOURS_PER_ROUND = 2     # 每轮优先重试的失败小时数


def _count_digest(counts: dict[str, int]) -> str:
    payload = json.dumps(counts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def merge_days(db: Database) -> int:
    """凑满 24 小时的天合并进天级表并释放小时缓存。返回新合并天数。"""
    by_day = db.gha_hours_by_day()
    have_days = set(db.gha_days())
    merged = 0
    for day, hours in by_day.items():
        if day in have_days:
            db.gha_delete_hours([f"{day}-{h:02d}" for h in hours])
            continue
        if len(hours) < 24:
            continue
        counts: dict[str, int] = {}
        for c in hours.values():
            for repo, n in c.items():
                counts[repo] = counts.get(repo, 0) + n
        db.gha_save_day(day, counts)
        db.gha_delete_hours([f"{day}-{h:02d}" for h in hours])
        merged += 1
    return merged


def catch_up(db: Database, max_hours: int = 6, log=print,
             cancel=None) -> tuple[int, bool]:
    """自适应增量：从游标后一小时追到最近已结束小时，每轮最多 max_hours 个包。
    单包失败不推进游标（下轮自动重试）。返回 (新增小时数, 是否追平)。"""
    latest = gharchive.latest_done_hour()
    cursor = db.gha_get_cursor()
    store = Store(db)
    latest_dt = datetime.strptime(latest, "%Y-%m-%d-%H")
    retry_window_start = (latest_dt - timedelta(days=30)).strftime(
        "%Y-%m-%d-%H")
    failed = store.failed_archive_hours(
        ARCHIVE_SOURCE, retry_window_start, latest)
    if cursor and cursor >= latest and not failed:
        return 0, True
    early = bool(cursor and cursor >= latest)
    if early:
        start = failed[0]           # 游标已追平但有失败缺口：从最早缺口重试
    elif cursor:
        start = (datetime.strptime(cursor, "%Y-%m-%d-%H") + timedelta(hours=1)
                 ).strftime("%Y-%m-%d-%H")
    else:
        # 无游标：默认只取最近小时；用户配置的回填小时数决定是否补历史
        backfill = int(db.get_setting("gha_backfill_hours", 0) or 0)
        backfill = max(0, min(backfill, BACKFILL_MAX_HOURS))
        if backfill:
            start = (latest_dt - timedelta(hours=backfill)).strftime(
                "%Y-%m-%d-%H")
        else:
            start = latest
    done = 0
    retried: set[str] = set()

    def _advance(key: str):
        cursor_now = db.gha_get_cursor()
        if not cursor_now or key > cursor_now:
            db.gha_set_cursor(key)

    def already_done(key: str, day: str) -> bool:
        row = store.archive_hour(ARCHIVE_SOURCE, key)
        if row and row["status"] in ("success", "empty"):
            if db.gha_get_hour(key) is not None or db.gha_get_day(day) is not None:
                return True
            # 账本成功但缓存被清理：重新下载，避免把缺失当成功
        if db.gha_get_hour(key) is not None or db.gha_get_day(day) is not None:
            counts = db.gha_get_hour(key) or {}
            store.mark_archive_hour(
                ARCHIVE_SOURCE, key, "success",
                event_count=sum(counts.values()),
                checksum=_count_digest(counts))
            return True
        return False

    def fetch(key: str) -> str:
        """返回 success / 404 / failed。"""
        nonlocal done
        day = key[:10]
        if already_done(key, day):
            _advance(key)
            return "success"
        try:
            counts = gharchive.download_hour(day, int(key[11:]), cancel)
        except Exception as e:
            if "404" in str(e):
                log(f"  🗄 小时包 {key} 尚未发布，稍后自动再看")
                return "404"
            store.mark_archive_hour(ARCHIVE_SOURCE, key, "failed",
                                    error=str(e))
            log(f"  ⚠️ 小时包 {key} 获取失败（{e}），稍后自动重试")
            return "failed"
        if cancel is not None and cancel.is_set():
            return "failed"
        n = sum(counts.values())
        db.gha_save_hour(key, counts)
        store.mark_archive_hour(
            ARCHIVE_SOURCE, key, "empty" if n == 0 else "success",
            event_count=n, checksum=_count_digest(counts))
        _advance(key)
        done += 1
        log(f"  🗄 GH Archive +1小时 {key}（{n} 个 Star 事件）")
        return "success"

    # 先重试窗口内的失败小时，避免游标越过缺口后永久忽略
    for key in store.failed_archive_hours(
            ARCHIVE_SOURCE, start, latest)[:RETRY_HOURS_PER_ROUND]:
        if done >= max_hours or (cancel is not None and cancel.is_set()):
            break
        retried.add(key)
        fetch(key)          # 404/再次失败保留 failed，状态可见

    for key in gharchive.iter_hours(start, latest):
        if early:
            break                   # 已追平，只做失败缺口重试
        if done >= max_hours or (cancel is not None and cancel.is_set()):
            break
        if key in retried:
            continue
        result = fetch(key)
        if result == "404":
            return done, True
        if result == "failed" and cancel is not None and cancel.is_set():
            break

    merged = merge_days(db)
    failed_left = store.failed_archive_hours(ARCHIVE_SOURCE, start, latest)
    caught = db.gha_get_cursor() >= latest and not failed_left
    return done, caught


def backfill(db: Database, days: int = 7, log=print,
             cancel=None) -> dict:
    """从最近已结束小时向前补齐 N 天小时包（用于首次建榜/覆盖提示）。

    与 `catch_up` 不同：不依赖游标、不会把游标退到过去，下载失败只记
    failed，保留已成功小时。返回统计；取消时保留已完成部分。
    """
    days = max(1, min(int(days), 30))
    latest = gharchive.latest_done_hour()
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    keys = list(gharchive.iter_hours(
        start.strftime("%Y-%m-%d-%H"), latest))
    keys.reverse()          # 先补最近小时，取消时也留下可用数据
    need = len(keys)
    store = Store(db)
    done = skipped = failed = 0
    log(f"🗄 GH Archive 回填近 {days} 天（{need} 小时，已下载会跳过）")
    for key in keys:
        if cancel is not None and cancel.is_set():
            log("🗄 回填已取消，保留已完成部分")
            break
        day = key[:10]
        row = store.archive_hour(ARCHIVE_SOURCE, key)
        if (row and row["status"] in ("success", "empty")
                and (db.gha_get_hour(key) is not None
                     or db.gha_get_day(day) is not None)):
            skipped += 1
            continue
        try:
            counts = gharchive.download_hour(day, int(key[11:]), cancel)
        except Exception as e:
            if "404" in str(e):
                log(f"  ⚠️ 小时 {key} 尚未发布，跳过")
            else:
                store.mark_archive_hour(ARCHIVE_SOURCE, key, "failed",
                                        error=str(e))
                failed += 1
                log(f"  ⚠️ 小时 {key} 失败（{e}）")
            continue
        if cancel is not None and cancel.is_set():
            log("🗄 回填已取消，保留已完成部分")
            break
        n = sum(counts.values())
        db.gha_save_hour(key, counts)
        store.mark_archive_hour(
            ARCHIVE_SOURCE, key, "empty" if n == 0 else "success",
            event_count=n, checksum=_count_digest(counts))
        done += 1
        if done % 5 == 0 or done == 1:
            log(f"  🗄 回填 {done}/{need}，当前 {key}（{n} 个 Star 事件）")
    merged = merge_days(db)
    stats = {"days": days, "expected": need, "downloaded": done,
             "skipped": skipped, "failed": failed, "merged": merged,
             "cancelled": bool(cancel is not None and cancel.is_set())}
    log(f"🗄 回填结束：新增 {done}，跳过 {skipped}，失败 {failed}，"
        f"合并 {merged} 天")
    return stats


def window_counts(db: Database, days: int) -> dict[str, int]:
    """近 N 天窗口的 WatchEvent 总计数（天级 + 当日小时级拼合）。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)
             ).strftime("%Y-%m-%d")
    total: dict[str, int] = {}
    for d in db.gha_days():
        if start <= d <= today:
            for repo, n in (db.gha_get_day(d) or {}).items():
                total[repo] = total.get(repo, 0) + n
    for day, hours in db.gha_hours_by_day().items():
        if start <= day <= today:
            for c in hours.values():
                for repo, n in c.items():
                    total[repo] = total.get(repo, 0) + n
    return total


def window_coverage(db: Database, days: int,
                    now: datetime | None = None) -> tuple[int, int]:
    """返回窗口内 (已覆盖小时数, 应有小时数)。

    窗口从 (今天 - days + 1) 的 00:00 UTC 起，到最近一个已结束小时。
    天级聚合按 24 小时计；未合并的小时级按实际条目计（两者互斥）。
    """
    now = now or datetime.now(timezone.utc)
    latest = (datetime.strptime(gharchive.latest_done_hour(), "%Y-%m-%d-%H")
              .replace(tzinfo=timezone.utc))
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if latest < start:
        return 0, 0
    expected = int((latest - start).total_seconds() // 3600) + 1
    start_day, latest_day = start.date(), latest.date()
    covered = 0
    for day_s in db.gha_days():
        try:
            d = date.fromisoformat(day_s)
        except ValueError:
            continue
        if start_day <= d <= latest_day:
            covered += 24
    for day_s, hours in db.gha_hours_by_day().items():
        try:
            d = date.fromisoformat(day_s)
        except ValueError:
            continue
        if not (start_day <= d <= latest_day):
            continue
        for h in hours:
            if d == latest_day and h > latest.hour:
                continue
            covered += 1
    return min(covered, expected), expected


def archive_coverage(db: Database, days: int, *,
                     now: datetime | None = None) -> dict:
    """小时账本覆盖：成功/空包/失败/未取，用于诊断而非事件完整率。"""
    now = now or datetime.now(timezone.utc)
    latest = (datetime.strptime(gharchive.latest_done_hour(), "%Y-%m-%d-%H")
              .replace(tzinfo=timezone.utc))
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if latest < start:
        return {"expected": 0, "success": 0, "empty": 0, "failed": 0,
                "tracked": 0, "missing": 0}
    expected = int((latest - start).total_seconds() // 3600) + 1
    start_key = start.strftime("%Y-%m-%d-%H")
    end_key = latest.strftime("%Y-%m-%d-%H")
    counts = Store(db).archive_status_counts(ARCHIVE_SOURCE, start_key, end_key)
    success = counts.get("success", 0)
    empty = counts.get("empty", 0)
    failed = counts.get("failed", 0)
    tracked = success + empty + failed
    return {"expected": expected, "success": success, "empty": empty,
            "failed": failed, "tracked": tracked,
            "missing": max(0, expected - tracked)}


def build_board(db: Database, days: int, top: int = 50,
                interests: dict | None = None,
                now: datetime | None = None) -> tuple[list[Repo], str]:
    """从本地聚合数据生成近 N 天增量 Top 榜（stars 不可得，显示 ?）。"""
    counts = window_counts(db, days)
    # 过滤窗口总量 < 2 的噪声（单日 1 次 × N 天的项目不进榜）
    rows = sorted(((n, r) for r, n in counts.items() if n >= 2), reverse=True)
    if not rows:
        raise RuntimeError("GH Archive 本地数据不足（请先开启后台同步积累数据）")

    covered, expected = window_coverage(db, days, now=now)
    ratio = (covered / expected) if expected else 0.0
    daily_ok = covered >= MIN_DAILY_HOURS
    effective_days = covered / 24 if covered else 0.0
    label = f"近{days}天"
    coverage_text = f"{covered}/{expected} 小时（{ratio:.0%}）"
    ledger = archive_coverage(db, days, now=now)
    if ledger["tracked"] and (ledger["failed"] or ledger["missing"]):
        coverage_text += f"，失败{ledger['failed']}，未取{ledger['missing']}"
    today = (now or datetime.now(timezone.utc)).date()
    window_start = (today - timedelta(days=days - 1)).isoformat()
    window_end = today.isoformat()
    completeness = "valid" if daily_ok and ratio >= FULL_COVERAGE_RATIO else "partial"

    repos: list[Repo] = []
    for inc, name in rows[:top]:
        tags, hits = match_interest(name, "", interests)
        repos.append(Repo(
            name=name, url=f"https://github.com/{name}",
            stars=None, forks=None,
            description=(f"GH Archive 统计：{label}内 {inc} 个 Star 事件"
                         "（该榜无简介/星数，点「🔗 打开」看项目主页）"),
            pushed="-",
            domain=classify_domain(name, ""),
            growth_daily=(round(inc / effective_days, 1)
                          if daily_ok and effective_days else 0.0),
            growth_7d=(inc if days == 7 and daily_ok
                       and ratio >= FULL_COVERAGE_RATIO else None),
            growth_value=inc,
            growth_window=label if daily_ok else f"{label}（样本不足）",
            coverage=coverage_text,
            metric="observed_star_events",
            window_days=days,
            window_start=window_start, window_end=window_end,
            completeness=completeness,
            interest_tags=tags, interest_hits=hits,
            source="GH Archive 自建",
        ))

    period = f"GH Archive {label}窗口 · 覆盖 {coverage_text}"
    if not daily_ok:
        period += "（样本不足，暂不计算日均）"
    return repos, period
