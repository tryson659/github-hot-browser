# -*- coding: utf-8 -*-
"""观测服务：把官方日桶/总量采样写入存储，并按窗口计算。

只负责"获取结果 → 标准化观测 → 窗口结果"，不操作 UI。官方历史适配器
未通过真实接口验证前，结果只作为只读参考，不能冒充净增长。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from app.core.db import Database
from app.domain.metrics import Granularity, Metric, rolling_window
from app.domain.windows import WindowResult, history_growth, net_growth
from app.sources.github_history import StarHistory, fetch_history
from app.storage.store import Store

HISTORY_SOURCE = "github_history"
HISTORY_METRIC = Metric.GITHUB_HISTORY_STARS.value


def record_history(db: Database, repo_name: str, history: StarHistory,
                   source: str = HISTORY_SOURCE,
                   store: Store | None = None) -> int:
    """写入官方日桶观测（同一天修订覆盖，不重复累计）。返回写入桶数。"""
    store = store or Store(db)
    repo_id = store.upsert_repository(repo_name)
    observed = history.fetched_at
    rows = []
    for bucket in history.days:
        day = bucket.day.isoformat()
        rows.append({
            "repo_id": repo_id, "source": source, "metric": HISTORY_METRIC,
            "observation_key": f"{source}:{repo_id}:{day}",
            "value": bucket.count, "observed_at": observed,
            "window_start": day, "window_end": day, "granularity": "daily",
            "completeness": "valid",
            "provenance": f"github stargazers/history bucket {day}",
            "parser_version": history.parser_version,
        })
    store.save_observations(rows)
    if history.days:
        store.add_candidate(repo_id, source, observed)
    return len(history.days)


def sync_history(db: Database, repo_name: str, *,
                 fetch=None, store: Store | None = None) -> dict:
    """拉取并写入单仓历史；记录 source_run，失败不留下成功状态。"""
    store = store or Store(db)
    fetch = fetch or fetch_history
    run_id = store.start_source_run(HISTORY_SOURCE)
    try:
        history = fetch(repo_name)
        count = record_history(db, repo_name, history, store=store)
    except Exception as e:
        store.finish_source_run(run_id, "failed", fail_count=1, error=str(e))
        raise
    store.finish_source_run(run_id, "success", ok_count=count)
    return {"repo": repo_name, "days": count, "run_id": run_id}


def history_window(db: Database, repo_name: str, days: int,
                   now: datetime | None = None,
                   store: Store | None = None) -> WindowResult | None:
    """按滚动窗口汇总官方日桶；仓库不存在返回 None，缺桶由结果标记 PARTIAL。"""
    store = store or Store(db)
    repo = store.repository(repo_name)
    if repo is None:
        return None
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days + 1)).date()
    buckets = store.history_buckets(repo["id"], since=since)
    spec = rolling_window(now, days, metric=Metric.GITHUB_HISTORY_STARS,
                          granularity=Granularity.DAILY)
    return history_growth(buckets, spec)


def record_sample(db: Database, repo_name: str, stars: int, *,
                  sampled_at: datetime | None = None, run_id: str = "",
                  store: Store | None = None) -> int:
    """记录一次成功总量采样（失败请求不应调用本函数）。"""
    store = store or Store(db)
    repo_id = store.upsert_repository(repo_name)
    store.save_star_sample(repo_id, stars, sampled_at, run_id)
    return repo_id


def net_window(db: Database, repo_name: str, days: int,
               now: datetime | None = None,
               tolerance: timedelta = timedelta(hours=24),
               store: Store | None = None) -> WindowResult | None:
    """总量采样净增长；缺起点/终点返回 MISSING，不用 0 补齐。"""
    store = store or Store(db)
    repo = store.repository(repo_name)
    if repo is None:
        return None
    now = now or datetime.now(timezone.utc)
    samples = store.star_samples(repo["id"])
    spec = rolling_window(now, days, metric=Metric.NET_STARS,
                          granularity=Granularity.SAMPLE)
    return net_growth(samples, spec, tolerance=tolerance)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="拉取单仓官方 Star 历史并打印窗口结果（只读、需联网）")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--days", type=int, default=7, choices=(7, 30))
    args = parser.parse_args(argv)

    db = Database()
    try:
        info = sync_history(db, args.repo)
        result = history_window(db, args.repo, args.days)
        print(f"仓库：{args.repo}；写入日桶：{info['days']}；run={info['run_id']}")
        if result is None:
            print("未找到仓库记录")
            return 1
        print(f"窗口：{result.requested_start.date()} ~ "
              f"{result.requested_end.date()}（请求 {args.days} 天）")
        print(f"指标：{result.metric.value}；值：{result.value}；"
              f"完整性：{result.completeness.value}；质量：{result.quality_ok}")
        if result.actual_start and result.actual_end:
            print(f"实际桶范围：{result.actual_start.date()} ~ "
                  f"{result.actual_end.date()}")
        if result.reason:
            print(f"说明：{result.reason}")
        return 0
    except Exception as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_main())
