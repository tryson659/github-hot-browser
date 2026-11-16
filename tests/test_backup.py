# -*- coding: utf-8 -*-
"""阶段 B：一致性备份包、预检、整库恢复与回退。"""
import json
import os
import zipfile
from datetime import datetime, timezone

import pytest

from app.core.db import Database
from app.services import backup


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "browser.db"))
    yield d
    d.close()


def _seed(db, store):
    store.seed_defaults()
    ppt = store.category_by_name("PPT / 演示工具")["id"]
    store.add_repo(_repo("a/one"), categories=[ppt])
    db.set_setting("github_token", "SECRET-TOKEN")
    db.set_setting("translate_apikey", "SECRET-KEY")
    db.add_favorite(_repo("a/one"))


def _repo(name="a/one"):
    from app.core.models import Repo
    return Repo(name=name, url=f"https://github.com/{name}", stars=42,
                description="demo", language="C++")


def _create(db, path):
    from app.storage.library import LibraryStore
    _seed(db, LibraryStore(db))
    manifest = backup.create_backup(db, str(path), app_version="2.4.0",
                                    now=datetime(2026, 11, 15,
                                                 tzinfo=timezone.utc))
    return manifest


def _write_zip(path, db_path, manifest, extra=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(backup.MANIFEST_NAME, json.dumps(manifest))
        zf.write(db_path, backup.DB_NAME)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)


class TestCreate:
    def test_manifest_counts_and_no_secrets(self, db, tmp_path):
        path = tmp_path / "b.zip"
        manifest = _create(db, path)
        assert manifest["db_schema_version"] == 5
        assert manifest["counts"]["library_entries"] == 1
        assert manifest["counts"]["favorites"] == 1
        assert "github_token" in manifest["excluded_secrets"]
        with zipfile.ZipFile(path) as zf:
            assert set(zf.namelist()) == {backup.MANIFEST_NAME, backup.DB_NAME}
            data = zf.read(backup.DB_NAME)
            assert b"SECRET-TOKEN" not in data
            assert b"SECRET-KEY" not in data
        # 源库仍保留凭证，仅备份脱敏
        assert db.get_setting("github_token") == "SECRET-TOKEN"

    def test_inspect_matches_manifest(self, db, tmp_path):
        path = tmp_path / "b.zip"
        manifest = _create(db, path)
        info = backup.inspect_backup(str(path))
        assert info["manifest"]["created_at"] == "2026-11-15T00:00:00Z"
        assert info["counts"]["categories"] == manifest["counts"]["categories"]


class TestReject:
    def _valid_zip(self, db, tmp_path):
        path = tmp_path / "b.zip"
        manifest = _create(db, path)
        return path, manifest

    def test_bad_zip(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        with pytest.raises(backup.RestoreError):
            backup.inspect_backup(str(bad))

    def test_newer_schema_rejected(self, db, tmp_path):
        path, manifest = self._valid_zip(db, tmp_path)
        with zipfile.ZipFile(path) as zf:
            db_bytes = zf.read(backup.DB_NAME)
        manifest["db_schema_version"] = 99
        bad = tmp_path / "newer.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr(backup.MANIFEST_NAME, json.dumps(manifest))
            zf.writestr(backup.DB_NAME, db_bytes)
        with pytest.raises(backup.RestoreError):
            backup.inspect_backup(str(bad))

    def test_hash_mismatch_rejected(self, db, tmp_path):
        path, manifest = self._valid_zip(db, tmp_path)
        manifest["files"][0]["sha256"] = "0" * 64
        bad = tmp_path / "badhash.zip"
        with zipfile.ZipFile(path) as zf:
            db_bytes = zf.read(backup.DB_NAME)
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr(backup.MANIFEST_NAME, json.dumps(manifest))
            zf.writestr(backup.DB_NAME, db_bytes)
        with pytest.raises(backup.RestoreError):
            backup.inspect_backup(str(bad))

    def test_path_traversal_rejected(self, db, tmp_path):
        path, manifest = self._valid_zip(db, tmp_path)
        with zipfile.ZipFile(path) as zf:
            db_bytes = zf.read(backup.DB_NAME)
        bad = tmp_path / "traversal.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr(backup.MANIFEST_NAME, json.dumps(manifest))
            zf.writestr(backup.DB_NAME, db_bytes)
            zf.writestr("../escape.txt", b"x")
        with pytest.raises(backup.RestoreError):
            backup.inspect_backup(str(bad))

    def test_missing_manifest_rejected(self, db, tmp_path):
        path = tmp_path / "b.zip"
        _create(db, path)
        bad = tmp_path / "nomanifest.zip"
        with zipfile.ZipFile(path) as zf:
            db_bytes = zf.read(backup.DB_NAME)
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr(backup.DB_NAME, db_bytes)
        with pytest.raises(backup.RestoreError):
            backup.inspect_backup(str(bad))


class TestRestore:
    def test_roundtrip_and_pre_restore(self, db, tmp_path):
        db_path = db.path
        db.close()
        db = Database(db_path)
        path = tmp_path / "b.zip"
        _create(db, path)
        db.set_setting("after", "added")          # 备份后修改，应被恢复覆盖
        db.close()

        result = backup.restore_backup(str(path), db_path)
        assert os.path.exists(result["pre_restore"])
        assert os.path.getsize(result["pre_restore"]) > 0

        db2 = Database(db_path)
        try:
            assert db2.get_setting("after") is None
            assert db2.is_favorite("a/one")
            from app.storage.library import LibraryStore
            rows = LibraryStore(db2).entries()
            assert [r["name"] for r in rows] == ["a/one"]
        finally:
            db2.close()

    def test_invalid_backup_keeps_current_db(self, db, tmp_path):
        db_path = db.path
        _create(db, tmp_path / "ok.zip")
        db.set_setting("keep", "value")
        db.close()
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"bad")
        with pytest.raises(backup.RestoreError):
            backup.restore_backup(str(bad), db_path)

        reopened = Database(db_path)
        try:
            assert reopened.get_setting("keep") == "value"
        finally:
            reopened.close()

    def test_restore_failure_rolls_back(self, db, tmp_path, monkeypatch):
        db_path = db.path
        path = tmp_path / "b.zip"
        _create(db, path)
        db.close()

        original_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 2 and str(dst).endswith("browser.db"):
                raise OSError("模拟替换失败")
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky_replace)
        with pytest.raises(OSError):
            backup.restore_backup(str(path), db_path)

        reopened = Database(db_path)
        try:
            assert reopened.is_favorite("a/one") or reopened.get_setting(
                "keep") is not None
        finally:
            reopened.close()
