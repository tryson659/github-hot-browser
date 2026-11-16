# -*- coding: utf-8 -*-
"""数据管理：创建一致性备份、校验备份、登记下次启动恢复。"""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from app import __version__
from app.core import config
from app.core.db import Database
from app.services import backup


class DataManagementDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._verified: str | None = None
        self.setWindowTitle("数据管理 · 本地备份与恢复")
        self.setMinimumWidth(620)
        self.setMinimumHeight(460)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "备份包包含一致性数据库与 manifest；自动去除 Token/API Key，但不加密。\n"
            "恢复会在下次启动前执行，并先生成恢复点；恢复后需重新配置凭证。"))

        lay.addWidget(QLabel("创建备份"))
        row = QHBoxLayout()
        self.backup_edit = QLineEdit(self._default_path())
        row.addWidget(self.backup_edit, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_save)
        row.addWidget(browse)
        make = QPushButton("创建")
        make.clicked.connect(self._create)
        row.addWidget(make)
        lay.addLayout(row)

        lay.addWidget(QLabel("校验 / 安排恢复"))
        row2 = QHBoxLayout()
        self.restore_edit = QLineEdit()
        self.restore_edit.setPlaceholderText("选择 .zip 备份包")
        row2.addWidget(self.restore_edit, 1)
        browse2 = QPushButton("浏览…")
        browse2.clicked.connect(self._browse_open)
        row2.addWidget(browse2)
        check = QPushButton("校验")
        check.clicked.connect(self._inspect)
        row2.addWidget(check)
        self.schedule_btn = QPushButton("安排恢复")
        self.schedule_btn.setEnabled(False)
        self.schedule_btn.clicked.connect(self._schedule)
        row2.addWidget(self.schedule_btn)
        lay.addLayout(row2)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("操作结果显示在这里")
        lay.addWidget(self.output, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.button(QDialogButtonBox.Close).setText("关闭")
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _default_path(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(config.BACKUP_DIR, f"ghb_{stamp}.zip")

    def _browse_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存备份", self.backup_edit.text(), "ZIP (*.zip)")
        if path:
            self.backup_edit.setText(path)

    def _browse_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份", self.restore_edit.text(), "ZIP (*.zip)")
        if path:
            self.restore_edit.setText(path)
            self.schedule_btn.setEnabled(False)
            self._verified = None

    def _create(self):
        path = self.backup_edit.text().strip()
        if not path:
            return
        try:
            manifest = backup.create_backup(
                self.db, path, app_version=__version__)
        except Exception as e:
            QMessageBox.warning(self, "备份失败", str(e))
            self.output.setPlainText(f"备份失败：{e}")
            return
        self.output.setPlainText(
            f"备份完成：{os.path.abspath(path)}\n"
            f"格式 {manifest['backup_format_version']} · schema "
            f"{manifest['db_schema_version']} · {manifest['app_version']}\n"
            f"计数：{manifest['counts']}\n"
            f"SHA-256：{manifest['files'][0]['sha256']}")

    def _inspect(self):
        path = self.restore_edit.text().strip()
        if not path:
            return
        try:
            info = backup.inspect_backup(path)
        except Exception as e:
            self._verified = None
            self.schedule_btn.setEnabled(False)
            self.output.setPlainText(f"校验失败：{e}")
            return
        self._verified = os.path.abspath(path)
        self.schedule_btn.setEnabled(True)
        m = info["manifest"]
        self.output.setPlainText(
            "校验通过。\n"
            f"文件：{info['path']}\n"
            f"格式 {m['backup_format_version']} · schema {m['db_schema_version']}"
            f" · 创建 {m.get('created_at', '?')} · 应用 {m.get('app_version', '?')}\n"
            f"计数：{info['counts']}\n"
            "尚未修改当前数据库。")

    def _schedule(self):
        if not self._verified:
            return
        ans = QMessageBox.question(
            self, "安排恢复",
            "恢复会在下次启动、打开数据库之前执行。\n"
            "当前数据库会先生成恢复点；如果恢复失败会回滚。\n"
            "请关闭应用后重新启动以完成恢复。\n\n继续登记吗？")
        if ans != QMessageBox.Yes:
            return
        try:
            marker = backup.write_pending_restore(
                config.DATA_DIR, self._verified)
        except Exception as e:
            QMessageBox.warning(self, "登记失败", str(e))
            return
        self.output.setPlainText(
            f"已登记恢复：{self._verified}\n标记文件：{marker}\n"
            "现在可以关闭应用；下次启动会自动恢复并生成恢复点。")
        QMessageBox.information(
            self, "已登记",
            "恢复已登记。请关闭应用并重新启动；启动时会先恢复备份，"
            "再打开主窗口。")
