# -*- coding: utf-8 -*-
"""应用入口（含运行时加固）。

    python -m app.main                  启动 GUI
    python -m app.main --migrate DIR    先把旧版 program/ 目录的 JSON 数据迁入 SQLite

加固顺序（R01/R05/R06）：
1. 单实例锁：GUI 与 --migrate 共用同一把锁，重复启动弹窗拒绝；
2. 退出标记：取得锁后判断上次是否异常退出，再写 running 标记；
3. 一致性备份：锁内用 SQLite 在线备份接口导出并校验，原子替换旧备份；
4. 崩溃日志：未捕获异常写 data/crash_logs/crash_时间戳.log。
"""
from __future__ import annotations

import os
import sys

from app import runtime
from app.core import config
from app.core.db import Database

USAGE = "用法：python -m app.main [--migrate <旧program目录>]"


def _run_migrate(target: str) -> int:
    from app.services.migrate import run_cli
    return run_cli(target)


def main() -> int:
    args = sys.argv[1:]

    if "--migrate" in args:
        idx = args.index("--migrate")
        if idx + 1 >= len(args):
            print(USAGE)
            return 2
        return _run_migrate(args[idx + 1])

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    config.ensure_data_dirs()
    runtime.install_crash_hook()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("GitHub 热点项目浏览器")

    # 锁 → 上次异常退出判断 → 写运行标记（顺序不可交换）
    lock, unclean = runtime.begin_session()
    if lock is None:
        QMessageBox.warning(None, "已在运行", "程序已在运行中，请勿重复启动。")
        return 1

    # 数据管理登记的恢复：必须在打开数据库之前、持有锁时执行
    restored_msg = ""
    pending = None
    try:
        from app.services import backup as backup_svc
        pending = backup_svc.read_pending_restore(config.DATA_DIR)
        if pending:
            try:
                result = backup_svc.restore_backup(pending, config.DB_PATH)
                backup_svc.clear_pending_restore(config.DATA_DIR)
                restored_msg = (
                    f"已从备份恢复：{os.path.basename(pending)}\n"
                    f"恢复前备份：{result['pre_restore']}\n"
                    "凭证未包含在备份中，如需要请重新配置。")
            except Exception as e:
                backup_svc.clear_pending_restore(config.DATA_DIR)
                runtime.mark_clean_exit()
                lock.unlock()
                QMessageBox.critical(
                    None, "恢复失败",
                    f"启动前恢复失败，已保留当前数据：\n{e}\n"
                    "可重新在「💾 数据管理」中校验备份后再试。")
                return 1
    except Exception:
        # marker 读取异常不应阻止正常启动
        pass

    db = Database()
    try:
        backup_error = ""
        try:
            runtime.backup_database(db)
        except Exception as e:      # 备份失败必须可见，但不阻塞只读浏览
            backup_error = str(e)
            print(f"启动备份失败：{e}", file=sys.stderr)

        from app.ui.main_window import MainWindow
        win = MainWindow(db)
        win.show()
        if unclean:
            QMessageBox.warning(
                win, "异常退出提醒",
                "检测到上次未正常退出。\n"
                f"崩溃日志：{config.CRASH_DIR}\n数据备份：{config.BACKUP_DIR}")
        if backup_error:
            QMessageBox.warning(
                win, "启动备份失败",
                "本次启动未能生成一致性备份，已保留上一份有效备份。\n"
                f"原因：{backup_error}\n备份目录：{config.BACKUP_DIR}")
        if restored_msg:
            QMessageBox.information(win, "恢复完成", restored_msg)
        try:
            code = app.exec()
        except BaseException:
            # 异常退出保留 running 标记，下次启动提示"上次未正常退出"
            raise
        else:
            runtime.mark_clean_exit()
            return code
    finally:
        db.close()
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
