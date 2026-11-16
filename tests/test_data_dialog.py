# -*- coding: utf-8 -*-
"""数据管理对话框：创建/校验备份、登记下次启动恢复。"""
import zipfile

import pytest
from PySide6.QtWidgets import QMessageBox

from app.core.db import Database
from app.services import backup
from app.ui.data_dialogs import DataManagementDialog


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "browser.db"))
    yield d
    d.close()


def test_create_inspect_and_schedule(tmp_path, db, monkeypatch):
    from app.ui import data_dialogs
    monkeypatch.setattr(data_dialogs.config, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    dlg = DataManagementDialog(db)
    target = tmp_path / "backup.zip"
    dlg.backup_edit.setText(str(target))
    dlg._create()
    assert zipfile.is_zipfile(target)
    assert "备份完成" in dlg.output.toPlainText()

    dlg.restore_edit.setText(str(target))
    dlg._inspect()
    assert dlg.schedule_btn.isEnabled()
    assert "校验通过" in dlg.output.toPlainText()

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **kw: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **kw: QMessageBox.Ok))
    dlg._schedule()
    assert backup.read_pending_restore(str(tmp_path / "data")) == str(target)
    backup.clear_pending_restore(str(tmp_path / "data"))
    dlg.close()


def test_invalid_backup_does_not_enable_restore(tmp_path, db):
    dlg = DataManagementDialog(db)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not zip")
    dlg.restore_edit.setText(str(bad))
    dlg._inspect()
    assert not dlg.schedule_btn.isEnabled()
    assert "校验失败" in dlg.output.toPlainText()
    dlg.close()
