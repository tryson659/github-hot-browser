# -*- coding: utf-8 -*-
"""GH Archive 来源状态诊断（批次 5）。

输出小时账本的成功/空包/失败/未取与最近一次同步运行，不触发下载。
“已取得文件小时数 / 期望小时数”是采集覆盖，不等于事件完整率。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from app.core.db import Database
from app.services import gha_board
from app.sources import gharchive
from app.storage.store import Store


def report(db: Database, days: int = 7,
           now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    coverage = gha_board.archive_coverage(db, days, now=now)
    store = Store(db)
    latest = store.latest_source_run(gha_board.ARCHIVE_SOURCE)
    failed = store.failed_archive_hours(
        gha_board.ARCHIVE_SOURCE, *_window_keys(days, now))
    return {"days": days, "coverage": coverage, "last_run": latest,
            "failed_hours": failed[:50], "failed_total": len(failed)}


def _window_keys(days: int, now: datetime) -> tuple[str, str]:
    latest = gharchive.latest_done_hour()
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start.strftime("%Y-%m-%d-%H"), latest


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GH Archive 小时账本诊断")
    parser.add_argument("--days", type=int, default=7, choices=(7, 30))
    args = parser.parse_args(argv)

    db = Database()
    try:
        info = report(db, args.days)
        c = info["coverage"]
        print(f"GH Archive 近{args.days}天：期望 {c['expected']} 小时；"
              f"成功 {c['success']}，空包 {c['empty']}，失败 {c['failed']}，"
              f"未取 {c['missing']}")
        run = info["last_run"]
        if run:
            print(f"最近同步：{run['started_at']} → "
                  f"{run['finished_at'] or '进行中'}；状态 {run['status']}；"
                  f"成功包 {run['ok_count']}")
        else:
            print("最近同步：无记录")
        if info["failed_total"]:
            print(f"失败小时（前 50）：{', '.join(info['failed_hours'])}")
        return 0
    except Exception as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_main())
