# -*- coding: utf-8 -*-
"""批次 4：独立追踪调度、暂停/删除墓碑与标签。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import Database
from app.services import tracking
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


def _set_due(store, name, due):
    repo = store.repository(name)
    store.update_tracking(repo["id"], next_due_at=due)


class TestScheduling:
    def test_new_tracking_due_and_pause(self, db, store):
        rid = tracking.ensure_tracked(db, "owner/repo", store=store)
        _set_due(store, "owner/repo", NOW - timedelta(minutes=1))
        assert [r["repo_id"] for r in tracking.due_tracking(db, NOW)] == [rid]

        assert tracking.set_paused(db, "owner/repo", True, store=store)
        assert tracking.due_tracking(db, NOW) == []
        assert tracking.set_paused(db, "owner/repo", False, store=store)
        _set_due(store, "owner/repo", NOW - timedelta(minutes=1))
        assert tracking.due_tracking(db, NOW)          # 恢复后重新排期

    def test_ensure_does_not_reset_paused(self, db, store):
        tracking.ensure_tracked(db, "owner/repo", store=store)
        tracking.set_paused(db, "owner/repo", True, store=store)
        tracking.ensure_tracked(db, "owner/repo", store=store)  # 自动发现
        row = store.tracking(store.repository("owner/repo")["id"])
        assert row["paused"] == 1 and row["enabled"] == 1

    def test_removed_not_resurrected(self, db, store):
        tracking.ensure_tracked(db, "owner/repo", store=store)
        assert tracking.remove_tracking(db, "owner/repo", store=store)
        tracking.ensure_tracked(db, "owner/repo", store=store)  # 自动发现
        repo = store.repository("owner/repo")
        row = store.tracking(repo["id"])
        assert row["removed"] == 1 and row["enabled"] == 0
        assert tracking.due_tracking(db, NOW) == []

        tracking.add_tracking(db, "owner/repo", store=store)    # 用户显式恢复
        row = store.tracking(repo["id"])
        assert row["removed"] == 0 and row["enabled"] == 1

    def test_record_sample_schedules_next_due(self, db, store):
        tracking.ensure_tracked(db, "owner/repo", store=store)
        assert tracking.record_sample(db, "owner/repo", 120,
                                      sampled_at=NOW, store=store)
        repo = store.repository("owner/repo")
        row = store.tracking(repo["id"])
        assert row["last_sampled_at"].startswith("2026-11-15T12:00")
        assert row["next_due_at"].startswith("2026-11-15T18:00")
        assert store.star_samples(repo["id"]) == [(NOW, 120)]
        assert tracking.due_tracking(db, NOW + timedelta(hours=5)) == []
        assert tracking.due_tracking(db, NOW + timedelta(hours=7))

    def test_removed_tracking_cannot_sample(self, db, store):
        tracking.ensure_tracked(db, "owner/repo", store=store)
        tracking.remove_tracking(db, "owner/repo", store=store)
        assert not tracking.record_sample(db, "owner/repo", 1,
                                          sampled_at=NOW, store=store)

    def test_defer_without_fake_sample(self, db, store):
        tracking.ensure_tracked(db, "owner/repo", store=store)
        repo = store.repository("owner/repo")
        _set_due(store, "owner/repo", NOW - timedelta(minutes=5))
        assert tracking.defer(db, "owner/repo", until=NOW + timedelta(hours=2),
                              store=store)
        assert store.star_samples(repo["id"]) == []
        assert tracking.due_tracking(db, NOW) == []
        assert tracking.due_tracking(db, NOW + timedelta(hours=3))

    def test_restart_preserves_state(self, tmp_path):
        path = str(tmp_path / "t.db")
        db = Database(path)
        store = Store(db)
        tracking.add_tracking(db, "owner/repo", note="重启保留", store=store)
        tracking.set_tags(db, "owner/repo", ["引擎"], store=store)
        _set_due(store, "owner/repo", NOW + timedelta(hours=1))
        db.close()

        db2 = Database(path)
        try:
            store2 = Store(db2)
            row = store2.tracking(store2.repository("owner/repo")["id"])
            assert row["note"] == "重启保留"
            assert tracking.due_tracking(db2, NOW) == []
            assert tracking.due_tracking(db2, NOW + timedelta(hours=2))
            assert tracking.tags(db2, "owner/repo", store=store2)["user"] == ["引擎"]
        finally:
            db2.close()


class TestTags:
    def test_tag_origins_are_separate(self, db, store):
        rid = tracking.ensure_tracked(db, "owner/repo", store=store)
        store.set_tags(rid, ["引擎", "底层"], "user")
        store.set_tags(rid, ["cpp", "game"], "github_topic")
        got = tracking.tags(db, "owner/repo", store=store)
        assert got["user"] == ["底层", "引擎"] or got["user"] == ["引擎", "底层"]
        assert got["github_topic"] == ["cpp", "game"]

    def test_add_and_remove_tag(self, db, store):
        rid = tracking.ensure_tracked(db, "owner/repo", store=store)
        store.add_tag(rid, "a")
        store.add_tag(rid, "b")
        store.remove_tag(rid, "a")
        assert [t["tag"] for t in store.tags(rid)] == ["b"]

    def test_tag_validation(self, db, store):
        rid = tracking.ensure_tracked(db, "owner/repo", store=store)
        with pytest.raises(ValueError):
            store.set_tags(rid, [f"tag{i}" for i in range(31)])
        with pytest.raises(ValueError):
            store.set_tags(rid, ["x" * 41])
