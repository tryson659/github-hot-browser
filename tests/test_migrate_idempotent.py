# -*- coding: utf-8 -*-
"""R06：迁移可重复执行、错误可见、不静默覆盖用户修改。"""
import json

import pytest

from app.core.db import Database
from app.core.models import Repo
from app.services.migrate import migrate


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_history_not_duplicated_on_rerun(db, tmp_path):
    old = tmp_path / "old"
    (old / "history").mkdir(parents=True)
    _write(old / "history" / "7d_Python_20261101_120000.json",
           {"repos": [{"name": "a/b", "url": "https://github.com/a/b",
                       "stars": 100}]})
    first = migrate(str(old), db, log=lambda *_: None)
    second = migrate(str(old), db, log=lambda *_: None)
    rows = db._read(lambda c: c.execute("SELECT count(*) FROM history").fetchone()[0])
    assert rows == 1
    assert first["history"] == 1
    assert second["history"] == 0


def test_user_note_not_overwritten(db, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    _write(old / "favorites.json",
           {"a/b": {"name": "a/b", "url": "https://github.com/a/b", "stars": 1}})
    _write(old / "fav_meta.json", {"a/b": {"note": "旧备注", "tags": ["旧"]}})
    migrate(str(old), db, log=lambda *_: None)
    db.set_favorite_note("a/b", "迁移后新备注", ["新"])

    stats = migrate(str(old), db, log=lambda *_: None)
    meta = db.get_fav_meta("a/b")
    assert meta["note"] == "迁移后新备注"
    assert meta["tags"] == ["新"]
    assert stats["fav_meta_skipped"] == 1


def test_settings_not_overwritten_after_user_edit(db, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    _write(old / "settings.json", {"languages": ["Rust"], "pool_size": 80})
    migrate(str(old), db, log=lambda *_: None)
    db.set_setting("languages", ["Go"])

    stats = migrate(str(old), db, log=lambda *_: None)
    assert db.get_setting("languages") == ["Go"]
    assert db.get_setting("pool_size") == 80
    assert stats["settings_skipped"] >= 1


def test_corrupt_json_reported_but_other_files_migrated(db, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "settings.json").write_text("{broken", encoding="utf-8")
    _write(old / "favorites.json",
           {"a/b": {"name": "a/b", "url": "https://github.com/a/b", "stars": 2}})
    stats = migrate(str(old), db, log=lambda *_: None)
    assert stats["errors"] == 1
    assert stats["error_details"]
    assert db.is_favorite("a/b")          # 坏文件不阻断其余迁移


def test_utf8_bom_json_is_accepted(db, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "settings.json").write_text(
        json.dumps({"languages": ["Rust"]}), encoding="utf-8-sig")
    stats = migrate(str(old), db, log=lambda *_: None)
    assert stats["errors"] == 0
    assert db.get_setting("languages") == ["Rust"]


def test_missing_directory_is_error(db, tmp_path):
    stats = migrate(str(tmp_path / "nope"), db, log=lambda *_: None)
    assert stats["errors"] == 1
    assert stats["favorites"] == 0


def test_legacy_duplicate_history_is_deduped_on_open(tmp_path):
    """旧库存在重复历史时，启动先保留最早一条再建唯一索引。"""
    import sqlite3

    path = str(tmp_path / "t.db")
    first = Database(path)
    first.save_history("C++", 7,
                       [Repo(name="a/b", url="https://github.com/a/b")],
                       taken_at="2026-11-01 00:00:00")
    first.close()

    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP INDEX uq_history")
        raw.execute(
            "INSERT INTO history(channel, days, taken_at, repos)"
            " SELECT channel, days, taken_at, repos FROM history")
        raw.commit()
        assert raw.execute("SELECT count(*) FROM history").fetchone()[0] == 2
    finally:
        raw.close()

    db = Database(path)
    try:
        rows = db._read(lambda c: c.execute(
            "SELECT count(*) FROM history").fetchone()[0])
        assert rows == 1
        # 索引已恢复：再次重复插入被唯一约束阻止
        db.save_history("C++", 7, [], taken_at="2026-11-01 00:00:00",
                        overwrite=False)
        rows = db._read(lambda c: c.execute(
            "SELECT count(*) FROM history").fetchone()[0])
        assert rows == 1
    finally:
        db.close()
