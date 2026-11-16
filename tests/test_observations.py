# -*- coding: utf-8 -*-
"""批次 3：观测服务（历史日桶/总量采样 → 窗口结果）。"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.db import Database
from app.domain.metrics import Completeness, Metric
from app.services import observations
from app.sources.github_history import HistoryDay, StarHistory
from app.storage.store import Store

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


@pytest.fixture()
def store(db):
    return Store(db)


def _history(repo="owner/repo", counts=None, fetched_at=NOW):
    counts = counts or {11: 10}
    days = []
    for offset in range(7, 0, -1):
        day = NOW.date() - timedelta(days=offset)
        days.append(HistoryDay(day, counts.get(day.day, 10)))
    return StarHistory(repo=repo, days=tuple(days), fetched_at=fetched_at)


def test_record_and_window_complete(db, store):
    hist = _history()
    assert observations.record_history(db, "owner/repo", hist) == 7
    result = observations.history_window(db, "owner/repo", 7, now=NOW)
    assert result is not None
    assert result.metric is Metric.GITHUB_HISTORY_STARS
    assert result.value == 70
    assert result.completeness is Completeness.VALID
    assert result.actual_start and result.actual_end


def test_missing_bucket_is_partial(db):
    hist = _history()
    # 去掉 11-10
    days = tuple(d for d in hist.days if d.day != date(2026, 11, 10))
    observations.record_history(
        db, "owner/repo", StarHistory("owner/repo", days, NOW))
    result = observations.history_window(db, "owner/repo", 7, now=NOW)
    assert result.value == 60
    assert result.completeness is Completeness.PARTIAL
    assert "缺 1/7" in result.reason


def test_revision_replaces_not_accumulates(db):
    hist = _history()
    observations.record_history(db, "owner/repo", hist)
    revised = tuple(HistoryDay(d.day, 99) for d in hist.days)
    observations.record_history(
        db, "owner/repo", StarHistory("owner/repo", revised, NOW))
    store = Store(db)
    repo = store.repository("owner/repo")
    assert store.count_observations(repo["id"], "github_history_stars") == 7
    assert observations.history_window(
        db, "owner/repo", 7, now=NOW).value == 693


def test_unknown_repo_window_none(db):
    assert observations.history_window(db, "no/repo", 7, now=NOW) is None


def test_net_window_from_samples(db):
    observations.record_sample(db, "owner/repo", 100, sampled_at=NOW - timedelta(days=7))
    observations.record_sample(db, "owner/repo", 170, sampled_at=NOW - timedelta(hours=2))
    result = observations.net_window(db, "owner/repo", 7, now=NOW)
    assert result.value == 70
    assert result.completeness is Completeness.VALID


def test_net_window_missing_start_is_none_value(db):
    observations.record_sample(db, "owner/repo", 170, sampled_at=NOW - timedelta(days=2))
    result = observations.net_window(db, "owner/repo", 7, now=NOW)
    assert result.value is None
    assert result.completeness is Completeness.MISSING


def test_sync_success_records_run(db, store):
    info = observations.sync_history(db, "owner/repo", fetch=lambda name: _history(name))
    assert info["days"] == 7
    run = store.source_run(info["run_id"])
    assert run["status"] == "success" and run["ok_count"] == 7


def test_sync_failure_marks_run_failed(db, store):
    def boom(name):
        raise RuntimeError("HTTP 500")

    with pytest.raises(RuntimeError):
        observations.sync_history(db, "owner/repo", fetch=boom)
    rows = db.query(lambda c: c.execute(
        "SELECT status, error FROM source_runs ORDER BY started_at DESC").fetchall())
    assert rows and rows[0]["status"] == "failed"
    assert "HTTP 500" in rows[0]["error"]
    assert store.repository("owner/repo") is None    # 失败不创建伪仓库
