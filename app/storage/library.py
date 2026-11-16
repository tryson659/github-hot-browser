# -*- coding: utf-8 -*-
"""工具库存储（阶段 A）：分类、收录条目与多对多关系。

分类删除使用墓碑（deleted=1），只移除分类关系；项目、收藏、追踪、观测保留。
默认分类按稳定 seed_key 播种；已删除/已改名的 seed 不会在升级时被恢复。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.core.db import Database
from app.domain.library import (
    CATEGORY_TEMPLATE_VERSION,
    DEFAULT_CATEGORIES,
    category_name_key,
    clean_category_name,
    clean_description,
    seed_category_id,
)
from app.core.models import Repo
from app.storage.store import Store


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LibraryStore:
    def __init__(self, db: Database):
        self._db = db
        self.repos = Store(db)

    # ---------- 默认分类 ----------
    def seed_defaults(self) -> int:
        """首次安装写入默认分类；已存在的 seed 不覆盖名称/排序/隐藏/删除状态。"""
        inserted = 0

        def op(c):
            nonlocal inserted
            for seed in DEFAULT_CATEGORIES:
                exists = c.execute(
                    "SELECT 1 FROM categories WHERE seed_key=?",
                    (seed.seed_key,)).fetchone()
                if exists:
                    continue        # 包括已删除墓碑：升级不得恢复
                # 同名自定义分类已存在时不制造唯一键冲突
                conflict = c.execute(
                    "SELECT 1 FROM categories WHERE normalized_name=? AND deleted=0",
                    (category_name_key(seed.name),)).fetchone()
                if conflict:
                    continue
                c.execute(
                    "INSERT INTO categories(id, name, normalized_name, sort_order,"
                    " description, seed_key, template_version, updated_at)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (seed_category_id(seed.seed_key), seed.name,
                     category_name_key(seed.name), seed.sort_order,
                     seed.description, seed.seed_key,
                     CATEGORY_TEMPLATE_VERSION, _now_iso()))
                inserted += 1

        self._db.write(op)
        self._db.set_setting("__library_template_version__",
                             CATEGORY_TEMPLATE_VERSION)
        return inserted

    # ---------- 分类 ----------
    def categories(self, *, include_hidden: bool = True,
                   include_deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM categories"
        where = []
        if not include_deleted:
            where.append("deleted=0")
        if not include_hidden:
            where.append("hidden=0")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY sort_order, name"
        rows = self._db.query(lambda c: c.execute(sql).fetchall())
        return [dict(r) for r in rows]

    def category(self, category_id: str) -> dict | None:
        row = self._db.query(lambda c: c.execute(
            "SELECT * FROM categories WHERE id=?", (category_id,)).fetchone())
        return dict(row) if row else None

    def category_by_name(self, name: str) -> dict | None:
        row = self._db.query(lambda c: c.execute(
            "SELECT * FROM categories WHERE normalized_name=? AND deleted=0",
            (category_name_key(name),)).fetchone())
        return dict(row) if row else None

    def create_category(self, name: str, *, description: str = "",
                        sort_order: int | None = None, icon: str = "") -> str:
        display = clean_category_name(name)
        key = category_name_key(display)
        category_id = str(uuid4())
        if sort_order is None:
            row = self._db.query(lambda c: c.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM categories"
                " WHERE deleted=0").fetchone())
            sort_order = int(row[0])
        try:
            self._db.write(lambda c: c.execute(
                "INSERT INTO categories(id, name, normalized_name, sort_order,"
                " description, icon, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (category_id, display, key, int(sort_order),
                 clean_description(description), icon or "", _now_iso())))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"分类已存在：{display}") from e
        return category_id

    def rename_category(self, category_id: str, name: str):
        display = clean_category_name(name)
        key = category_name_key(display)
        try:
            self._db.write(lambda c: c.execute(
                "UPDATE categories SET name=?, normalized_name=?, updated_at=?"
                " WHERE id=? AND deleted=0",
                (display, key, _now_iso(), category_id)))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"分类已存在：{display}") from e

    def update_category(self, category_id: str, *, description: str | None = None,
                        sort_order: int | None = None,
                        hidden: bool | None = None, icon: str | None = None):
        sets, params = [], []
        if description is not None:
            sets.append("description=?")
            params.append(clean_description(description))
        if sort_order is not None:
            sets.append("sort_order=?")
            params.append(int(sort_order))
        if hidden is not None:
            sets.append("hidden=?")
            params.append(int(bool(hidden)))
        if icon is not None:
            sets.append("icon=?")
            params.append(icon)
        if not sets:
            return
        sets.append("updated_at=?")
        params.extend([_now_iso(), category_id])
        self._db.write(lambda c: c.execute(
            f"UPDATE categories SET {', '.join(sets)} WHERE id=? AND deleted=0",
            tuple(params)))

    def reorder_categories(self, ordered_ids: list[str]):
        def op(c):
            for index, category_id in enumerate(ordered_ids):
                c.execute(
                    "UPDATE categories SET sort_order=?, updated_at=?"
                    " WHERE id=? AND deleted=0",
                    ((index + 1) * 10, _now_iso(), category_id))
        self._db.write(op)

    def delete_category(self, category_id: str, *,
                        move_to: str | None = None) -> dict:
        """墓碑删除分类，只移除关系；可选把受影响项目转到另一分类。"""
        if move_to == category_id:
            raise ValueError("不能移动到被删除的分类")
        if move_to is not None:
            target = self.category(move_to)
            if target is None or target["deleted"]:
                raise ValueError("目标分类不存在")
        stats = {"moved": 0, "removed": 0}

        def op(c):
            row = c.execute(
                "SELECT id FROM categories WHERE id=? AND deleted=0",
                (category_id,)).fetchone()
            if row is None:
                raise ValueError("分类不存在")
            repo_ids = [r["repo_id"] for r in c.execute(
                "SELECT repo_id FROM library_categories WHERE category_id=?",
                (category_id,)).fetchall()]
            if move_to is not None:
                c.execute(
                    "INSERT OR IGNORE INTO library_categories(repo_id, category_id,"
                    " origin, confirmed_at) SELECT repo_id, ?, origin, confirmed_at"
                    " FROM library_categories WHERE category_id=?",
                    (move_to, category_id))
                stats["moved"] = len(repo_ids)
            stats["removed"] = len(repo_ids)
            c.execute("DELETE FROM library_categories WHERE category_id=?",
                      (category_id,))
            c.execute(
                "UPDATE categories SET deleted=1, hidden=1, updated_at=?"
                " WHERE id=?", (_now_iso(), category_id))

        self._db.write(op)
        return stats

    # ---------- 工具库条目 ----------
    def add_entry(self, repo_name: str, *, categories: list[str] | None = None,
                  source: str = "", user_summary: str = "",
                  metadata: dict | None = None) -> int:
        category_ids = self._validate_categories(categories or [])
        metadata = metadata or {}
        repo_id = self.repos.upsert_repository(repo_name, **metadata)
        now = _now_iso()

        def op(c):
            c.execute(
                "INSERT OR IGNORE INTO library_entries(repo_id, added_at,"
                " user_summary, source, updated_at) VALUES(?, ?, ?, ?, ?)",
                (repo_id, now, user_summary or "", source or "", now))
            if user_summary:
                c.execute(
                    "UPDATE library_entries SET user_summary=?, updated_at=?"
                    " WHERE repo_id=?", (user_summary, now, repo_id))
            for category_id in category_ids:
                c.execute(
                    "INSERT OR REPLACE INTO library_categories(repo_id,"
                    " category_id, origin, confirmed_at) VALUES(?, ?, 'user', ?)",
                    (repo_id, category_id, now))
        self._db.write(op)
        return repo_id

    def add_repo(self, repo: Repo, *, categories: list[str] | None = None,
                 source: str = "榜单收录") -> int:
        return self.add_entry(
            repo.name, categories=categories, source=source,
            metadata=dict(url=repo.url, description=repo.description,
                          language=repo.language, topics=repo.topics,
                          archived=repo.archived, stars=repo.stars,
                          forks=repo.forks, pushed=repo.pushed,
                          license=repo.license, created=repo.created))

    def _validate_categories(self, category_ids: list[str]) -> list[str]:
        out = []
        for category_id in category_ids:
            category = self.category(category_id)
            if category is None or category["deleted"]:
                raise ValueError(f"分类不存在：{category_id}")
            if category_id not in out:
                out.append(category_id)
        return out

    def entry(self, repo_name: str) -> dict | None:
        repo = self.repos.repository(repo_name)
        if repo is None:
            return None
        row = self._db.query(lambda c: c.execute(
            "SELECT * FROM library_entries WHERE repo_id=?",
            (repo["id"],)).fetchone())
        if row is None:
            return None
        out = dict(row)
        out.update(self._repo_fields(repo))
        out["categories"] = self.entry_categories(repo["id"])
        return out

    def _repo_fields(self, repo: dict) -> dict:
        return {
            "name": repo["full_name"], "url": repo.get("url") or "",
            "description": repo.get("description") or "",
            "language": repo.get("language") or "-",
            "stars": repo.get("stars"), "forks": repo.get("forks"),
            "pushed": repo.get("pushed") or "",
            "archived": bool(repo.get("archived")),
        }

    def entry_categories(self, repo_id: int) -> list[dict]:
        rows = self._db.query(lambda c: c.execute(
            "SELECT c.id, c.name, c.hidden FROM library_categories lc"
            " JOIN categories c ON c.id = lc.category_id"
            " WHERE lc.repo_id=? AND c.deleted=0 ORDER BY c.sort_order, c.name",
            (repo_id,)).fetchall())
        return [dict(r) for r in rows]

    def entries(self, *, category_id: str | None = None,
                uncategorized: bool = False, query: str = "",
                sort: str = "recent", limit: int | None = None) -> list[dict]:
        sql = ("SELECT e.*, r.full_name AS name, r.url, r.description,"
               " r.language, r.stars, r.forks, r.pushed, r.archived"
               " FROM library_entries e JOIN repositories r ON r.id = e.repo_id")
        where, params = [], []
        if category_id is not None:
            sql += " JOIN library_categories lc ON lc.repo_id = e.repo_id"
            where.append("lc.category_id=?")
            params.append(category_id)
        if uncategorized:
            where.append(
                "NOT EXISTS (SELECT 1 FROM library_categories lc2"
                " JOIN categories c2 ON c2.id = lc2.category_id"
                " WHERE lc2.repo_id=e.repo_id AND c2.deleted=0)")
        text = (query or "").strip()
        if text:
            like = f"%{text.lower()}%"
            where.append(
                "(lower(r.full_name) LIKE ? OR lower(r.description) LIKE ?"
                " OR lower(e.user_summary) LIKE ?)")
            params.extend([like, like, like])
        if where:
            sql += " WHERE " + " AND ".join(where)
        if sort == "stars":
            sql += " ORDER BY r.stars IS NULL, r.stars DESC, e.added_at DESC"
        elif sort == "name":
            sql += " ORDER BY r.full_name"
        else:
            sql += " ORDER BY e.added_at DESC, r.full_name"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self._db.query(lambda c: c.execute(sql, tuple(params)).fetchall())
        out = []
        for row in rows:
            item = dict(row)
            item["archived"] = bool(item["archived"])
            item["categories"] = self.entry_categories(item["repo_id"])
            out.append(item)
        return out

    def set_entry_categories(self, repo_name: str, category_ids: list[str]):
        repo = self.repos.repository(repo_name)
        if repo is None:
            raise ValueError("项目不存在")
        valid = self._validate_categories(category_ids)
        now = _now_iso()

        def op(c):
            c.execute("DELETE FROM library_categories WHERE repo_id=?",
                      (repo["id"],))
            for category_id in valid:
                c.execute(
                    "INSERT INTO library_categories(repo_id, category_id,"
                    " origin, confirmed_at) VALUES(?, ?, 'user', ?)",
                    (repo["id"], category_id, now))
        self._db.write(op)

    def remove_entry(self, repo_name: str) -> bool:
        repo = self.repos.repository(repo_name)
        if repo is None:
            return False

        def op(c):
            c.execute("DELETE FROM library_categories WHERE repo_id=?",
                      (repo["id"],))
            cur = c.execute("DELETE FROM library_entries WHERE repo_id=?",
                            (repo["id"],))
            return cur.rowcount > 0
        return bool(self._db.write(op))

    def counts(self) -> dict:
        total = self._db.query(lambda c: c.execute(
            "SELECT count(*) FROM library_entries").fetchone()[0])
        uncategorized = len(self.entries(uncategorized=True))
        by_category = self._db.query(lambda c: c.execute(
            "SELECT c.id, count(lc.repo_id) AS n FROM categories c"
            " LEFT JOIN library_categories lc ON lc.category_id = c.id"
            " WHERE c.deleted=0 GROUP BY c.id").fetchall())
        return {"total": int(total), "uncategorized": uncategorized,
                "by_category": {r["id"]: int(r["n"]) for r in by_category}}
