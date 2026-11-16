# -*- coding: utf-8 -*-
"""本地一致性备份与整库恢复（阶段 B）。

备份包是自包含 ZIP：
    manifest.json    格式/schema/版本/文件大小/SHA-256/业务计数/脱敏声明
    database.sqlite  SQLite 在线备份 + 去除凭证 + VACUUM 重建

恢复流程：只读预检 → 校验哈希/完整性/版本 → 生成恢复前备份 → 关闭连接后
临时文件替换 → 失败回滚。调用方必须先停止任务并关闭数据库连接。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app import __version__
from app.core.config import DB_PATH
from app.core.db import SCHEMA_VERSION, Database

BACKUP_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DB_NAME = "database.sqlite"
_ALLOWED = {MANIFEST_NAME, DB_NAME}
_MAX_UNCOMPRESSED = 1024 * 1024 * 1024          # 1 GiB
_MAX_MANIFEST = 64 * 1024
_MAX_RATIO = 500
SENSITIVE_SETTING_KEYS = ("github_token", "translate_apikey", "translate_email")
PENDING_RESTORE_NAME = "pending_restore.json"


def pending_restore_file(data_dir: str) -> str:
    return os.path.join(data_dir, PENDING_RESTORE_NAME)


def write_pending_restore(data_dir: str, backup_path: str) -> str:
    """登记“下次启动前恢复”；实际替换只在应用关闭、无连接时发生。"""
    os.makedirs(data_dir, exist_ok=True)
    target = pending_restore_file(data_dir)
    tmp = target + ".tmp"
    payload = {"backup": os.path.abspath(backup_path),
               "requested_at": datetime.now(timezone.utc).strftime(
                   "%Y-%m-%dT%H:%M:%SZ")}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)
    return target


def read_pending_restore(data_dir: str) -> str | None:
    try:
        with open(pending_restore_file(data_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    path = data.get("backup") if isinstance(data, dict) else None
    return path if isinstance(path, str) and path else None


def clear_pending_restore(data_dir: str):
    try:
        os.remove(pending_restore_file(data_dir))
    except FileNotFoundError:
        pass


class BackupError(RuntimeError):
    pass


class RestoreError(RuntimeError):
    pass


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_hash(zf: zipfile.ZipFile, name: str) -> str:
    h = hashlib.sha256()
    with zf.open(name) as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ro_uri(path: str) -> str:
    return Path(path).resolve().as_uri() + "?mode=ro"


def _db_counts(path: str) -> dict:
    conn = sqlite3.connect(_ro_uri(path), uri=True)
    try:
        def has_table(table: str) -> bool:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone() is not None

        def count(table: str, where: str = "") -> int:
            if not has_table(table):
                return 0
            sql = f"SELECT count(*) FROM {table}"
            if where:
                sql += f" WHERE {where}"
            return int(conn.execute(sql).fetchone()[0])

        try:
            schema = json.loads(conn.execute(
                "SELECT value FROM settings WHERE key='__schema_version__'"
            ).fetchone()[0])
        except (TypeError, ValueError, sqlite3.Error, json.JSONDecodeError):
            schema = 0
        return {
            "schema_version": int(schema or 0),
            "categories": count("categories", "deleted=0"),
            "library_entries": count("library_entries"),
            "favorites": count("favorites"),
            "tracking": count("tracking", "removed=0"),
            "star_samples": count("star_samples"),
        }
    finally:
        conn.close()


def _sanitize_db(path: str, secret_values: list[str]):
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA secure_delete=ON")
        conn.executemany("DELETE FROM settings WHERE key=?",
                         [(k,) for k in SENSITIVE_SETTING_KEYS])
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    data = Path(path).read_bytes()
    for value in secret_values:
        if value and value.encode("utf-8") in data:
            raise BackupError("脱敏后仍能在备份库中找到凭证，已中止")


def create_backup(db: Database, path: str, *, app_version: str,
                  now: datetime | None = None) -> dict:
    """生成一致性 ZIP；失败不覆盖上一份有效包。"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    now = now or datetime.now(timezone.utc)
    secret_values = []
    for key in SENSITIVE_SETTING_KEYS:
        value = db.get_setting(key)
        if isinstance(value, str) and value:
            secret_values.append(value)

    with tempfile.TemporaryDirectory(prefix="ghb_backup_") as td:
        raw_db = os.path.join(td, DB_NAME)
        db.backup_to(raw_db)
        _sanitize_db(raw_db, secret_values)
        counts = _db_counts(raw_db)
        db_size = os.path.getsize(raw_db)
        db_hash = _sha256(raw_db)
        manifest = {
            "backup_format_version": BACKUP_FORMAT_VERSION,
            "db_schema_version": counts["schema_version"],
            "app_version": app_version,
            "created_at": now.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "excluded_secrets": list(SENSITIVE_SETTING_KEYS),
            "counts": {k: v for k, v in counts.items()
                       if k != "schema_version"},
            "files": [{"name": DB_NAME, "size": db_size, "sha256": db_hash}],
        }
        tmp_zip = path + ".tmp"
        try:
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(MANIFEST_NAME, json.dumps(
                    manifest, ensure_ascii=False, indent=2))
                zf.write(raw_db, DB_NAME)
            os.replace(tmp_zip, path)
        finally:
            if os.path.exists(tmp_zip):
                try:
                    os.remove(tmp_zip)
                except OSError:
                    pass
    return manifest


def _validate_members(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    names = [i.filename for i in infos]
    if len(names) != len(set(names)):
        raise RestoreError("备份包含重复文件")
    if set(names) != _ALLOWED:
        raise RestoreError(f"备份文件清单不符合格式：{sorted(names)}")
    total = 0
    compressed = 0
    for info in infos:
        name = info.filename
        pure = Path(name)
        if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1:
            raise RestoreError(f"非法文件路径：{name}")
        if info.is_dir() or info.flag_bits & 0x1:
            raise RestoreError(f"不支持目录或加密条目：{name}")
        mode = info.external_attr >> 16
        if mode & 0o170000 == 0o120000:
            raise RestoreError(f"不允许符号链接：{name}")
        total += info.file_size
        compressed += info.compress_size
    if total > _MAX_UNCOMPRESSED:
        raise RestoreError("备份解压后体积超过限制")
    if compressed and total / max(compressed, 1) > _MAX_RATIO and total > 10 * 1024 * 1024:
        raise RestoreError("备份压缩比异常")


def inspect_backup(path: str) -> dict:
    """只读预检：格式、版本、大小、哈希、SQLite 完整性与业务计数。"""
    if not os.path.isfile(path):
        raise RestoreError("备份文件不存在")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise RestoreError("不是有效 ZIP 备份") from e
    with zf:
        _validate_members(zf)
        manifest_info = zf.getinfo(MANIFEST_NAME)
        if manifest_info.file_size > _MAX_MANIFEST:
            raise RestoreError("manifest 过大")
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise RestoreError("manifest 无法解析") from e
        if manifest.get("backup_format_version") != BACKUP_FORMAT_VERSION:
            raise RestoreError("不支持的备份格式版本")
        schema = manifest.get("db_schema_version")
        if not isinstance(schema, int) or schema <= 0:
            raise RestoreError("manifest 缺少有效 schema 版本")
        if schema > SCHEMA_VERSION:
            raise RestoreError(
                f"备份 schema {schema} 高于当前支持 {SCHEMA_VERSION}")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) != 1:
            raise RestoreError("manifest 文件清单非法")
        entry = files[0]
        if entry.get("name") != DB_NAME:
            raise RestoreError("manifest 未引用 database.sqlite")
        info = zf.getinfo(DB_NAME)
        if int(entry.get("size", -1)) != info.file_size:
            raise RestoreError("manifest 文件大小不匹配")
        if entry.get("sha256") != _zip_hash(zf, DB_NAME):
            raise RestoreError("备份文件 SHA-256 校验失败")

        with tempfile.TemporaryDirectory(prefix="ghb_inspect_") as td:
            tmp_db = os.path.join(td, DB_NAME)
            with zf.open(DB_NAME) as src, open(tmp_db, "wb") as dst:
                shutil.copyfileobj(src, dst)
            conn = sqlite3.connect(_ro_uri(tmp_db), uri=True)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RestoreError("SQLite 完整性检查失败")
                counts = _db_counts(tmp_db)
            finally:
                conn.close()
        if counts["schema_version"] != schema:
            raise RestoreError("manifest 与数据库 schema 版本不一致")
        return {"path": os.path.abspath(path), "manifest": manifest,
                "counts": counts}


def restore_backup(path: str, db_path: str, *,
                   pre_restore_dir: str | None = None) -> dict:
    """整库恢复；调用方必须先停止任务并关闭所有数据库连接。

    失败时尽量恢复原库，且保留恢复前备份。
    """
    info = inspect_backup(path)
    target = os.path.abspath(db_path)
    target_dir = os.path.dirname(target) or "."
    os.makedirs(target_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pre_dir = pre_restore_dir or os.path.join(target_dir, "backups")
    os.makedirs(pre_dir, exist_ok=True)
    pre_restore = os.path.join(pre_dir, f"pre_restore_{stamp}.sqlite")

    if os.path.exists(target):
        old = Database(target)
        try:
            old.backup_to(pre_restore)
        finally:
            old.close()

    tmp_new = os.path.join(target_dir, f".restore_{stamp}.sqlite")
    old_copy = os.path.join(target_dir, f".old_{stamp}.sqlite")
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open(DB_NAME) as src, open(tmp_new, "wb") as dst:
                shutil.copyfileobj(src, dst)
        if _sha256(tmp_new) != info["manifest"]["files"][0]["sha256"]:
            raise RestoreError("恢复副本哈希不一致")
        conn = sqlite3.connect(_ro_uri(tmp_new), uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("恢复副本完整性检查失败")
        finally:
            conn.close()

        for suffix in ("-wal", "-shm"):
            try:
                os.remove(target + suffix)
            except FileNotFoundError:
                pass
        if os.path.exists(target):
            os.replace(target, old_copy)
        os.replace(tmp_new, target)
        try:
            check = Database(target)
            try:
                if check.integrity_check() != "ok":
                    raise RestoreError("恢复后数据库完整性检查失败")
            finally:
                check.close()
        except Exception:
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
            for suffix in ("-wal", "-shm"):
                try:
                    os.remove(target + suffix)
                except FileNotFoundError:
                    pass
            if os.path.exists(old_copy):
                os.replace(old_copy, target)
            raise
        if os.path.exists(old_copy):
            os.remove(old_copy)
        return {"restored": target, "pre_restore": pre_restore,
                "manifest": info["manifest"]}
    except Exception:
        if os.path.exists(tmp_new):
            try:
                os.remove(tmp_new)
            except OSError:
                pass
        if not os.path.exists(target) and os.path.exists(old_copy):
            os.replace(old_copy, target)
        raise


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="本地一致性备份/恢复（需先关闭应用）")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("path")
    create.add_argument("--db", default=DB_PATH)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("path")
    restore = sub.add_parser("restore")
    restore.add_argument("path")
    restore.add_argument("--db", default=DB_PATH)
    restore.add_argument("--yes", action="store_true",
                         help="确认替换当前数据库")
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            db = Database(args.db)
            try:
                manifest = create_backup(db, args.path,
                                         app_version=__version__)
            finally:
                db.close()
            print(f"备份完成：{args.path}；条目 {manifest['counts']}")
            return 0
        if args.command == "inspect":
            info = inspect_backup(args.path)
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        if args.command == "restore":
            if not args.yes:
                print("恢复会替换当前数据库；确认后加 --yes（应用须已关闭）",
                      file=sys.stderr)
                return 2
            result = restore_backup(args.path, args.db)
            print(f"恢复完成：{result['restored']}；恢复前备份："
                  f"{result['pre_restore']}")
            return 0
        return 2
    except Exception as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
