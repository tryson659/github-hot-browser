# -*- coding: utf-8 -*-
"""存储层：仓库身份、采样、指标观测与数据源任务记录。

只通过 `Database.write/query` 访问 SQLite，不持有连接、不做业务判断。
旧表（cache/history/favorites）保持只读兼容；v2 表只新增不覆盖。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

from app.core.db import Database
from app.domain.identity import normalize_repo_name


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_OBS_UPSERT = (
    "INSERT INTO metric_observations("
    " repo_id, source, metric, observation_key, window_start, window_end,"
    " value, observed_at, granularity, completeness, stale, provenance,"
    " parser_version) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(observation_key) DO UPDATE SET"
    "   value=excluded.value, observed_at=excluded.observed_at,"
    "   window_start=excluded.window_start, window_end=excluded.window_end,"
    "   granularity=excluded.granularity, completeness=excluded.completeness,"
    "   stale=excluded.stale, provenance=excluded.provenance,"
    "   parser_version=excluded.parser_version")


def _obs_params(row: dict) -> tuple:
    value = row.get("value")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError("value 必须是整数或 None")
    key = row.get("observation_key")
    if not key:
        raise ValueError("observation_key 不能为空")
    return (
        row["repo_id"], row["source"], row["metric"], key,
        row.get("window_start", ""), row.get("window_end", ""), value,
        _iso(row.get("observed_at") or _utc_now()),
        row.get("granularity", ""), row.get("completeness", ""),
        int(bool(row.get("stale", False))), row.get("provenance", ""),
        row.get("parser_version", ""),
    )


class Store:
    def __init__(self, db: Database):
        self._db = db

    # ---------- 仓库身份 ----------
    def upsert_repository(self, name: str, *, github_id: int | None = None,
                          url: str = "", description: str = "",
                          language: str = "", topics: list[str] | None = None,
                          archived: bool = False,
                          stars: int | None = None, forks: int | None = None,
                          pushed: str = "", license: str = "",
                          created: str = "") -> int:
        canonical = normalize_repo_name(name).lower()
        now = _iso(_utc_now())
        topics_json = json.dumps(topics or [], ensure_ascii=False)

        def op(c):
            row = c.execute(
                "SELECT id, github_id FROM repositories WHERE full_name=?",
                (canonical,)).fetchone()
            if row is None:
                cur = c.execute(
                    "INSERT INTO repositories(github_id, full_name, url, description,"
                    " language, topics, archived, stars, forks, pushed, license,"
                    " created, updated_at)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (github_id, canonical, url, description, language,
                     topics_json, int(archived), stars, forks, pushed or "",
                     license or "-", created or "", now))
                return int(cur.lastrowid)
            if github_id is not None and row["github_id"] is None:
                c.execute("UPDATE repositories SET github_id=? WHERE id=?",
                          (github_id, row["id"]))
            c.execute(
                "UPDATE repositories SET url=?, description=?, language=?,"
                " topics=?, archived=?, updated_at=? WHERE id=?",
                (url or "", description or "", language or "", topics_json,
                 int(archived), now, row["id"]))
            meta = {k: v for k, v in (
                ("stars", stars), ("forks", forks),
                ("pushed", pushed), ("license", license), ("created", created))
                if v not in (None, "")}
            if meta:
                sets = ", ".join(f"{k}=?" for k in meta)
                c.execute(f"UPDATE repositories SET {sets} WHERE id=?",
                          (*meta.values(), row["id"]))
            return int(row["id"])

        return self._db.write(op)

    def repository(self, name: str) -> dict | None:
        """按当前名称或别名查找仓库；未知返回 None。"""
        try:
            canonical = normalize_repo_name(name).lower()
        except ValueError:
            return None
        row = self._db.query(lambda c: c.execute(
            "SELECT * FROM repositories WHERE full_name=?", (canonical,)).fetchone())
        if row is None:
            row = self._db.query(lambda c: c.execute(
                "SELECT r.* FROM repositories r JOIN repository_aliases a"
                " ON a.repo_id = r.id WHERE a.name=?", (canonical,)).fetchone())
        if row is None:
            return None
        out = dict(row)
        try:
            out["topics"] = json.loads(out.get("topics") or "[]")
        except ValueError:
            out["topics"] = []
        return out

    def add_alias(self, provider: str, name: str, repo_id: int):
        self._db.write(lambda c: c.execute(
            "INSERT OR REPLACE INTO repository_aliases(provider, name, repo_id)"
            " VALUES(?, ?, ?)", (provider, name.lower(), repo_id)))

    def add_candidate(self, repo_id: int, source: str,
                      at: datetime | None = None):
        stamp = _iso(at or _utc_now())
        self._db.write(lambda c: c.execute(
            "INSERT INTO candidate_sources(repo_id, source, first_seen, last_seen)"
            " VALUES(?, ?, ?, ?) ON CONFLICT(repo_id, source)"
            " DO UPDATE SET last_seen=excluded.last_seen",
            (repo_id, source, stamp, stamp)))

    def candidates(self, repo_id: int) -> list[dict]:
        rows = self._db.query(lambda c: c.execute(
            "SELECT source, first_seen, last_seen FROM candidate_sources"
            " WHERE repo_id=? ORDER BY source", (repo_id,)).fetchall())
        return [dict(r) for r in rows]

    # ---------- 总量采样 ----------
    def save_star_sample(self, repo_id: int, stars: int,
                         sampled_at: datetime | None = None,
                         run_id: str = ""):
        if not isinstance(stars, int) or isinstance(stars, bool) or stars < 0:
            raise ValueError("stars 必须是 >=0 的整数")
        stamp = _iso(sampled_at or _utc_now())
        self._db.write(lambda c: c.execute(
            "INSERT INTO star_samples(repo_id, sampled_at, stars, run_id)"
            " VALUES(?, ?, ?, ?) ON CONFLICT(repo_id, sampled_at)"
            " DO UPDATE SET stars=excluded.stars, run_id=excluded.run_id",
            (repo_id, stamp, stars, run_id)))

    def star_samples(self, repo_id: int,
                     since: datetime | None = None) -> list[tuple[datetime, int]]:
        sql = ("SELECT sampled_at, stars FROM star_samples WHERE repo_id=?"
               + (" AND sampled_at>=?" if since else "")
               + " ORDER BY sampled_at")
        params = (repo_id, _iso(since)) if since else (repo_id,)
        rows = self._db.query(lambda c: c.execute(sql, params).fetchall())
        return [(_parse_iso(r["sampled_at"]), int(r["stars"])) for r in rows]

    # ---------- 指标观测 ----------
    def save_observation(self, repo_id: int, *, source: str, metric: str,
                         observation_key: str, value: int | None,
                         observed_at: datetime | None = None,
                         window_start: str = "", window_end: str = "",
                         granularity: str = "", completeness: str = "",
                         stale: bool = False, provenance: str = "",
                         parser_version: str = ""):
        self.save_observations([dict(
            repo_id=repo_id, source=source, metric=metric,
            observation_key=observation_key, value=value,
            observed_at=observed_at, window_start=window_start,
            window_end=window_end, granularity=granularity,
            completeness=completeness, stale=stale, provenance=provenance,
            parser_version=parser_version)])

    def save_observations(self, rows: list[dict]):
        """一个事务写入一批观测：失败整体回滚，避免"完成但只有一半"。"""
        if not rows:
            return
        params = [_obs_params(r) for r in rows]

        def op(c):
            c.executemany(_OBS_UPSERT, params)

        self._db.write(op)

    def observations(self, repo_id: int, metric: str | None = None,
                     since: date | None = None) -> list[dict]:
        sql = "SELECT * FROM metric_observations WHERE repo_id=?"
        params: list = [repo_id]
        if metric:
            sql += " AND metric=?"
            params.append(metric)
        if since:
            sql += " AND window_start>=?"
            params.append(since.isoformat())
        sql += " ORDER BY window_start, observed_at"
        rows = self._db.query(lambda c: c.execute(sql, tuple(params)).fetchall())
        return [dict(r) for r in rows]

    def history_buckets(self, repo_id: int,
                        since: date | None = None) -> dict[date, int]:
        """官方历史日桶：window_start=window_end=桶日期；未知值不写入。"""
        out: dict[date, int] = {}
        for row in self.observations(repo_id, metric="github_history_stars",
                                     since=since):
            if row["value"] is None or not row["window_start"]:
                continue
            try:
                day = date.fromisoformat(row["window_start"][:10])
            except ValueError:
                continue
            out[day] = int(row["value"])
        return out

    def count_observations(self, repo_id: int, metric: str) -> int:
        return int(self._db.query(lambda c: c.execute(
            "SELECT count(*) FROM metric_observations WHERE repo_id=? AND metric=?",
            (repo_id, metric)).fetchone()[0]))

    # ---------- 数据源任务 ----------
    def start_source_run(self, source: str, run_id: str | None = None) -> str:
        run_id = run_id or uuid.uuid4().hex[:12]
        self._db.write(lambda c: c.execute(
            "INSERT INTO source_runs(run_id, source, started_at, status)"
            " VALUES(?, ?, ?, 'running')", (run_id, source, _iso(_utc_now()))))
        return run_id

    def finish_source_run(self, run_id: str, status: str, *,
                          ok_count: int = 0, fail_count: int = 0,
                          error: str = ""):
        self._db.write(lambda c: c.execute(
            "UPDATE source_runs SET finished_at=?, status=?, ok_count=?,"
            " fail_count=?, error=? WHERE run_id=?",
            (_iso(_utc_now()), status, int(ok_count), int(fail_count),
             (error or "")[:500], run_id)))

    def source_run(self, run_id: str) -> dict | None:
        row = self._db.query(lambda c: c.execute(
            "SELECT * FROM source_runs WHERE run_id=?", (run_id,)).fetchone())
        return dict(row) if row else None

    def latest_source_run(self, source: str) -> dict | None:
        row = self._db.query(lambda c: c.execute(
            "SELECT * FROM source_runs WHERE source=?"
            " ORDER BY started_at DESC LIMIT 1", (source,)).fetchone())
        return dict(row) if row else None

    # ---------- 个人追踪 ----------
    def ensure_tracking(self, repo_id: int, *, favorite: bool = False,
                        enabled: bool = True, paused: bool = False,
                        note: str = "", interval_hours: float = 6.0,
                        next_due_at: datetime | None = None) -> dict:
        """只创建缺失的追踪行；已存在时绝不修改启用/暂停/备注（防自动发现覆盖）。"""
        now = _iso(_utc_now())
        next_due = _iso(next_due_at) if next_due_at else now

        def op(c):
            c.execute(
                "INSERT OR IGNORE INTO tracking(repo_id, favorite, enabled, paused,"
                " note, sample_interval_hours, next_due_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (repo_id, int(favorite), int(enabled), int(paused), note or "",
                 float(interval_hours), next_due, now))
        self._db.write(op)
        return self.tracking(repo_id) or {}

    def tracking(self, repo_id: int) -> dict | None:
        row = self._db.query(lambda c: c.execute(
            "SELECT t.*, r.full_name AS name, r.url AS url"
            " FROM tracking t JOIN repositories r ON r.id = t.repo_id"
            " WHERE t.repo_id=?", (repo_id,)).fetchone())
        return dict(row) if row else None

    def list_tracking(self, *, enabled_only: bool = False,
                      include_paused: bool = True,
                      include_removed: bool = False) -> list[dict]:
        sql = ("SELECT t.*, r.full_name AS name, r.url AS url"
               " FROM tracking t JOIN repositories r ON r.id = t.repo_id")
        where = []
        if enabled_only:
            where.append("t.enabled=1")
        if not include_paused:
            where.append("t.paused=0")
        if not include_removed:
            where.append("t.removed=0")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t.next_due_at, t.repo_id"
        rows = self._db.query(lambda c: c.execute(sql).fetchall())
        return [dict(r) for r in rows]

    def update_tracking(self, repo_id: int, **fields):
        allowed = {"favorite", "enabled", "paused", "removed", "note",
                   "sample_interval_hours", "next_due_at", "last_sampled_at"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不支持的追踪字段：{sorted(unknown)}")
        if not fields:
            return
        sets, params = [], []
        for key, value in fields.items():
            if key in ("favorite", "enabled", "paused", "removed"):
                value = int(bool(value))
            elif key == "sample_interval_hours":
                value = max(1.0, float(value))
            elif key in ("next_due_at", "last_sampled_at") and isinstance(
                    value, datetime):
                value = _iso(value)
            sets.append(f"{key}=?")
            params.append(value)
        sets.append("updated_at=?")
        params.append(_iso(_utc_now()))
        params.append(repo_id)
        self._db.write(lambda c: c.execute(
            f"UPDATE tracking SET {', '.join(sets)} WHERE repo_id=?",
            tuple(params)))

    def set_paused(self, repo_id: int, paused: bool):
        self.update_tracking(repo_id, paused=paused)

    def set_enabled(self, repo_id: int, enabled: bool):
        self.update_tracking(repo_id, enabled=enabled)

    def set_note(self, repo_id: int, note: str):
        """用户显式编辑备注时才覆盖；导入合并使用 ensure/update 策略。"""
        self.update_tracking(repo_id, note=(note or "").strip())

    def delete_tracking(self, repo_id: int):
        """停止追踪并留下墓碑；自动发现不得把已删除项重新启用。"""
        self.update_tracking(repo_id, removed=True, enabled=False, paused=True)

    # ---------- 用户标签 / 仓库标签 ----------
    def set_tags(self, repo_id: int, tags: list[str], origin: str = "user"):
        cleaned: list[str] = []
        for tag in tags or []:
            tag = (tag or "").strip()
            if not tag:
                continue
            if len(tag) > 40:
                raise ValueError(f"标签过长：{tag[:20]}…")
            if tag not in cleaned:
                cleaned.append(tag)
        if len(cleaned) > 30:
            raise ValueError("单个仓库最多 30 个标签")
        if origin not in ("user", "github_topic"):
            raise ValueError(f"未知标签来源：{origin}")

        def op(c):
            c.execute("DELETE FROM repository_tags WHERE repo_id=? AND origin=?",
                      (repo_id, origin))
            c.executemany(
                "INSERT INTO repository_tags(repo_id, tag, origin) VALUES(?, ?, ?)",
                [(repo_id, tag, origin) for tag in cleaned])
            if origin == "user":
                c.executemany(
                    "INSERT OR IGNORE INTO user_tags(name) VALUES(?)",
                    [(tag,) for tag in cleaned])
        self._db.write(op)

    def add_tag(self, repo_id: int, tag: str, origin: str = "user"):
        current = [t["tag"] for t in self.tags(repo_id) if t["origin"] == origin]
        self.set_tags(repo_id, current + [tag], origin)

    def remove_tag(self, repo_id: int, tag: str, origin: str = "user"):
        self._db.write(lambda c: c.execute(
            "DELETE FROM repository_tags WHERE repo_id=? AND tag=? AND origin=?",
            (repo_id, tag, origin)))

    def tags(self, repo_id: int) -> list[dict]:
        rows = self._db.query(lambda c: c.execute(
            "SELECT tag, origin FROM repository_tags WHERE repo_id=?"
            " ORDER BY origin, tag", (repo_id,)).fetchall())
        return [dict(r) for r in rows]

    def user_tags(self) -> list[dict]:
        rows = self._db.query(lambda c: c.execute(
            "SELECT name, color FROM user_tags ORDER BY name").fetchall())
        return [dict(r) for r in rows]

    # ---------- 事件小时账本 ----------
    def mark_archive_hour(self, source: str, hour: str, status: str, *,
                          event_count: int = 0, checksum: str = "",
                          error: str = ""):
        if status not in ("success", "empty", "failed"):
            raise ValueError(f"未知账本状态：{status}")
        self._db.write(lambda c: c.execute(
            "INSERT INTO archive_hours(source, hour, status, event_count,"
            " downloaded_at, checksum, error) VALUES(?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(source, hour) DO UPDATE SET"
            "   status=excluded.status, event_count=excluded.event_count,"
            "   downloaded_at=excluded.downloaded_at, checksum=excluded.checksum,"
            "   error=excluded.error",
            (source, hour, status, int(event_count), _iso(_utc_now()),
             checksum or "", (error or "")[:300])))

    def archive_hour(self, source: str, hour: str) -> dict | None:
        row = self._db.query(lambda c: c.execute(
            "SELECT * FROM archive_hours WHERE source=? AND hour=?",
            (source, hour)).fetchone())
        return dict(row) if row else None

    def archive_hours(self, source: str, *, start: str | None = None,
                      end: str | None = None,
                      status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM archive_hours WHERE source=?"
        params: list = [source]
        if start:
            sql += " AND hour>=?"
            params.append(start)
        if end:
            sql += " AND hour<=?"
            params.append(end)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY hour"
        rows = self._db.query(lambda c: c.execute(sql, tuple(params)).fetchall())
        return [dict(r) for r in rows]

    def failed_archive_hours(self, source: str, start: str,
                             end: str) -> list[str]:
        return [r["hour"] for r in self.archive_hours(
            source, start=start, end=end, status="failed")]

    def archive_status_counts(self, source: str, start: str,
                              end: str) -> dict[str, int]:
        rows = self._db.query(lambda c: c.execute(
            "SELECT status, count(*) AS n FROM archive_hours"
            " WHERE source=? AND hour>=? AND hour<=? GROUP BY status",
            (source, start, end)).fetchall())
        return {r["status"]: int(r["n"]) for r in rows}


def since_days(days: int, now: datetime | None = None) -> datetime:
    return (now or _utc_now()) - timedelta(days=days)
