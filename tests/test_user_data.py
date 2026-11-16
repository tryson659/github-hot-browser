# -*- coding: utf-8 -*-
"""批次 4：用户数据版本化 JSON 往返、合并策略与原子写入。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import Database
from app.core.models import Repo
from app.services import tracking
from app.services import user_data
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


def _seed(db, store):
    rid = tracking.add_tracking(db, "owner/repo", note="用户备注",
                                tags=["引擎"], store=store)
    store.update_tracking(rid, favorite=True, next_due_at=NOW + timedelta(hours=2))
    tracking.record_sample(db, "owner/repo", 150, sampled_at=NOW, store=store)
    store.update_tracking(rid, next_due_at=NOW + timedelta(hours=2))
    return rid


class TestExport:
    def test_shape_and_secrets(self, db, store):
        _seed(db, store)
        db.set_setting("github_token", "SECRET-TOKEN")
        payload = user_data.export_user_data(
            db, include_history=True, store=store, now=NOW)
        assert payload["schema_version"] == 1
        item = payload["items"][0]
        assert item["name"] == "owner/repo"
        assert item["favorite"] is True
        assert item["note"] == "用户备注"
        assert item["tags"]["user"] == ["引擎"]
        assert item["next_due_at"].startswith("2026-11-15T14:00")
        assert payload["history"]["samples"]["owner/repo"] == [
            ["2026-11-15T12:00:00Z", 150]]
        text = json.dumps(payload, ensure_ascii=False)
        assert "SECRET-TOKEN" not in text and "github_token" not in text

    def test_legacy_favorite_exported_without_auto_sampling(self, db, store):
        db.add_favorite(Repo(name="old/fav", url="https://github.com/old/fav"))
        db.set_favorite_note("old/fav", "旧备注", ["旧标签"])
        payload = user_data.export_user_data(db, store=store)
        item = payload["items"][0]
        assert item["favorite"] is True
        assert item["enabled"] is False      # 旧收藏不自动开启周期采样
        assert item["note"] == "旧备注"
        assert item["tags"]["user"] == ["旧标签"]

    def test_removed_state_exported(self, db, store):
        tracking.add_tracking(db, "owner/repo", store=store)
        tracking.remove_tracking(db, "owner/repo", store=store)
        item = user_data.export_user_data(db, store=store)["items"][0]
        assert item["removed"] is True and item["enabled"] is False


class TestImport:
    def test_roundtrip_into_new_db(self, db, store):
        _seed(db, store)
        payload = user_data.export_user_data(
            db, include_history=True, store=store, now=NOW)

        db2 = Database(str(db.path) + ".copy")
        try:
            store2 = Store(db2)
            stats = user_data.import_user_data(db2, payload, store=store2)
            assert stats["added"] == 1 and stats["errors"] == 0
            repo = store2.repository("owner/repo")
            row = store2.tracking(repo["id"])
            assert row["note"] == "用户备注"
            assert row["favorite"] == 1
            assert row["next_due_at"].startswith("2026-11-15T14:00")
            assert tracking.tags(db2, "owner/repo", store=store2)["user"] == ["引擎"]
            assert store2.star_samples(repo["id"]) == [
                (NOW, 150)]     # history 采样随导出恢复
        finally:
            db2.close()

    def test_merge_keeps_note_and_pause(self, db, store):
        rid = tracking.add_tracking(db, "owner/repo", note="用户备注", store=store)
        tracking.set_paused(db, "owner/repo", True, store=store)
        store.set_tags(rid, ["a"], "user")
        payload = {
            "schema_version": 1,
            "items": [{
                "name": "owner/repo", "favorite": True, "enabled": True,
                "paused": False, "note": "导入备注",
                "tags": {"user": ["b"], "github_topic": []},
            }],
        }
        stats = user_data.import_user_data(db, payload, store=store)
        assert stats["updated"] == 1
        assert stats["conflicts"] and stats["conflicts"][0]["field"] == "note"
        row = store.tracking(rid)
        assert row["note"] == "用户备注"          # 不覆盖用户备注
        assert row["paused"] == 1 and row["favorite"] == 1
        assert [t["tag"] for t in store.tags(rid)] == ["a", "b"]

    def test_skip_and_replace(self, db, store):
        rid = tracking.add_tracking(db, "owner/repo", note="原备注", store=store)
        payload = {
            "schema_version": 1,
            "items": [{"name": "owner/repo", "note": "新备注",
                       "tags": {"user": ["新"]}}],
        }
        stats = user_data.import_user_data(db, payload, mode="skip", store=store)
        assert stats["skipped"] == 1
        assert store.tracking(rid)["note"] == "原备注"

        stats = user_data.import_user_data(db, payload, mode="replace", store=store)
        assert stats["updated"] == 1
        assert store.tracking(rid)["note"] == "新备注"
        assert [t["tag"] for t in store.tags(rid)] == ["新"]

    def test_duplicate_import_is_idempotent(self, db, store):
        payload = {
            "schema_version": 1,
            "items": [{"name": "owner/repo", "note": "备注"}],
        }
        first = user_data.import_user_data(db, payload, store=store)
        second = user_data.import_user_data(db, payload, store=store)
        assert first["added"] == 1
        assert second["added"] == 0 and second["unchanged"] == 1
        assert len(store.list_tracking()) == 1

    def test_invalid_version_rejected(self, db, store):
        with pytest.raises(user_data.UserDataError):
            user_data.import_user_data(db, {"schema_version": 99, "items": []},
                                       store=store)

    def test_bad_item_does_not_abort_batch(self, db, store):
        payload = {
            "schema_version": 1,
            "items": [
                {"name": "not-a-repo"},
                {"name": "good/repo", "note": "ok"},
            ],
        }
        stats = user_data.import_user_data(db, payload, store=store)
        assert stats["added"] == 1 and stats["errors"] == 1
        assert store.repository("good/repo") is not None


class TestFile:
    def test_atomic_write_and_load(self, tmp_path, db, store):
        payload = user_data.export_user_data(db, store=store)
        path = tmp_path / "out" / "user.json"
        user_data.write_json(str(path), payload)
        assert user_data.load_json(str(path)) == payload
        assert not (tmp_path / "out" / "user.json.tmp").exists()

    def test_import_removed_roundtrip(self, db, store):
        tracking.add_tracking(db, "owner/repo", store=store)
        tracking.remove_tracking(db, "owner/repo", store=store)
        payload = user_data.export_user_data(db, store=store)

        db2 = Database(str(db.path) + ".removed")
        try:
            store2 = Store(db2)
            user_data.import_user_data(db2, payload, store=store2)
            row = store2.tracking(store2.repository("owner/repo")["id"])
            assert row["removed"] == 1 and row["enabled"] == 0
            assert tracking.due_tracking(db2, NOW) == []
        finally:
            db2.close()
