# -*- coding: utf-8 -*-
"""工具库对话框：收录时选择分类、分类管理。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout)

from app.core.db import Database
from app.core.models import Repo
from app.services import library as library_svc


class LibraryAddDialog(QDialog):
    """一个或多个项目收录到工具库；不选分类则进入待分类。"""

    def __init__(self, db: Database, repos: list[Repo], *,
                 selected_ids: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.repos = list(repos)
        self._selected = set(selected_ids or [])
        n = len(self.repos)
        self.setWindowTitle("收录到工具库" if n == 1 else f"批量收录 {n} 个项目")
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "选择分类（可多选）；不选则进入「待分类」。\n"
            "收录不等于收藏，也不会自动开启追踪。"))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(lambda: self._check_all(True))
        row.addWidget(all_btn)
        none_btn = QPushButton("清空")
        none_btn.clicked.connect(lambda: self._check_all(False))
        row.addWidget(none_btn)
        new_btn = QPushButton("＋ 新建分类")
        new_btn.clicked.connect(self._new_category)
        row.addWidget(new_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.summary_edit: QLineEdit | None = None
        if n == 1:
            self.summary_edit = QLineEdit()
            self.summary_edit.setPlaceholderText(
                "用途说明（可选）：例如“把 Markdown 批量转成 PPT”")
            lay.addWidget(self.summary_edit)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("收录")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._load()

    def _load(self):
        checked = self.selected_ids() or self._selected
        self.list.clear()
        for cat in library_svc.categories(self.db):
            item = QListWidgetItem(
                cat["name"] + ("（隐藏）" if cat.get("hidden") else ""))
            item.setData(Qt.UserRole, cat["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if cat["id"] in checked
                               else Qt.Unchecked)
            self.list.addItem(item)

    def _check_all(self, on: bool):
        state = Qt.Checked if on else Qt.Unchecked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def _new_category(self):
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称：")
        if not ok or not name.strip():
            return
        try:
            cid = library_svc.create_category(self.db, name)
        except ValueError as e:
            QMessageBox.warning(self, "无法新建", str(e))
            return
        self._selected = set(self.selected_ids()) | {cid}
        self._load()

    def selected_ids(self) -> list[str]:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.data(Qt.UserRole))
        return out

    def summary(self) -> str:
        return self.summary_edit.text().strip() if self.summary_edit else ""


class LibraryManagerDialog(QDialog):
    """分类管理：新建、重命名、排序、隐藏、删除（只删关系）。"""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("管理工具库分类")
        self.setMinimumWidth(440)
        self.setMinimumHeight(380)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "删除分类只会移除分类关系；项目、收藏、追踪、备注和历史均保留，"
            "无其他分类的项目进入「待分类」。"))
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.new_btn = QPushButton("＋ 新建")
        self.new_btn.clicked.connect(self._new)
        row.addWidget(self.new_btn)
        self.rename_btn = QPushButton("重命名")
        self.rename_btn.clicked.connect(self._rename)
        row.addWidget(self.rename_btn)
        self.hide_btn = QPushButton("隐藏/显示")
        self.hide_btn.clicked.connect(self._toggle_hidden)
        row.addWidget(self.hide_btn)
        self.up_btn = QPushButton("上移")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        row.addWidget(self.up_btn)
        self.down_btn = QPushButton("下移")
        self.down_btn.clicked.connect(lambda: self._move(1))
        row.addWidget(self.down_btn)
        self.del_btn = QPushButton("删除")
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.del_btn)
        lay.addLayout(row)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.button(QDialogButtonBox.Close).setText("关闭")
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._refresh()

    def _refresh(self, select_id: str | None = None):
        current = select_id
        if current is None and self.list.currentItem() is not None:
            current = self.list.currentItem().data(Qt.UserRole)
        counts = library_svc.counts(self.db)
        self.list.clear()
        for cat in library_svc.categories(self.db):
            n = counts["by_category"].get(cat["id"], 0)
            text = f"{cat['name']}  {n}"
            if cat.get("hidden"):
                text += "  （隐藏）"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, cat["id"])
            self.list.addItem(item)
            if cat["id"] == current:
                self.list.setCurrentItem(item)
        self._sync_buttons()

    def _sync_buttons(self):
        has = self.list.currentItem() is not None
        for b in (self.rename_btn, self.hide_btn, self.up_btn,
                  self.down_btn, self.del_btn):
            b.setEnabled(has)

    def _selected(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        cid = item.data(Qt.UserRole)
        return next((c for c in library_svc.categories(self.db)
                     if c["id"] == cid), None)

    def _new(self):
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称：")
        if not ok or not name.strip():
            return
        try:
            cid = library_svc.create_category(self.db, name)
        except ValueError as e:
            QMessageBox.warning(self, "无法新建", str(e))
            return
        self._refresh(cid)

    def _rename(self):
        cat = self._selected()
        if cat is None:
            return
        name, ok = QInputDialog.getText(self, "重命名分类", "新名称：",
                                        text=cat["name"])
        if not ok or not name.strip() or name.strip() == cat["name"]:
            return
        try:
            library_svc.rename_category(self.db, cat["id"], name)
        except ValueError as e:
            QMessageBox.warning(self, "无法重命名", str(e))
            return
        self._refresh(cat["id"])

    def _toggle_hidden(self):
        cat = self._selected()
        if cat is None:
            return
        library_svc.update_category(self.db, cat["id"],
                                    hidden=not bool(cat["hidden"]))
        self._refresh(cat["id"])

    def _move(self, delta: int):
        cat = self._selected()
        if cat is None:
            return
        cats = library_svc.categories(self.db)
        ids = [c["id"] for c in cats]
        idx = ids.index(cat["id"])
        new = idx + delta
        if not (0 <= new < len(ids)):
            return
        ids[idx], ids[new] = ids[new], ids[idx]
        library_svc.reorder_categories(self.db, ids)
        self._refresh(cat["id"])

    def _delete(self):
        cat = self._selected()
        if cat is None:
            return
        n = library_svc.counts(self.db)["by_category"].get(cat["id"], 0)
        move_to = None
        if n:
            others = [c for c in library_svc.categories(self.db)
                      if c["id"] != cat["id"] and not c.get("deleted")]
            labels = ["不转移（进入待分类）"] + [c["name"] for c in others]
            choice, ok = QInputDialog.getItem(
                self, "删除分类",
                f"「{cat['name']}」有 {n} 个项目。\n"
                "先选择这些项目的去处，再确认删除：",
                labels, 0, False)
            if not ok:
                return
            if choice != labels[0]:
                move_to = others[labels.index(choice) - 1]["id"]
        ans = QMessageBox.question(
            self, "确认删除分类",
            f"删除「{cat['name']}」？\n项目、收藏、追踪、备注和历史都会保留；"
            + ("项目将转移到所选分类。" if move_to else "项目将进入待分类。"))
        if ans != QMessageBox.Yes:
            return
        try:
            library_svc.delete_category(self.db, cat["id"], move_to=move_to)
        except ValueError as e:
            QMessageBox.warning(self, "无法删除", str(e))
            return
        self._refresh()
