# -*- coding: utf-8 -*-
"""批次 3：v2 存储层（身份、观测、采样、任务记录）。"""
from datetime import date, datetime, timezone

import pytest

from app.core.db import Database
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


def test_schema_is_additive_and_versioned(db):
    tables = set(db.query(lambda c: [
        r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]))
    assert {"repositories", "repository_aliases", "candidate_sources",
            "star_samples", "metric_observations", "source_runs",
            "tracking", "user_tags", "repository_tags",
            "archive_hours", "library_entries", "categories",
            "library_categories"} <= tables
    assert {"cache", "history", "favorites"} <= tables      # 旧表仍在
    assert db.schema_version == 5


class TestRepositories:
    def test_upsert_is_stable_and_normalized(self, store):
        first = store.upsert_repository(
            "https://github.com/Owner/Repo/tree/main",
            url="https://github.com/Owner/Repo", topics=["cpp"])
        second = store.upsert_repository("owner/repo")
        assert first == second
        repo = store.repository("OWNER/REPO")
        assert repo["full_name"] == "owner/repo"
        assert repo["topics"] == []
        store.upsert_repository("owner/repo", topics=["cpp"], language="C++")
        assert store.repository("owner/repo")["topics"] == ["cpp"]

    def test_invalid_name_rejected_or_missing(self, store):
        with pytest.raises(ValueError):
            store.upsert_repository("not-a-repo")
        assert store.repository("a/b/c") is None

    def test_alias_resolves_renamed_repo(self, store):
        rid = store.upsert_repository("renamed/repo")
        store.add_alias("github", "Old/Repo", rid)
        assert store.repository("old/repo")["id"] == rid

    def test_candidate_sources_keep_first_and_last(self, store):
        rid = store.upsert_repository("a/b")
        store.add_candidate(rid, "trending", datetime(2026, 11, 10, tzinfo=timezone.utc))
        store.add_candidate(rid, "trending", datetime(2026, 11, 12, tzinfo=timezone.utc))
        rows = store.candidates(rid)
        assert len(rows) == 1
        assert rows[0]["first_seen"].startswith("2026-11-10")
        assert rows[0]["last_seen"].startswith("2026-11-12")


class TestSamples:
    def test_upsert_same_timestamp(self, store):
        rid = store.upsert_repository("a/b")
        store.save_star_sample(rid, 100, NOW)
        store.save_star_sample(rid, 120, NOW)
        assert store.star_samples(rid) == [(NOW, 120)]

    def test_samples_sorted_and_filtered(self, store):
        rid = store.upsert_repository("a/b")
        store.save_star_sample(rid, 100, datetime(2026, 11, 8, tzinfo=timezone.utc))
        store.save_star_sample(rid, 120, datetime(2026, 11, 15, tzinfo=timezone.utc))
        assert [s for _, s in store.star_samples(rid)] == [100, 120]
        recent = store.star_samples(rid, since=datetime(2026, 11, 10, tzinfo=timezone.utc))
        assert [s for _, s in recent] == [120]

    @pytest.mark.parametrize("stars", [-1, 1.5, True, "5"])
    def test_invalid_sample_rejected(self, store, stars):
        rid = store.upsert_repository("a/b")
        with pytest.raises(ValueError):
            store.save_star_sample(rid, stars, NOW)


class TestObservations:
    def test_upsert_by_observation_key(self, store):
        rid = store.upsert_repository("a/b")
        kwargs = dict(source="github_history", metric="github_history_stars",
                      observation_key="gh:2026-11-01",
                      window_start="2026-11-01", window_end="2026-11-01",
                      granularity="daily", completeness="valid",
                      observed_at=NOW)
        store.save_observation(rid, value=5, **kwargs)
        store.save_observation(rid, value=9, **kwargs)
        rows = store.observations(rid, metric="github_history_stars")
        assert len(rows) == 1 and rows[0]["value"] == 9
        assert store.count_observations(rid, "github_history_stars") == 1

    def test_history_buckets_skip_unknown(self, store):
        rid = store.upsert_repository("a/b")
        store.save_observation(
            rid, source="github_history", metric="github_history_stars",
            observation_key="gh:day1", value=None,
            window_start="2026-11-01", window_end="2026-11-01",
            granularity="daily", completeness="missing")
        store.save_observation(
            rid, source="github_history", metric="github_history_stars",
            observation_key="gh:day2", value=0,
            window_start="2026-11-02", window_end="2026-11-02",
            granularity="daily", completeness="valid")
        buckets = store.history_buckets(rid)
        assert buckets == {date(2026, 11, 2): 0}

    def test_non_integer_value_rejected(self, store):
        rid = store.upsert_repository("a/b")
        with pytest.raises(ValueError):
            store.save_observation(
                rid, source="x", metric="m", observation_key="k",
                value=1.5, window_start="2026-11-01",
                window_end="2026-11-01")


class TestSourceRuns:
    def test_run_lifecycle(self, store):
        run_id = store.start_source_run("github_history", run_id="r1")
        assert store.source_run(run_id)["status"] == "running"
        store.finish_source_run(run_id, "success", ok_count=7)
        row = store.source_run(run_id)
        assert row["status"] == "success" and row["ok_count"] == 7
        assert row["finished_at"]
