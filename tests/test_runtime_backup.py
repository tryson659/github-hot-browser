# -*- coding: utf-8 -*-
"""R01/R05：启动备份一致性与退出标记语义。"""
import os
import sqlite3

import pytest

from app import runtime
from app.core.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def test_backup_includes_open_wal_commits(db, tmp_path):
    """保持连接打开且有已提交数据时，备份必须包含这些数据且完整。"""
    db.set_setting("review_marker", "committed")
    dest = tmp_path / "backup" / "browser.db.bak"
    db.backup_to(str(dest))

    copy = sqlite3.connect(str(dest))
    try:
        row = copy.execute(
            "SELECT value FROM settings WHERE key='review_marker'").fetchone()
        assert row is not None and row[0] == '"committed"'
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        copy.close()


def test_backup_replaces_atomically_and_keeps_old_on_failure(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "t.db"))
    try:
        db.set_setting("v", 1)
        backup = runtime.backup_database(db, base=str(tmp_path))
        assert os.path.exists(backup)

        class Boom:
            def backup_to(self, path):
                raise RuntimeError("磁盘写满")

        with pytest.raises(RuntimeError):
            runtime.backup_database(Boom(), base=str(tmp_path))
        # 失败不破坏上一份有效副本，也不留 .tmp
        assert os.path.exists(backup)
        assert not os.path.exists(backup + ".tmp")
        assert os.path.getsize(backup) > 0
    finally:
        db.close()


def test_backup_marks_failure_for_broken_destination(db, tmp_path):
    with pytest.raises(runtime.BackupError):
        # 临时备份路径被同名目录占用 → 无法生成临时库
        bad_dir = tmp_path / "bad"
        (bad_dir / "backups" / "browser.db.bak.tmp").mkdir(parents=True)
        runtime.backup_database(db, base=str(bad_dir))


def test_daily_backup_rotation_keeps_seven(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        runtime.backup_database(db, base=str(tmp_path))
        backups = tmp_path / "backups"
        daily = list(backups.glob("browser-*.db.bak"))
        assert len(daily) == 1
        for i in range(10):
            (backups / f"browser-202001{i:02d}.db.bak").write_bytes(b"old")
        runtime.backup_database(db, base=str(tmp_path))
        assert len(list(backups.glob("browser-*.db.bak"))) <= 7
    finally:
        db.close()


def test_unclean_exit_flag_semantics(tmp_path):
    base = str(tmp_path / "runtime")
    assert runtime.is_unclean_exit(base) is False      # 初次启动不告警
    runtime.mark_running(base)
    assert runtime.is_unclean_exit(base) is True       # 崩溃后标记仍在
    runtime.mark_clean_exit(base)
    assert runtime.is_unclean_exit(base) is False      # 正常退出删标记
    runtime.mark_clean_exit(base)                      # 重复删除不报错


def test_runtime_paths_follow_data_dir(tmp_path):
    base = str(tmp_path)
    assert runtime.lock_path(base).endswith("app.lock")
    assert runtime.clean_flag_path(base).endswith(".clean_exit")
    assert runtime.crash_dir(base).endswith("crash_logs")
    assert runtime.backup_dir(base).endswith("backups")
