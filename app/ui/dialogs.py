# -*- coding: utf-8 -*-
"""设置对话框与收藏备注对话框。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from app.core import config
from app.core.db import Database


class SettingsDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        s = db.all_settings()

        lay = QVBoxLayout(self)

        # ---- 语言通道 ----
        lang_box = QGroupBox("语言抓取通道（勾选参与抓取）")
        lg = QVBoxLayout(lang_box)
        self.lang_checks: dict[str, QCheckBox] = {}
        row = QHBoxLayout()
        for i, lang in enumerate(config.COMMON_LANGS + s.get("custom_langs", [])):
            cb = QCheckBox(lang)
            cb.setChecked(lang in s.get("languages", []))
            self.lang_checks[lang] = cb
            row.addWidget(cb)
            if (i + 1) % 5 == 0:
                lg.addLayout(row)
                row = QHBoxLayout()
        if row.count():
            lg.addLayout(row)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("自定义语言："))
        self.custom_edit = QLineEdit(",".join(s.get("custom_langs", [])))
        self.custom_edit.setPlaceholderText("如 Assembly, Haskell（逗号分隔）")
        crow.addWidget(self.custom_edit)
        lg.addLayout(crow)
        lay.addWidget(lang_box)

        # ---- 领域通道 ----
        dom_box = QGroupBox("领域抓取通道（跨语言，条数随「每通道抓取数量」设置）")
        dg = QHBoxLayout(dom_box)
        self.dom_checks: dict[str, QCheckBox] = {}
        for d, _ in config.DOMAIN_RULES:
            cb = QCheckBox(d)
            cb.setChecked(d in s.get("domains", []))
            self.dom_checks[d] = cb
            dg.addWidget(cb)
        dg.addStretch(1)
        lay.addWidget(dom_box)

        # ---- 常规 ----
        form = QFormLayout()
        self.pool_spin = QSpinBox()
        self.pool_spin.setRange(10, config.POOL_MAX)
        self.pool_spin.setValue(int(s.get("pool_size", config.POOL_N)))
        self.pool_spin.setSuffix(" 条/通道")
        form.addRow("每通道抓取数量", self.pool_spin)

        self.source_combo = QComboBox()
        for label, key in config.HOT_SOURCES:
            self.source_combo.addItem(label, key)
        idx = self.source_combo.findData(s.get("hot_source", "auto"))
        self.source_combo.setCurrentIndex(max(0, idx))
        form.addRow("本周热门数据源", self.source_combo)

        self.api_check = QCheckBox(
            "启用 GitHub API 源（Search / OpenGithubs / 官方历史；"
            "未认证额度极低，默认关闭）")
        self.api_check.setChecked(bool(s.get("github_api_enabled", False)))
        self.api_check.toggled.connect(self._sync_source_options)
        form.addRow("GitHub API 源", self.api_check)
        self._sync_source_options()

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色（GitHub Dark）", "dark")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.setCurrentIndex(0 if s.get("theme") == "dark" else 1)
        form.addRow("主题", self.theme_combo)

        self.gha_check = QCheckBox("后台自动下载小时包（约 5MB/小时，GH Archive 自建榜的数据来源）")
        self.gha_check.setChecked(bool(s.get("gha_hourly", True)))
        form.addRow("GH Archive 增量同步", self.gha_check)

        self.gha_backfill = QSpinBox()
        self.gha_backfill.setRange(0, 720)
        self.gha_backfill.setValue(int(s.get("gha_backfill_hours", 0) or 0))
        self.gha_backfill.setSuffix(" 小时（0=仅从最新小时开始）")
        self.gha_backfill.setToolTip(
            "仅首次同步（无游标）时生效；补历史会按每小时约 5MB 下载，"
            "每轮最多追 6 个小时包。")
        form.addRow("GH Archive 首次回填", self.gha_backfill)

        self.fetch_spin = QSpinBox()
        self.fetch_spin.setRange(0, 24)
        self.fetch_spin.setValue(int(s.get("fetch_interval_hours", 0) or 0))
        self.fetch_spin.setSuffix(" 小时（0=关闭）")
        form.addRow("定时抓取间隔", self.fetch_spin)

        self.upd_spin = QSpinBox()
        self.upd_spin.setRange(1, 168)
        self.upd_spin.setValue(int(s.get("update_interval_hours", 12) or 12))
        self.upd_spin.setSuffix(" 小时")
        form.addRow("更新检查间隔", self.upd_spin)

        self.token_edit = QLineEdit(s.get("github_token", "") or "")
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("可选 · 填后 API 限额 60 → 5000 次/小时")
        self.token_edit.setToolTip(
            "环境变量 GITHUB_TOKEN 优先于这里保存的值；\n"
            "清空此处只删除应用保存的 Token，不影响外部环境变量。")
        form.addRow("GitHub Token", self.token_edit)

        lay.addLayout(form)

        # ---- 翻译接口 ----
        tr_box = QGroupBox("翻译接口（不填则用默认 MyMemory 免费接口）")
        tl = QFormLayout(tr_box)
        self.tr_provider = QComboBox()
        self.tr_provider.addItem("MyMemory（默认，免配置）", "mymemory")
        self.tr_provider.addItem("LibreTranslate 兼容接口（可自建）", "libre")
        idx = self.tr_provider.findData(s.get("translate_provider", "mymemory"))
        self.tr_provider.setCurrentIndex(max(0, idx))
        tl.addRow("翻译接口", self.tr_provider)
        self.tr_endpoint = QLineEdit(s.get("translate_endpoint", "") or "")
        self.tr_endpoint.setPlaceholderText("如 https://libretranslate.com/translate 或自建地址")
        tl.addRow("接口地址", self.tr_endpoint)
        self.tr_apikey = QLineEdit(s.get("translate_apikey", "") or "")
        self.tr_apikey.setEchoMode(QLineEdit.Password)
        self.tr_apikey.setPlaceholderText("可选 · LibreTranslate.com 需要 API Key")
        tl.addRow("API Key", self.tr_apikey)
        self.tr_email = QLineEdit(s.get("translate_email", "") or "")
        self.tr_email.setPlaceholderText("可选 · MyMemory 注册邮箱，提额至约 5 万字符/天")
        tl.addRow("MyMemory 邮箱", self.tr_email)
        trow = QHBoxLayout()
        self.tr_test_btn = QPushButton("🧪 测试翻译")
        self.tr_test_btn.setProperty("class", "flat")
        self.tr_test_btn.clicked.connect(self._test_translate)
        trow.addWidget(self.tr_test_btn)
        self.tr_result = QLabel("（用当前填写的配置试译一句英文）")
        self.tr_result.setObjectName("sectionLabel")
        self.tr_result.setWordWrap(True)
        trow.addWidget(self.tr_result, 1)
        tl.addRow(trow)
        lay.addWidget(tr_box)

        # ---- 兴趣方向自定义 ----
        int_box = QGroupBox("兴趣匹配方向（每行：方向名: 词1, 词2；清空恢复内置）")
        il = QVBoxLayout(int_box)
        interests = s.get("interests") or config.INTEREST_KEYWORDS
        self.interests_edit = QPlainTextEdit(
            "\n".join(f"{k}: {', '.join(v)}" for k, v in interests.items()))
        self.interests_edit.setMaximumHeight(110)
        self.interests_edit.setPlaceholderText(
            "游戏引擎: engine, vulkan, render\nC++服务端: server, grpc, asio")
        il.addWidget(self.interests_edit)
        hint = QLabel("提示：项目简介多为英文，英文关键词命中率更高")
        hint.setObjectName("sectionLabel")
        il.addWidget(hint)
        lay.addWidget(int_box)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _sync_source_options(self):
        """API 源默认禁用；只有勾选后才允许在数据源下拉中选择。"""
        enabled = self.api_check.isChecked()
        blocked = {"opengithubs", "search"}
        model = self.source_combo.model()
        for i in range(self.source_combo.count()):
            key = self.source_combo.itemData(i)
            if key in blocked:
                item = model.item(i)
                if item is not None:
                    item.setEnabled(enabled)
                    item.setToolTip(
                        "" if enabled else "需勾选「GitHub API 源」后可选")
        if not enabled and self.source_combo.currentData() in blocked:
            self.source_combo.setCurrentIndex(
                max(0, self.source_combo.findData("trending")))

    def _tr_overrides(self) -> dict:
        return {
            "translate_provider": self.tr_provider.currentData(),
            "translate_endpoint": self.tr_endpoint.text().strip(),
            "translate_apikey": self.tr_apikey.text().strip(),
            "translate_email": self.tr_email.text().strip(),
        }

    def _test_translate(self):
        """用当前表单配置（未保存也生效）试译固定句子，结果就地显示。"""
        from PySide6.QtCore import QThread
        from app.services import translate as tr_svc
        overrides = self._tr_overrides()
        self.tr_test_btn.setEnabled(False)
        self.tr_result.setText("正在测试…")

        class _W(QThread):
            def run(self_):
                try:
                    self_.out = ("ok", tr_svc.translate(
                        "Open source makes the world better.",
                        self.db, overrides=overrides, skip_cache=True))
                except Exception as ex:
                    self_.out = ("err", str(ex))

        self._tr_worker = _W(self)
        self._tr_worker.finished.connect(self._on_tr_tested)
        self._tr_worker.start()

    def _on_tr_tested(self):
        status, out = getattr(self._tr_worker, "out", ("err", "未知错误"))
        self._tr_worker.deleteLater()
        self._tr_worker = None
        self.tr_test_btn.setEnabled(True)
        if status == "ok":
            self.tr_result.setText(f"✔ 成功：{out}")
        else:
            self.tr_result.setText(f"✘ 失败：{out}")

    def _save(self):
        custom = [x.strip() for x in self.custom_edit.text().split(",") if x.strip()]
        self.db.set_setting("custom_langs", custom)
        langs = [l for l, cb in self.lang_checks.items() if cb.isChecked()]
        # 新键入的自定义语言尚无复选框，直接纳入跟踪；
        # 已有复选框的尊重勾选状态（取消勾选即停止跟踪，不会被强制加回）
        for c in custom:
            if c not in self.lang_checks and c not in langs:
                langs.append(c)
        self.db.set_setting("languages", langs)
        self.db.set_setting("domains",
                            [d for d, cb in self.dom_checks.items() if cb.isChecked()])
        self.db.set_setting("pool_size", self.pool_spin.value())
        self.db.set_setting("hot_source", self.source_combo.currentData())
        self.db.set_setting("github_api_enabled", self.api_check.isChecked())
        self.db.set_setting("theme", self.theme_combo.currentData())
        self.db.set_setting("gha_hourly", self.gha_check.isChecked())
        self.db.set_setting("gha_backfill_hours", self.gha_backfill.value())
        self.db.set_setting("fetch_interval_hours", self.fetch_spin.value())
        self.db.set_setting("update_interval_hours", self.upd_spin.value())
        self.db.set_setting("github_token", self.token_edit.text().strip())
        self.db.set_setting("translate_provider", self.tr_provider.currentData())
        self.db.set_setting("translate_endpoint", self.tr_endpoint.text().strip())
        self.db.set_setting("translate_apikey", self.tr_apikey.text().strip())
        self.db.set_setting("translate_email", self.tr_email.text().strip())
        # 兴趣方向：解析 "方向: 词1, 词2" 行；空/全无效则恢复内置
        interests: dict[str, list[str]] = {}
        for line in self.interests_edit.toPlainText().splitlines():
            if ":" not in line:
                continue
            label, kws = line.split(":", 1)
            words = [w.strip() for w in kws.split(",") if w.strip()]
            if label.strip() and words:
                interests[label.strip()] = words
        self.db.set_setting("interests", interests or config.INTEREST_KEYWORDS)
        self.accept()

    def _tr_busy(self) -> bool:
        w = getattr(self, "_tr_worker", None)
        return bool(w is not None and w.isRunning())

    def accept(self):
        if self._tr_busy():
            self.tr_result.setText("翻译测试进行中，请稍候…")
            return
        super().accept()

    def reject(self):
        if self._tr_busy():
            self.tr_result.setText("翻译测试进行中，请稍候…")
            return
        super().reject()

    def closeEvent(self, e):
        # 翻译测试线程仍在跑时不允许销毁对话框（网络超时可达 20 秒）
        if self._tr_busy():
            self.tr_result.setText("翻译测试进行中，请稍候…")
            e.ignore()
            return
        super().closeEvent(e)


class NoteDialog(QDialog):
    """收藏备注 + 标签编辑。"""

    def __init__(self, db: Database, name: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.name = name
        self.setWindowTitle(f"备注 — {name}")
        self.setMinimumWidth(420)
        meta = db.favorite_meta(name)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("备注："))
        self.note_edit = QPlainTextEdit(meta.get("note", ""))
        self.note_edit.setMaximumHeight(120)
        lay.addWidget(self.note_edit)
        lay.addWidget(QLabel("标签（逗号分隔）："))
        self.tags_edit = QLineEdit(", ".join(meta.get("tags", [])))
        lay.addWidget(self.tags_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _save(self):
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        self.db.set_favorite_note(self.name, self.note_edit.toPlainText().strip(), tags)
        self.accept()


class ImportDialog(QDialog):
    """名单导入：粘贴文本或选文件，解析出 owner/repo 列表供确认。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入观察名单")
        self.setMinimumSize(520, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("粘贴名单（支持 github.com/owner/repo 链接或裸 owner/repo）："))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("ggml-org/llama.cpp\nhttps://github.com/torvalds/linux\n...")
        self.text_edit.textChanged.connect(self._update_count)
        lay.addWidget(self.text_edit)

        row = QHBoxLayout()
        pick = QPushButton("📂 选择文件…")
        pick.setProperty("class", "flat")
        pick.clicked.connect(self._pick_file)
        row.addWidget(pick)
        self.count_lbl = QLabel("识别到 0 个项目")
        row.addWidget(self.count_lbl)
        row.addStretch(1)
        lay.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("导入")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _pick_file(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "选择名单文件", "", "文本文件 (*.txt *.md *.csv);;全部 (*)")
        if not path:
            return
        for enc in ("utf-8", "gbk"):     # 中文 Windows 记事本常存 GBK
            try:
                with open(path, "r", encoding=enc) as f:
                    self.text_edit.setPlainText(f.read())
                return
            except UnicodeDecodeError:
                continue

    def names(self) -> list[str]:
        from app.services.importer import parse_repo_names
        return parse_repo_names(self.text_edit.toPlainText())

    def _update_count(self):
        self.count_lbl.setText(f"识别到 {len(self.names())} 个项目")


class HistoryDialog(QDialog):
    """历史快照回看：选择某次抓拍的快照查看，或回到最新。"""

    def __init__(self, db: Database, channel: str, days: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.channel = channel
        self.days = days
        self.selected_id: int | None = None
        self.setWindowTitle(f"历史快照 — {channel} · 近{days}天")
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("选择要回看的快照（双击打开）："))
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        self.list = QListWidget()
        for hid, taken_at in db.list_history(channel, days):
            item = QListWidgetItem(taken_at)
            item.setData(Qt.UserRole, hid)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        lay.addWidget(self.list)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("查看该快照")
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _accept(self):
        item = self.list.currentItem()
        if item is not None:
            self.selected_id = item.data(Qt.UserRole)
        self.accept()
