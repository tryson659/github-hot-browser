# -*- coding: utf-8 -*-
"""SQLite 存储层。

替代旧版 12 个散落的 JSON 文件：单文件、事务、WAL 并发读、显式锁。
所有写操作走 _write（连接级 RLock + 事务），读操作走 _read。

表结构：
  settings  应用设置（key → JSON 值）
  cache     榜单/通道缓存 (channel, days) → repos JSON + 抓取时间 + 数据期
  history   抓取历史快照（增长基线来源）
  favorites 收藏（含收藏基准 saved_stars/saved_at）
  fav_meta  收藏备注/标签/里程碑墓碑（取消收藏不丢）
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

from app.core.config import DB_PATH, DEFAULT_SETTINGS
from app.core.models import Repo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    channel    TEXT NOT NULL,
    days       INTEGER NOT NULL,
    repos      TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    period     TEXT DEFAULT '',
    PRIMARY KEY (channel, days)
);
CREATE TABLE IF NOT EXISTS history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    channel  TEXT NOT NULL,
    days     INTEGER NOT NULL,
    taken_at TEXT NOT NULL,
    repos    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history ON history(channel, days, taken_at);
CREATE TABLE IF NOT EXISTS favorites (
    name        TEXT PRIMARY KEY,
    repo        TEXT NOT NULL,
    saved_stars INTEGER DEFAULT 0,
    saved_at    TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fav_meta (
    name      TEXT PRIMARY KEY,
    note      TEXT DEFAULT '',
    tags      TEXT DEFAULT '[]',
    milestone INTEGER DEFAULT 0
);
-- GH Archive 天级聚合：day → {repo: watch_count}
CREATE TABLE IF NOT EXISTS gharc_days (
    day    TEXT PRIMARY KEY,
    counts TEXT NOT NULL
);
-- GH Archive 小时级增量缓存（凑满 24 小时合并进天级后删除）
CREATE TABLE IF NOT EXISTS gharc_hours (
    hour   TEXT PRIMARY KEY,   -- "YYYY-MM-DD-HH"（UTC）
    counts TEXT NOT NULL
);
-- GH Archive 同步游标（单行表）
CREATE TABLE IF NOT EXISTS gharc_cursor (
    id     INTEGER PRIMARY KEY CHECK (id = 1),
    cursor TEXT DEFAULT ''
);
-- 增长观察名单
CREATE TABLE IF NOT EXISTS watchlist (
    name       TEXT PRIMARY KEY,
    url        TEXT DEFAULT '',
    desc       TEXT DEFAULT '',
    lang       TEXT DEFAULT '-',
    manual     INTEGER DEFAULT 0,
    first_seen TEXT DEFAULT ''
);
-- 观察名单每日采样：Star 数
CREATE TABLE IF NOT EXISTS watch_samples (
    name  TEXT NOT NULL,
    date  TEXT NOT NULL,       -- YYYY-MM-DD
    stars INTEGER DEFAULT 0,
    PRIMARY KEY (name, date)
);
-- 翻译缓存：原文 → 译文
CREATE TABLE IF NOT EXISTS translations (
    source     TEXT PRIMARY KEY,
    translated TEXT NOT NULL
);
-- 更新检测基线（收藏 + 手动观察项目）
CREATE TABLE IF NOT EXISTS update_check (
    name   TEXT PRIMARY KEY,
    stars  INTEGER DEFAULT 0,
    pushed TEXT DEFAULT '',
    at     REAL DEFAULT 0
);
-- 仓库 topics 标签缓存（ecosyste.ms 补齐，7 天有效）
CREATE TABLE IF NOT EXISTS repo_topics (
    name   TEXT PRIMARY KEY,
    topics TEXT DEFAULT '[]',
    at     REAL DEFAULT 0
);
-- Release 查询缓存
CREATE TABLE IF NOT EXISTS releases (
    name    TEXT PRIMARY KEY,
    tag     TEXT DEFAULT '',
    date    TEXT DEFAULT '',
    checked REAL DEFAULT 0
);
"""

# v2.1 业务模型表（批次 3）：只新增，不修改旧 cache/history/favorites。
# 旧表继续作为 legacy 缓存读取，新观测/身份/追踪逐步迁移到以下表。
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS repositories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id   INTEGER UNIQUE,
    full_name   TEXT NOT NULL UNIQUE,
    url         TEXT DEFAULT '',
    description TEXT DEFAULT '',
    language    TEXT DEFAULT '',
    topics      TEXT DEFAULT '[]',
    archived    INTEGER DEFAULT 0,
    stars       INTEGER,
    forks       INTEGER,
    pushed      TEXT DEFAULT '',
    license     TEXT DEFAULT '-',
    created     TEXT DEFAULT '',
    updated_at  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS repository_aliases (
    provider TEXT NOT NULL,
    name     TEXT NOT NULL,
    repo_id  INTEGER NOT NULL,
    PRIMARY KEY (provider, name)
);
CREATE TABLE IF NOT EXISTS candidate_sources (
    repo_id    INTEGER NOT NULL,
    source     TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (repo_id, source)
);
CREATE TABLE IF NOT EXISTS star_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id    INTEGER NOT NULL,
    sampled_at TEXT NOT NULL,
    stars      INTEGER NOT NULL CHECK (stars >= 0),
    run_id     TEXT DEFAULT '',
    UNIQUE (repo_id, sampled_at)
);
CREATE INDEX IF NOT EXISTS idx_star_samples
    ON star_samples(repo_id, sampled_at);
CREATE TABLE IF NOT EXISTS metric_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL,
    source          TEXT NOT NULL,
    metric          TEXT NOT NULL,
    observation_key TEXT NOT NULL UNIQUE,
    window_start    TEXT DEFAULT '',
    window_end      TEXT DEFAULT '',
    value           INTEGER,
    observed_at     TEXT NOT NULL,
    granularity     TEXT DEFAULT '',
    completeness    TEXT DEFAULT '',
    stale           INTEGER DEFAULT 0,
    provenance      TEXT DEFAULT '',
    parser_version  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_obs_repo_metric
    ON metric_observations(repo_id, metric, window_start);
CREATE TABLE IF NOT EXISTS source_runs (
    run_id      TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    status      TEXT DEFAULT '',
    error       TEXT DEFAULT '',
    ok_count    INTEGER DEFAULT 0,
    fail_count  INTEGER DEFAULT 0
);
"""

# v2.2 个人追踪与用户标签（批次 4）：与榜单缓存独立；暂停/删除状态不被自动发现重置。
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS tracking (
    repo_id             INTEGER PRIMARY KEY,
    favorite            INTEGER DEFAULT 0,
    enabled             INTEGER DEFAULT 1,
    paused              INTEGER DEFAULT 0,
    removed             INTEGER DEFAULT 0,
    note                TEXT DEFAULT '',
    sample_interval_hours REAL DEFAULT 6,
    next_due_at         TEXT DEFAULT '',
    last_sampled_at     TEXT DEFAULT '',
    updated_at          TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS user_tags (
    name  TEXT PRIMARY KEY,
    color TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS repository_tags (
    repo_id INTEGER NOT NULL,
    tag     TEXT NOT NULL,
    origin  TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (repo_id, tag, origin)
);
"""

# v2.3 事件小时账本（批次 5）：区分成功/空包/失败，游标只用于调度。
_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS archive_hours (
    source        TEXT NOT NULL,
    hour          TEXT NOT NULL,
    status        TEXT NOT NULL,
    event_count   INTEGER DEFAULT 0,
    downloaded_at TEXT DEFAULT '',
    checksum      TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    PRIMARY KEY (source, hour)
);
CREATE INDEX IF NOT EXISTS idx_archive_hours_status
    ON archive_hours(source, status, hour);
"""

# v2.4 工具库（阶段 A）：分类与项目多对多；删除分类用墓碑，不删除项目/收藏/追踪。
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS library_entries (
    repo_id      INTEGER PRIMARY KEY,
    added_at     TEXT NOT NULL,
    user_summary TEXT DEFAULT '',
    source       TEXT DEFAULT '',
    updated_at   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS categories (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    sort_order      INTEGER DEFAULT 0,
    description     TEXT DEFAULT '',
    seed_key        TEXT DEFAULT '',
    icon            TEXT DEFAULT '',
    hidden          INTEGER DEFAULT 0,
    deleted         INTEGER DEFAULT 0,
    template_version INTEGER DEFAULT 0,
    updated_at      TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_category_name
    ON categories(normalized_name) WHERE deleted=0;
CREATE INDEX IF NOT EXISTS idx_category_sort
    ON categories(deleted, hidden, sort_order);
CREATE TABLE IF NOT EXISTS library_categories (
    repo_id     INTEGER NOT NULL,
    category_id TEXT NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'user',
    confirmed_at TEXT DEFAULT '',
    PRIMARY KEY (repo_id, category_id)
);
CREATE INDEX IF NOT EXISTS idx_library_categories_category
    ON library_categories(category_id, repo_id);
"""

SCHEMA_VERSION = 5


class Database:
    """线程安全存储。工作线程与 UI 线程可并发调用。"""

    def __init__(self, path: str = DB_PATH):
        self._path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_SCHEMA_V2)
            self._conn.executescript(_SCHEMA_V3)
            self._conn.executescript(_SCHEMA_V4)
            self._conn.executescript(_SCHEMA_V5)
            self._conn.commit()
        self._ensure_repository_columns()
        self._ensure_history_unique()
        self._init_settings()
        self._init_schema_version()
        self.fixup_hot_channels()

    def _init_schema_version(self):
        if self.get_setting("__schema_version__", 0) != SCHEMA_VERSION:
            self.set_setting("__schema_version__", SCHEMA_VERSION)

    def _ensure_repository_columns(self):
        """旧 v2 库补齐工具库需要的仓库元数据列（只增列，不改旧列）。"""
        wanted = {
            "stars": "INTEGER",
            "forks": "INTEGER",
            "pushed": "TEXT DEFAULT ''",
            "license": "TEXT DEFAULT '-'",
            "created": "TEXT DEFAULT ''",
        }
        with self._lock:
            columns = {row[1] for row in self._conn.execute(
                "PRAGMA table_info(repositories)")}
            for name, ddl in wanted.items():
                if name not in columns:
                    self._conn.execute(
                        f"ALTER TABLE repositories ADD COLUMN {name} {ddl}")
            self._conn.commit()

    @property
    def schema_version(self) -> int:
        return int(self.get_setting("__schema_version__", 0) or 0)

    @property
    def path(self) -> str:
        return self._path

    @property
    def is_closed(self) -> bool:
        return self._closed

    def close(self):
        """关闭连接；重复调用安全（关闭后线程不应再访问数据库）。"""
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    # ---------- 一致性备份（R01） ----------
    def backup_to(self, dest_path: str):
        """用 SQLite 在线备份接口生成一致性副本（含 WAL 中已提交事务）。

        直接 copy2 主文件会漏掉 WAL 里尚未 checkpoint 的提交，恢复后可能
        丢数据或无法查询；本方法在连接仍打开时导出完整数据库。
        """
        dest_path = os.path.abspath(dest_path)
        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._lock:
            if self._closed:
                raise RuntimeError("数据库已关闭，无法备份")
            dest = sqlite3.connect(dest_path)
            try:
                self._conn.backup(dest)
                dest.execute("PRAGMA journal_mode=DELETE")
                dest.commit()
            finally:
                dest.close()
        return dest_path

    def integrity_check(self) -> str:
        """返回 SQLite 完整性检查结果，正常为 'ok'。"""
        row = self._read(lambda c: c.execute("PRAGMA integrity_check").fetchone())
        return row[0] if row else "unknown"

    def _ensure_history_unique(self):
        """历史快照以 (channel, days, taken_at) 唯一，迁移可重复执行（R06）。

        旧库可能已有重复行：先清理同键重复（保留最早 id），再建唯一索引。
        """
        def op(c):
            try:
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_history"
                          " ON history(channel, days, taken_at)")
            except sqlite3.IntegrityError:
                c.execute(
                    "DELETE FROM history WHERE id NOT IN ("
                    " SELECT MIN(id) FROM history GROUP BY channel, days, taken_at)")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_history"
                          " ON history(channel, days, taken_at)")
        self._write(op)

    # ---------- 基础读写 ----------
    def _write(self, fn):
        with self._lock:
            try:
                r = fn(self._conn)
                self._conn.commit()
                return r
            except Exception:
                self._conn.rollback()
                raise

    def _read(self, fn):
        with self._lock:
            return fn(self._conn)

    # ---------- 公开事务入口（供 storage 层使用，避免直接访问连接） ----------
    def write(self, fn):
        return self._write(fn)

    def query(self, fn):
        return self._read(fn)

    # ---------- 设置 ----------
    def _init_settings(self):
        def op(c):
            for k, v in DEFAULT_SETTINGS.items():
                c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                          (k, json.dumps(v, ensure_ascii=False)))
        self._write(op)

    def get_setting(self, key, default=None):
        row = self._read(lambda c: c.execute(
            "SELECT value FROM settings WHERE key=?", (key,)).fetchone())
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def set_setting(self, key, value):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
            (key, json.dumps(value, ensure_ascii=False))))

    def all_settings(self) -> dict:
        rows = self._read(lambda c: c.execute("SELECT key, value FROM settings").fetchall())
        out = dict(DEFAULT_SETTINGS)
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                pass
        return out

    # ---------- 榜单/通道缓存 ----------
    def save_cache(self, channel: str, days: int, repos: list[Repo], period: str = ""):
        payload = json.dumps([r.to_dict() for r in repos], ensure_ascii=False)
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO cache(channel, days, repos, fetched_at, period)"
            " VALUES(?, ?, ?, ?, ?)",
            (channel, days, payload, time.time(), period)))

    def load_cache(self, channel: str, days: int) -> tuple[list[Repo], float, str]:
        """返回 (repos, fetched_at, period)；无缓存返回 ([], 0, '')。"""
        row = self._read(lambda c: c.execute(
            "SELECT repos, fetched_at, period FROM cache WHERE channel=? AND days=?",
            (channel, days)).fetchone())
        if row is None:
            return [], 0.0, ""
        try:
            repos = [Repo.from_dict(d) for d in json.loads(row["repos"])]
        except Exception:
            repos = []
        return repos, row["fetched_at"], row["period"] or ""

    def cached_channels(self) -> list[str]:
        rows = self._read(lambda c: c.execute(
            "SELECT DISTINCT channel FROM cache").fetchall())
        return [r["channel"] for r in rows]

    def delete_cache(self, channel: str, days: int):
        self._write(lambda c: c.execute(
            "DELETE FROM cache WHERE channel=? AND days=?", (channel, days)))

    def fixup_hot_channels(self):
        """一次性整理：批1 时所有源都写在 HOT_KEY 下，若其 period 实为榜单数据，
        挪回 OpenGithubs 专属通道键（每源独立快照改造的历史遗留清理）。"""
        from app.core import config as _cfg
        if self.get_setting("__hot_ch_fixed__"):
            return
        for days in (7, 30):
            repos, _, period = self.load_cache(_cfg.HOT_KEY, days)
            if repos and ("周榜" in period or "月榜" in period
                          or "OpenGithubs" in period):
                key = _cfg.RANK_WEEK_KEY if days == 7 else _cfg.RANK_MONTH_KEY
                existing, _, _ = self.load_cache(key, days)
                if not existing:
                    self.save_cache(key, days, repos, period)
                self.delete_cache(_cfg.HOT_KEY, days)
        self.set_setting("__hot_ch_fixed__", True)

    # ---------- 历史快照（增长基线） ----------
    def save_history(self, channel: str, days: int, repos: list[Repo],
                     taken_at: str | None = None,
                     overwrite: bool = True) -> bool:
        """保存历史快照。

        (channel, days, taken_at) 唯一：迁移重复执行不会重复插入（R06）。
        overwrite=False 时保留已有同键快照（迁移用），返回是否新插入。
        """
        taken_at = taken_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = json.dumps([r.to_dict() for r in repos], ensure_ascii=False)
        sql = ("INSERT INTO history(channel, days, taken_at, repos)"
               " VALUES(?, ?, ?, ?) ON CONFLICT(channel, days, taken_at)"
               " DO UPDATE SET repos=excluded.repos")
        if not overwrite:
            sql = ("INSERT INTO history(channel, days, taken_at, repos)"
                   " VALUES(?, ?, ?, ?) ON CONFLICT(channel, days, taken_at)"
                   " DO NOTHING")
        cur = self._write(lambda c: c.execute(sql, (channel, days, taken_at, payload)))
        return bool(cur.rowcount)

    def baseline(self, channel: str, days: int,
                 min_age: float = 6.0, max_age: float = 8.0) -> dict[str, int]:
        """6~8 天前快照的 {name: stars}，作为近7天增长基线；无合格基线返回 {}。"""
        return self.baseline_snapshot(channel, days, min_age, max_age)[0]

    def baseline_snapshot(self, channel: str, days: int,
                          min_age: float = 6.0,
                          max_age: float = 8.0) -> tuple[dict[str, int], str]:
        """返回 (基线值, 基线快照时间)；用于标注实际采样窗口。"""
        rows = self._read(lambda c: c.execute(
            "SELECT taken_at, repos FROM history WHERE channel=? AND days=?"
            " ORDER BY taken_at DESC", (channel, days)).fetchall())
        now = datetime.now()
        baseline: dict[str, int] = {}
        baseline_at = ""
        for r in rows:
            try:
                t = datetime.strptime(r["taken_at"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            age = (now - t).total_seconds() / 86400
            if not (min_age <= age <= max_age):
                continue
            if not baseline_at:
                baseline_at = r["taken_at"]
            for d in json.loads(r["repos"]):
                if d.get("stars") is not None:
                    baseline.setdefault(d["name"], d["stars"])
        return baseline, baseline_at

    def latest_snapshot(self, channel: str, days: int,
                        exclude_latest: bool = False) -> list[Repo] | None:
        """最新（或次新，exclude_latest=True）快照，供历史对比徽章用。"""
        rows = self._read(lambda c: c.execute(
            "SELECT repos FROM history WHERE channel=? AND days=?"
            " ORDER BY taken_at DESC LIMIT 2", (channel, days)).fetchall())
        idx = 1 if exclude_latest else 0
        if len(rows) <= idx:
            return None
        try:
            return [Repo.from_dict(d) for d in json.loads(rows[idx]["repos"])]
        except Exception:
            return None

    def list_history(self, channel: str, days: int) -> list[tuple[int, str]]:
        rows = self._read(lambda c: c.execute(
            "SELECT id, taken_at FROM history WHERE channel=? AND days=?"
            " ORDER BY taken_at DESC", (channel, days)).fetchall())
        return [(r["id"], r["taken_at"]) for r in rows]

    def load_history_by_id(self, hid: int) -> list[Repo] | None:
        row = self._read(lambda c: c.execute(
            "SELECT repos FROM history WHERE id=?", (hid,)).fetchone())
        if row is None:
            return None
        try:
            return [Repo.from_dict(d) for d in json.loads(row["repos"])]
        except Exception:
            return None

    def delete_history(self, hid: int):
        self._write(lambda c: c.execute("DELETE FROM history WHERE id=?", (hid,)))

    def clear_history(self):
        self._write(lambda c: c.execute("DELETE FROM history"))

    # ---------- 收藏 ----------
    def add_favorite(self, repo: Repo):
        def op(c):
            c.execute(
                "INSERT OR REPLACE INTO favorites(name, repo, saved_stars, saved_at)"
                " VALUES(?, ?, ?, ?)",
                (repo.name, json.dumps(repo.to_dict(), ensure_ascii=False),
                 repo.stars or 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            c.execute("INSERT OR IGNORE INTO fav_meta(name) VALUES(?)", (repo.name,))
        self._write(op)

    def remove_favorite(self, name: str):
        # 只删 favorites，fav_meta 墓碑保留（备注/标签/里程碑不丢）
        self._write(lambda c: c.execute("DELETE FROM favorites WHERE name=?", (name,)))

    def is_favorite(self, name: str) -> bool:
        row = self._read(lambda c: c.execute(
            "SELECT 1 FROM favorites WHERE name=?", (name,)).fetchone())
        return row is not None

    def list_favorites(self) -> list[Repo]:
        rows = self._read(lambda c: c.execute(
            "SELECT repo FROM favorites ORDER BY saved_at DESC").fetchall())
        out = []
        for r in rows:
            try:
                out.append(Repo.from_dict(json.loads(r["repo"])))
            except Exception:
                pass
        return out

    def favorite_meta(self, name: str) -> dict:
        row = self._read(lambda c: c.execute(
            "SELECT f.saved_stars, f.saved_at, m.note, m.tags, m.milestone"
            " FROM favorites f LEFT JOIN fav_meta m ON m.name = f.name"
            " WHERE f.name=?", (name,)).fetchone())
        if row is None:
            return {}
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception:
            tags = []
        return {"saved_stars": row["saved_stars"] or 0, "saved_at": row["saved_at"] or "",
                "note": row["note"] or "", "tags": tags,
                "milestone": row["milestone"] or 0}

    def get_fav_meta(self, name: str) -> dict:
        """只读墓碑表（含已取消收藏的项目），迁移合并用。"""
        row = self._read(lambda c: c.execute(
            "SELECT note, tags, milestone FROM fav_meta WHERE name=?",
            (name,)).fetchone())
        if row is None:
            return {}
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception:
            tags = []
        return {"note": row["note"] or "", "tags": tags,
                "milestone": row["milestone"] or 0}

    def update_favorite_repo(self, repo: Repo):
        """刷新收藏项目的最新数据（保留收藏基准不动）。"""
        self._write(lambda c: c.execute(
            "UPDATE favorites SET repo=? WHERE name=?",
            (json.dumps(repo.to_dict(), ensure_ascii=False), repo.name)))

    def reset_favorite_baseline(self, name: str, stars: int):
        """把收藏基准更新为当前 Star 数（里程碑弹窗确认后调用）。"""
        self._write(lambda c: c.execute(
            "UPDATE favorites SET saved_stars=?, saved_at=? WHERE name=?",
            (stars, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name)))

    def set_favorite_baseline(self, name: str, stars: int, saved_at: str = ""):
        """写入收藏基准（迁移旧版数据用，公开方法，替代直接访问连接）。"""
        saved_at = saved_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write(lambda c: c.execute(
            "UPDATE favorites SET saved_stars=?, saved_at=? WHERE name=?",
            (stars, saved_at, name)))

    def set_favorite_note(self, name: str, note: str, tags: list[str]):
        # UPSERT：只更新 note/tags，保留已有 milestone（REPLACE 会整行覆盖）
        self._write(lambda c: c.execute(
            "INSERT INTO fav_meta(name, note, tags) VALUES(?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET note=excluded.note, tags=excluded.tags",
            (name, note, json.dumps(tags, ensure_ascii=False))))

    def set_milestone(self, name: str, milestone: int):
        self._write(lambda c: c.execute(
            "INSERT INTO fav_meta(name, milestone) VALUES(?, ?)"
            " ON CONFLICT(name) DO UPDATE SET milestone=excluded.milestone",
            (name, milestone)))

    # ---------- GH Archive ----------
    def gha_get_day(self, day: str) -> dict[str, int] | None:
        row = self._read(lambda c: c.execute(
            "SELECT counts FROM gharc_days WHERE day=?", (day,)).fetchone())
        return json.loads(row["counts"]) if row else None

    def gha_save_day(self, day: str, counts: dict[str, int]):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO gharc_days(day, counts) VALUES(?, ?)",
            (day, json.dumps(counts))))

    def gha_days(self) -> list[str]:
        rows = self._read(lambda c: c.execute(
            "SELECT day FROM gharc_days").fetchall())
        return [r["day"] for r in rows]

    def gha_get_hour(self, hour: str) -> dict[str, int] | None:
        row = self._read(lambda c: c.execute(
            "SELECT counts FROM gharc_hours WHERE hour=?", (hour,)).fetchone())
        return json.loads(row["counts"]) if row else None

    def gha_hours_by_day(self) -> dict[str, dict[int, dict[str, int]]]:
        """返回 {day: {hour_int: counts}}，供 24 小时合并判断。"""
        rows = self._read(lambda c: c.execute(
            "SELECT hour, counts FROM gharc_hours").fetchall())
        by_day: dict[str, dict[int, dict[str, int]]] = {}
        for r in rows:
            by_day.setdefault(r["hour"][:10], {})[int(r["hour"][11:])] = \
                json.loads(r["counts"])
        return by_day

    def gha_save_hour(self, hour: str, counts: dict[str, int]):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO gharc_hours(hour, counts) VALUES(?, ?)",
            (hour, json.dumps(counts))))

    def gha_delete_hours(self, hours: list[str]):
        def op(c):
            c.executemany("DELETE FROM gharc_hours WHERE hour=?",
                          [(h,) for h in hours])
        self._write(op)

    def gha_get_cursor(self) -> str:
        row = self._read(lambda c: c.execute(
            "SELECT cursor FROM gharc_cursor WHERE id=1").fetchone())
        return row["cursor"] if row else ""

    def gha_set_cursor(self, cursor: str):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO gharc_cursor(id, cursor) VALUES(1, ?)",
            (cursor,)))

    def gha_cleanup(self, keep_days: int = 40):
        """清理过旧的天级聚合（默认保留 40 天，覆盖 30 天窗口）。"""
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.now(timezone.utc) - _td(days=keep_days)).strftime("%Y-%m-%d")
        self._write(lambda c: c.execute(
            "DELETE FROM gharc_days WHERE day < ?", (cutoff,)))

    # ---------- 增长观察名单 ----------
    def watch_add(self, name: str, url: str = "", desc: str = "",
                  lang: str = "-", manual: bool = False):
        self._write(lambda c: c.execute(
            "INSERT INTO watchlist(name, url, desc, lang, manual, first_seen)"
            " VALUES(?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET"
            "   url=CASE WHEN excluded.url != '' THEN excluded.url ELSE watchlist.url END,"
            "   desc=CASE WHEN excluded.desc != '' THEN excluded.desc ELSE watchlist.desc END,"
            "   lang=CASE WHEN excluded.lang != '-' THEN excluded.lang ELSE watchlist.lang END,"
            "   manual=MAX(watchlist.manual, excluded.manual)",
            (name, url, desc, lang, int(manual),
             datetime.now().strftime("%Y-%m-%d"))))

    def watch_remove(self, name: str):
        def op(c):
            c.execute("DELETE FROM watchlist WHERE name=?", (name,))
            c.execute("DELETE FROM watch_samples WHERE name=?", (name,))
        self._write(op)

    def watch_list(self, manual_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM watchlist" + (" WHERE manual=1" if manual_only else "")
        rows = self._read(lambda c: c.execute(sql).fetchall())
        return [dict(r) for r in rows]

    def watch_sample(self, name: str, stars: int, date: str | None = None):
        date = date or datetime.now().strftime("%Y-%m-%d")
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO watch_samples(name, date, stars)"
            " VALUES(?, ?, ?)", (name, date, stars)))

    def watch_samples(self, name: str, days: int = 60) -> dict[str, int]:
        from datetime import timedelta as _td
        cutoff = (datetime.now() - _td(days=days)).strftime("%Y-%m-%d")
        rows = self._read(lambda c: c.execute(
            "SELECT date, stars FROM watch_samples WHERE name=? AND date>=?",
            (name, cutoff)).fetchall())
        return {r["date"]: r["stars"] for r in rows}

    def watch_cleanup(self, keep_days: int = 60):
        from datetime import timedelta as _td
        cutoff = (datetime.now() - _td(days=keep_days)).strftime("%Y-%m-%d")
        self._write(lambda c: c.execute(
            "DELETE FROM watch_samples WHERE date < ?", (cutoff,)))

    # ---------- 翻译缓存 ----------
    def get_translation(self, source: str) -> str | None:
        row = self._read(lambda c: c.execute(
            "SELECT translated FROM translations WHERE source=?",
            (source,)).fetchone())
        return row["translated"] if row else None

    def save_translation(self, source: str, translated: str):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO translations(source, translated) VALUES(?, ?)",
            (source, translated)))

    # ---------- 更新检测基线 ----------
    def update_baseline_get(self, name: str) -> dict | None:
        row = self._read(lambda c: c.execute(
            "SELECT stars, pushed, at FROM update_check WHERE name=?",
            (name,)).fetchone())
        return dict(row) if row else None

    def update_baseline_set(self, name: str, stars: int, pushed: str):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO update_check(name, stars, pushed, at)"
            " VALUES(?, ?, ?, ?)", (name, stars, pushed, time.time())))

    # ---------- topics 标签缓存 ----------
    def get_topics(self, name: str, max_age_days: float = 7) -> list[str] | None:
        """返回缓存的 topics；无缓存或过期返回 None。"""
        row = self._read(lambda c: c.execute(
            "SELECT topics, at FROM repo_topics WHERE name=?", (name,)).fetchone())
        if row is None:
            return None
        if time.time() - (row["at"] or 0) > max_age_days * 86400:
            return None
        try:
            return json.loads(row["topics"] or "[]")
        except Exception:
            return None

    def save_topics(self, name: str, topics: list[str]):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO repo_topics(name, topics, at) VALUES(?, ?, ?)",
            (name, json.dumps(topics, ensure_ascii=False), time.time())))

    def topics_missing(self, names: list[str]) -> list[str]:
        return [n for n in names if self.get_topics(n) is None]

    # ---------- Release 缓存 ----------
    def get_release(self, name: str) -> dict | None:
        row = self._read(lambda c: c.execute(
            "SELECT tag, date FROM releases WHERE name=?", (name,)).fetchone())
        return dict(row) if row else None

    def save_release(self, name: str, tag: str, date: str):
        self._write(lambda c: c.execute(
            "INSERT OR REPLACE INTO releases(name, tag, date, checked)"
            " VALUES(?, ?, ?, ?)", (name, tag, date, time.time())))
