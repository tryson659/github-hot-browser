# -*- coding: utf-8 -*-
"""R12：数据目录默认写入用户可写位置，支持显式便携模式，导入无副作用。"""
import os

from app.core import config


def test_import_does_not_create_data_dir(tmp_path, monkeypatch):
    # 导入已完成；这里只验证解析函数本身不创建目录
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    target = config.resolve_data_dir(str(app_dir), env={
        "LOCALAPPDATA": str(tmp_path / "local")})
    assert not os.path.exists(target)


def test_default_user_dir(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    local = tmp_path / "local"
    target = config.resolve_data_dir(str(app_dir), env={
        "LOCALAPPDATA": str(local)})
    assert target == str(local / config.APP_NAME / "data")


def test_portable_flag_uses_program_dir(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "portable.flag").write_text("", encoding="utf-8")
    target = config.resolve_data_dir(str(app_dir), env={
        "LOCALAPPDATA": str(tmp_path / "local")})
    assert target == str(app_dir / "data")


def test_env_override_wins(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    override = tmp_path / "custom"
    target = config.resolve_data_dir(str(app_dir), env={
        "GHB_DATA_DIR": str(override),
        "GHB_PORTABLE": "1",
        "LOCALAPPDATA": str(tmp_path / "local")})
    assert target == str(override)


def test_legacy_layout_keeps_working(tmp_path):
    app_dir = tmp_path / "app"
    (app_dir / "data").mkdir(parents=True)
    (app_dir / "data" / "browser.db").write_bytes(b"")
    target = config.resolve_data_dir(str(app_dir), env={
        "LOCALAPPDATA": str(tmp_path / "local")})
    assert target == str(app_dir / "data")


def test_legacy_ignored_when_user_db_exists(tmp_path):
    app_dir = tmp_path / "app"
    (app_dir / "data").mkdir(parents=True)
    (app_dir / "data" / "browser.db").write_bytes(b"")
    local = tmp_path / "local"
    user_db = local / config.APP_NAME / "data" / "browser.db"
    user_db.parent.mkdir(parents=True)
    user_db.write_bytes(b"")
    target = config.resolve_data_dir(str(app_dir), env={
        "LOCALAPPDATA": str(local)})
    assert target == str(user_db.parent)


def test_ensure_data_dirs_creates_subdirs(tmp_path):
    target = config.ensure_data_dirs(str(tmp_path / "newdata"))
    assert os.path.isdir(os.path.join(target, "crash_logs"))
    assert os.path.isdir(os.path.join(target, "backups"))
