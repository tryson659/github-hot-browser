# -*- coding: utf-8 -*-
"""运行时加固（R01/R05）：单实例锁、崩溃日志、一致性启动备份、干净退出标记。

设计要点：
- GUI 与 `--migrate` 入口共用同一把锁和同一套备份流程；
- 备份先取得单实例锁，再用 SQLite 在线备份接口导出临时文件，校验完整性后
  原子替换，失败时保留上一份有效副本并向上抛错（不静默吞掉）；
- `.clean_exit` 语义：进程启动时写入 = "正在运行"，正常退出时删除；
  启动时若标记存在，说明上次没有走到正常退出（崩溃/被杀）。
- 本模块导入时不依赖 Qt；`acquire_lock` 内部才导入 QLockFile，
  便于无界面环境（迁移/测试）复用。
"""
from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import sys
import traceback
from datetime import datetime

from app.core import config


class BackupError(RuntimeError):
    """启动备份失败（完整性校验不通过或写入失败）。"""


def data_dir() -> str:
    return config.DATA_DIR


def crash_dir(base: str | None = None) -> str:
    return os.path.join(base or config.DATA_DIR, "crash_logs")


def backup_dir(base: str | None = None) -> str:
    return os.path.join(base or config.DATA_DIR, "backups")


def lock_path(base: str | None = None) -> str:
    return os.path.join(base or config.DATA_DIR, "app.lock")


def clean_flag_path(base: str | None = None) -> str:
    return os.path.join(base or config.DATA_DIR, ".clean_exit")


def install_crash_hook(base: str | None = None) -> str:
    """未捕获异常写入 crash_logs；返回日志目录。"""
    directory = crash_dir(base)
    os.makedirs(directory, exist_ok=True)

    def hook(exc_type, exc_value, tb):
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(directory, f"crash_{stamp}.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"时间: {datetime.now().isoformat(timespec='seconds')}\n")
                f.write("".join(traceback.format_exception(
                    exc_type, exc_value, tb)))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, tb)

    sys.excepthook = hook
    return directory


def acquire_lock(base: str | None = None):
    """尝试取得单实例锁；已有实例运行时返回 None。"""
    from PySide6.QtCore import QLockFile

    os.makedirs(base or config.DATA_DIR, exist_ok=True)
    lock = QLockFile(lock_path(base))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(100):
        return None
    return lock


def is_unclean_exit(base: str | None = None) -> bool:
    """上次是否未正常退出（running 标记仍在）。"""
    return os.path.exists(clean_flag_path(base))


def mark_running(base: str | None = None):
    """写入"正在运行"标记；须在取得单实例锁之后调用。"""
    os.makedirs(base or config.DATA_DIR, exist_ok=True)
    with open(clean_flag_path(base), "w", encoding="utf-8") as f:
        f.write("running")


def begin_session(base: str | None = None):
    """锁 + 判断上次是否异常退出 + 写运行标记。返回 (lock, unclean)。"""
    os.makedirs(base or config.DATA_DIR, exist_ok=True)
    lock = acquire_lock(base)
    if lock is None:
        return None, False
    unclean = is_unclean_exit(base)
    mark_running(base)
    return lock, unclean


def mark_clean_exit(base: str | None = None):
    try:
        os.remove(clean_flag_path(base))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _rotate_daily(target: str, keep: int = 7) -> str:
    """每天保留一份启动备份，最多 keep 份；失败不影响主备份。"""
    directory = os.path.dirname(target)
    stamp = datetime.now().strftime("%Y%m%d")
    daily = os.path.join(directory, f"browser-{stamp}.db.bak")
    try:
        if not os.path.exists(daily):
            shutil.copy2(target, daily)
        old = sorted(glob.glob(os.path.join(directory, "browser-*.db.bak")),
                     reverse=True)
        for path in old[keep:]:
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError:
        pass
    return daily


def backup_database(db, base: str | None = None) -> str:
    """一致性备份：临时文件 + 完整性校验 + 原子替换，失败保留上一份。

    成功后额外生成每日副本（最多 7 份）；调用方必须已持有单实例锁。
    返回主备份文件路径。
    """
    directory = backup_dir(base)
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, "browser.db.bak")
    tmp = target + ".tmp"
    try:
        try:
            db.backup_to(tmp)
            check = sqlite3.connect(tmp)
            try:
                row = check.execute("PRAGMA integrity_check").fetchone()
            finally:
                check.close()
            if not row or row[0] != "ok":
                raise BackupError(
                    f"备份完整性检查失败：{row[0] if row else '无结果'}")
            os.replace(tmp, target)
            _rotate_daily(target)
            return target
        except BackupError:
            raise
        except Exception as e:
            raise BackupError(f"备份失败：{e}") from e
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
