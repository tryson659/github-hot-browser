# -*- coding: utf-8 -*-
"""工具库服务（阶段 A）：收录、分类维护与离线浏览。

UI 只调用本层；SQL 与事务在 `LibraryStore`。收录不等于收藏，也不自动开启追踪。
"""
from __future__ import annotations

import argparse
import sys

from app.core.db import Database
from app.core.models import Repo
from app.storage.library import LibraryStore


def ensure_defaults(db: Database) -> int:
    """启动时调用；重复执行不恢复已删除/改名的默认分类。"""
    return LibraryStore(db).seed_defaults()


def add_repo(db: Database, repo: Repo, *, categories: list[str] | None = None,
             source: str = "榜单收录") -> int:
    store = LibraryStore(db)
    return store.add_repo(repo, categories=categories, source=source)


def add_name(db: Database, name: str, *, categories: list[str] | None = None,
             summary: str = "", source: str = "手动收录",
             metadata: dict | None = None) -> int:
    return LibraryStore(db).add_entry(
        name, categories=categories, source=source,
        user_summary=summary, metadata=metadata)


def remove_entry(db: Database, name: str) -> bool:
    """从工具库移除；收藏、追踪、观测与仓库身份保留。"""
    return LibraryStore(db).remove_entry(name)


def entries(db: Database, *, category_id: str | None = None,
            uncategorized: bool = False, query: str = "",
            sort: str = "recent", limit: int | None = None) -> list[dict]:
    return LibraryStore(db).entries(
        category_id=category_id, uncategorized=uncategorized, query=query,
        sort=sort, limit=limit)


def categories(db: Database, *, include_hidden: bool = True) -> list[dict]:
    return LibraryStore(db).categories(include_hidden=include_hidden)


def entry(db: Database, name: str) -> dict | None:
    return LibraryStore(db).entry(name)


def entry_names(db: Database) -> set[str]:
    return {e["name"] for e in LibraryStore(db).entries()}


def growth_for(db: Database, names: list[str], days: int) -> dict[str, int]:
    """工具库排序用：只取同窗口、valid 的 net_stars 观测，未知不补 0。"""
    from datetime import date
    from app.storage.store import Store
    store = Store(db)
    out: dict[str, int] = {}

    def span(row: dict) -> int | None:
        try:
            start = date.fromisoformat(str(row.get("window_start", ""))[:10])
            end = date.fromisoformat(str(row.get("window_end", ""))[:10])
        except ValueError:
            return None
        return (end - start).days + 1

    for name in names:
        repo = store.repository(name)
        if repo is None:
            continue
        rows = []
        for r in store.observations(repo["id"], metric="net_stars"):
            if r.get("value") is None or r.get("completeness") != "valid":
                continue
            wd = span(r)
            if wd is not None and wd != days:
                continue
            rows.append(r)
        if not rows:
            continue
        latest = max(rows, key=lambda r: r.get("observed_at", ""))
        out[name] = int(latest["value"])
    return out


def update_category(db: Database, category_id: str, *,
                    description: str | None = None,
                    sort_order: int | None = None,
                    hidden: bool | None = None, icon: str | None = None):
    LibraryStore(db).update_category(
        category_id, description=description, sort_order=sort_order,
        hidden=hidden, icon=icon)


def reorder_categories(db: Database, ordered_ids: list[str]):
    LibraryStore(db).reorder_categories(ordered_ids)


def create_category(db: Database, name: str, *, description: str = "") -> str:
    return LibraryStore(db).create_category(name, description=description)


def rename_category(db: Database, category_id: str, name: str):
    LibraryStore(db).rename_category(category_id, name)


def delete_category(db: Database, category_id: str,
                    *, move_to: str | None = None) -> dict:
    return LibraryStore(db).delete_category(category_id, move_to=move_to)


def set_entry_categories(db: Database, name: str, category_ids: list[str]):
    LibraryStore(db).set_entry_categories(name, category_ids)


def counts(db: Database) -> dict:
    return LibraryStore(db).counts()


def _category_ids_by_name(store: LibraryStore, names: list[str]) -> list[str]:
    ids = []
    for name in names:
        category = store.category_by_name(name)
        if category is None:
            raise ValueError(f"分类不存在：{name}")
        ids.append(category["id"])
    return ids


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工具库命令行（阶段 A）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("categories")
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("repo")
    add.add_argument("--category", action="append", default=[])
    add.add_argument("--summary", default="")
    rm = sub.add_parser("remove")
    rm.add_argument("repo")
    args = parser.parse_args(argv)

    db = Database()
    try:
        store = LibraryStore(db)
        store.seed_defaults()
        if args.command == "categories":
            rows = store.categories()
            for row in rows:
                print(f"{row['id']}  {row['name']}  sort={row['sort_order']}  "
                      f"seed={row['seed_key'] or '-'}")
            return 0
        if args.command == "list":
            rows = store.entries()
            for row in rows:
                names = "、".join(c["name"] for c in row["categories"]) or "待分类"
                print(f"{row['name']}  ⭐{row.get('stars') or '?'}  [{names}]")
            print(f"共 {len(rows)} 项")
            return 0
        if args.command == "add":
            ids = _category_ids_by_name(store, args.category)
            store.add_entry(args.repo, categories=ids,
                            user_summary=args.summary)
            print(f"已加入工具库：{args.repo}")
            return 0
        if args.command == "remove":
            removed = store.remove_entry(args.repo)
            print("已移除" if removed else "不在工具库")
            return 0 if removed else 1
        return 2
    except Exception as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_main())
