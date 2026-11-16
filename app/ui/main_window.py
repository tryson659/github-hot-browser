# -*- coding: utf-8 -*-
"""主窗口：工具栏 + 侧栏 + 卡片流 + 状态栏。

批1 范围：本周热门（三源降级）/ 我的收藏 双榜单；语言×领域×关键词组合筛选；
抓取（全部/单通道/取消）；深浅主题；卡片动效与骨架屏。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from app.core import config
from app.core.db import Database
from app.core.models import Repo, sort_key
from app.domain.metrics import metric_label
from app.services import exporter, hot_source, topics as topics_svc
from app.services import library as library_svc
from app.services import translate as translate_svc, watchlist
from app.services.fav_refresh import FavRefreshWorker
from app.services.fetcher import FetchWorker
from app.services.gha_sync import GhaBackfillThread, GhaSyncThread
from app.services.importer import import_names
from app.services.updater import UpdateCheckWorker, check_due
from app.sources import http as http_mod
from app.ui import theme as theme_mod
from app.ui.dialogs import (HistoryDialog, ImportDialog, NoteDialog,
                            SettingsDialog)
from app.ui.data_dialogs import DataManagementDialog
from app.ui.library_dialogs import LibraryAddDialog, LibraryManagerDialog
from app.ui.repo_card import RepoCard, SkeletonCard
from app.ui.tasks import TaskRegistry


class MainWindow(QMainWindow):
    def __init__(self, db: Database, start_sync: bool = True):
        super().__init__()
        self.db = db
        self._apply_token()
        library_svc.ensure_defaults(db)      # 首次启动播种默认分类，已删不恢复
        self.setWindowTitle("GitHub 热点项目浏览器 v2")
        self.resize(1180, 780)

        # ---- 视图状态 ----
        self.board = "hot"              # hot | fav | watch | lib
        self.days = 7
        self.lang_filter: str | None = None
        self.domain_filter: str | None = None
        self.sort_name = config.SORTS[0]
        self.tag_filter: str | None = None
        self.library_category: str | None = None   # None=全部；__uncat__=待分类
        self._lib_names: set[str] = set()
        self._lib_cat_map: dict[str, list[str]] = {}
        self._lib_note_map: dict[str, str] = {}
        self._pools: dict[tuple[int, str], list[Repo]] = {}
        self._rendered = 0
        self._worker: FetchWorker | None = None
        self._fav_worker: FavRefreshWorker | None = None
        self._upd_worker: UpdateCheckWorker | None = None
        self._gha_backfill: GhaBackfillThread | None = None
        self._skeletons: list[SkeletonCard] = []
        self._snapshot_override: list[Repo] | None = None   # 历史快照回看
        self._rank_badges: dict[str, str] = {}
        self._filling = False            # 侧栏重建回声抑制（防选中即被清除）
        # 关闭流程（R02）：统一登记所有工作线程，全部结束才关闭数据库
        self._tasks = TaskRegistry(self)
        self._closing = False
        self._shutdown_deadline = 0.0
        self._db_closed = False

        self.C = theme_mod.THEMES[db.get_setting("theme", "dark")]
        self._build_ui()
        self._apply_theme()
        self._load_pools()
        self._setup_tray()
        QTimer.singleShot(50, self._refresh_view)

        # GH Archive 后台自适应增量同步（设置里可关）
        self._gha: GhaSyncThread | None = None
        if start_sync:
            self._gha = GhaSyncThread(db, self)
            self._gha.status.connect(self._on_gha_status)
            self._tasks.track(self._gha)
            self._gha.start()
            # 启动 2 秒后静默检查更新（受节流）
            QTimer.singleShot(2000, lambda: self._check_updates(silent=True))
        self._setup_scheduler()

    # ================= UI 构建 =================
    def _build_ui(self):
        # ---- 工具栏 ----
        tb = QFrame(objectName="toolbar")
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(10, 6, 10, 6)
        tb_lay.setSpacing(4)

        self.board_btns: dict[str, QPushButton] = {}
        for label, key in (("🔥 本周热门", "hot"), ("⭐ 我的收藏", "fav"),
                           ("📈 增长观察", "watch"), ("🧰 工具库", "lib")):
            b = QPushButton(label, checkable=True)
            b.clicked.connect(lambda _=False, k=key: self._set_board(k))
            self.board_btns[key] = b
            tb_lay.addWidget(b)
        self.board_btns["hot"].setChecked(True)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        tb_lay.addWidget(sep)

        self.days_combo = QComboBox()
        for label, d in config.RANGES:
            self.days_combo.addItem(label, d)
        self.days_combo.currentIndexChanged.connect(self._on_days)
        tb_lay.addWidget(self.days_combo)

        # 数据源切换器：切换即切换该源独立快照（仅热门榜可见）
        self.source_combo = QComboBox()
        for label, key in config.HOT_SOURCES:
            self.source_combo.addItem(label, key)
        idx = self.source_combo.findData(
            self.db.get_setting("hot_source", "auto"))
        self.source_combo.setCurrentIndex(max(0, idx))
        self.source_combo.currentIndexChanged.connect(self._on_source)
        self.source_combo.setToolTip("本周热门数据源 · 每个源独立快照，切换即查看")
        self._sync_source_combo()
        tb_lay.addWidget(self.source_combo)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(config.SORTS)
        self.sort_combo.currentTextChanged.connect(self._on_sort)
        tb_lay.addWidget(self.sort_combo)

        self.kw_edit = QLineEdit()
        self.kw_edit.setPlaceholderText("关键词筛选（名称/描述/备注）…")
        self.kw_edit.setClearButtonEnabled(True)
        self.kw_edit.setMaximumWidth(240)
        self.kw_edit.textChanged.connect(lambda _t: self._refresh_view())
        tb_lay.addWidget(self.kw_edit)

        tb_lay.addStretch(1)

        self.fetch_btn = QPushButton("🔄 全部抓取", objectName="fetchBtn")
        self.fetch_btn.setProperty("class", "accent")
        self.fetch_btn.clicked.connect(lambda: self._start_fetch(None))
        tb_lay.addWidget(self.fetch_btn)

        self.cancel_btn = QPushButton("⏹ 取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_fetch)
        tb_lay.addWidget(self.cancel_btn)

        export_btn = QPushButton("📤 导出")
        export_btn.clicked.connect(self._export_view)
        tb_lay.addWidget(export_btn)

        self.collect_btn = QPushButton("📥 收录当前列表")
        self.collect_btn.setToolTip("把当前筛选结果加入工具库，可选择多个分类")
        self.collect_btn.clicked.connect(self._collect_current)
        tb_lay.addWidget(self.collect_btn)

        theme_btn = QPushButton("🌓")
        theme_btn.setToolTip("切换深浅主题")
        theme_btn.clicked.connect(self._toggle_theme)
        tb_lay.addWidget(theme_btn)

        settings_btn = QPushButton("⚙ 设置")
        settings_btn.clicked.connect(self._open_settings)
        tb_lay.addWidget(settings_btn)
        data_btn = QPushButton("💾")
        data_btn.setToolTip("数据管理：创建/校验备份，安排下次启动恢复")
        data_btn.clicked.connect(self._open_data_management)
        tb_lay.addWidget(data_btn)
        self.setCentralWidget(QWidget())
        root = QVBoxLayout(self.centralWidget())
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(tb)

        # ---- 中部：侧栏 + 卡片流 ----
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        root.addLayout(mid, 1)

        side = QFrame(objectName="sidebar")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(2)
        t1 = QLabel("语言（单选）")
        t1.setObjectName("sidebarTitle")
        t1.setToolTip("语言单选；与领域、标签按交集组合")
        self.lang_title = t1
        sl.addWidget(t1)
        self.lang_list = QListWidget()
        self.lang_list.itemClicked.connect(self._on_lang)
        sl.addWidget(self.lang_list, 1)
        self.lib_new_btn = QPushButton("＋ 新建分类")
        self.lib_new_btn.setProperty("class", "flat")
        self.lib_new_btn.clicked.connect(self._new_library_category)
        self.lib_new_btn.hide()
        sl.addWidget(self.lib_new_btn)
        self.lib_manage_btn = QPushButton("🗂 管理分类")
        self.lib_manage_btn.setProperty("class", "flat")
        self.lib_manage_btn.clicked.connect(self._manage_library_categories)
        self.lib_manage_btn.hide()
        sl.addWidget(self.lib_manage_btn)
        self.lib_add_btn = QPushButton("＋ 手动收录")
        self.lib_add_btn.setProperty("class", "flat")
        self.lib_add_btn.setToolTip("输入 owner/repo 或 GitHub URL，可多个")
        self.lib_add_btn.clicked.connect(self._add_library_by_name)
        self.lib_add_btn.hide()
        sl.addWidget(self.lib_add_btn)
        self.lib_batch_btn = QPushButton("🗂 批量分类")
        self.lib_batch_btn.setProperty("class", "flat")
        self.lib_batch_btn.clicked.connect(self._batch_library_categories)
        self.lib_batch_btn.hide()
        sl.addWidget(self.lib_batch_btn)
        self.lib_remove_batch_btn = QPushButton("🗑 批量移出")
        self.lib_remove_batch_btn.setProperty("class", "flat")
        self.lib_remove_batch_btn.clicked.connect(self._batch_remove_library)
        self.lib_remove_batch_btn.hide()
        sl.addWidget(self.lib_remove_batch_btn)
        t2 = QLabel("领域")
        t2.setObjectName("sidebarTitle")
        self.dom_title = t2
        sl.addWidget(t2)
        self.dom_list = QListWidget()
        self.dom_list.itemClicked.connect(self._on_domain)
        sl.addWidget(self.dom_list, 1)
        t3 = QLabel("标签（交集）")
        t3.setObjectName("sidebarTitle")
        t3.setToolTip("标签单选；与语言、领域按交集组合")
        self.tag_title = t3
        sl.addWidget(t3)
        self.tag_combo = QComboBox()
        self.tag_combo.addItem("（全部标签）", None)
        self.tag_combo.currentIndexChanged.connect(self._on_tag_combo)
        sl.addWidget(self.tag_combo)
        self.topics_btn = QPushButton("🏷 补齐当前列表标签")
        self.topics_btn.setProperty("class", "flat")
        self.topics_btn.clicked.connect(self._enrich_visible_topics)
        sl.addWidget(self.topics_btn)
        clear_btn = QPushButton("✖ 清除筛选")
        clear_btn.setProperty("class", "flat")
        clear_btn.clicked.connect(self._clear_filters)
        self.clear_btn = clear_btn
        sl.addWidget(clear_btn)
        self.fetch_cur_btn = QPushButton("🎯 抓取当前通道")
        self.fetch_cur_btn.setProperty("class", "flat")
        self.fetch_cur_btn.clicked.connect(self._fetch_current)
        sl.addWidget(self.fetch_cur_btn)
        self.gha_btn = QPushButton("🗄 补齐近7天 GH Archive")
        self.gha_btn.setProperty("class", "flat")
        self.gha_btn.setToolTip("下载最近的 GH Archive 小时包；可取消，已下载会跳过")
        self.gha_btn.clicked.connect(self._start_gha_backfill)
        self.gha_btn.hide()
        sl.addWidget(self.gha_btn)
        self.fav_refresh_btn = QPushButton("🔃 刷新收藏数据")
        self.fav_refresh_btn.setProperty("class", "flat")
        self.fav_refresh_btn.clicked.connect(self._refresh_favorites)
        sl.addWidget(self.fav_refresh_btn)
        self.watch_add_btn = QPushButton("＋ 观察项目")
        self.watch_add_btn.setProperty("class", "flat")
        self.watch_add_btn.clicked.connect(self._add_watch)
        sl.addWidget(self.watch_add_btn)
        import_btn = QPushButton("📥 导入名单")
        import_btn.setProperty("class", "flat")
        import_btn.clicked.connect(self._import_watchlist)
        self.import_btn = import_btn
        sl.addWidget(import_btn)
        self.upd_btn = QPushButton("🔔 检查更新")
        self.upd_btn.setProperty("class", "flat")
        self.upd_btn.clicked.connect(lambda: self._check_updates(silent=False))
        sl.addWidget(self.upd_btn)
        hist_btn = QPushButton("📜 历史快照")
        hist_btn.setProperty("class", "flat")
        hist_btn.clicked.connect(self._open_history)
        self.hist_btn = hist_btn
        sl.addWidget(hist_btn)
        mid.addWidget(side)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 平滑滚动：滚轮由 QScroller 接管为像素级惯性滚动
        from PySide6.QtWidgets import QScroller, QScrollerProperties
        QScroller.grabGesture(self.scroll.viewport(),
                              QScroller.LeftMouseButtonGesture)
        props = QScroller.scroller(self.scroll.viewport()).scrollerProperties()
        props.setScrollMetric(QScrollerProperties.VerticalOvershootPolicy,
                              QScrollerProperties.OvershootAlwaysOff)
        QScroller.scroller(self.scroll.viewport()).setScrollerProperties(props)
        self.cards_host = QWidget()
        self.cards_lay = QVBoxLayout(self.cards_host)
        self.cards_lay.setContentsMargins(6, 6, 6, 6)
        self.cards_lay.setSpacing(2)
        self.cards_lay.addStretch(1)
        self.scroll.setWidget(self.cards_host)
        mid.addWidget(self.scroll, 1)

        # ---- 状态栏 ----
        self.status_lbl = QLabel("就绪")
        self.status_lbl.setObjectName("statusbar")
        root.addWidget(self.status_lbl)

    def _apply_theme(self):
        self.setStyleSheet(theme_mod.app_qss(self.C))

    def _on_gha_status(self, msg: str):
        """后台同步状态不抢状态栏：抓取/操作反馈优先。"""
        if self._worker is not None:
            return
        self.status_lbl.setText(msg)

    # ================= 数据 =================
    def _pool(self, days: int, channel: str) -> list[Repo]:
        key = (days, channel)
        if key not in self._pools:
            repos, _, _ = self.db.load_cache(channel, days)
            self._pools[key] = repos
        return self._pools[key]

    def _load_pools(self):
        for ch in self.db.cached_channels():
            for _, d in config.RANGES:
                self._pool(d, ch)

    def _current_pool(self) -> list[Repo]:
        if self.board == "fav":
            return self.db.list_favorites()
        if self.board == "watch":
            return watchlist.board_rows(self.db)
        if self.board == "lib":
            return self._library_pool()
        if self._snapshot_override is not None:
            return list(self._snapshot_override)
        return list(self._pool(self.days, self._hot_channel()))

    def _library_pool(self) -> list[Repo]:
        cat = self.library_category
        entries = library_svc.entries(
            self.db,
            category_id=(None if cat in (None, "__lib_all__", "__uncat__")
                         else cat),
            uncategorized=(cat == "__uncat__"))
        self._lib_cat_map = {}
        self._lib_note_map = {}
        growth = library_svc.growth_for(
            self.db, [e["name"] for e in entries], self.days)
        repo_rows = []
        for e in entries:
            r = Repo(
                name=e["name"], url=e.get("url", ""),
                stars=e.get("stars"), forks=e.get("forks"),
                language=e.get("language") or "-",
                description=e.get("description", ""),
                pushed=e.get("pushed") or "-",
                archived=bool(e.get("archived")), source="工具库")
            if e["name"] in growth:
                r.growth_value = growth[e["name"]]
                if self.days == 7:
                    r.growth_7d = growth[e["name"]]
                r.metric = "net_stars"
                r.growth_window = f"近{self.days}天"
                r.window_days = self.days
                r.completeness = "valid"
            r.library_added_at = e.get("added_at", "")
            self._lib_cat_map[e["name"]] = [
                c["name"] for c in e.get("categories", [])]
            self._lib_note_map[e["name"]] = e.get("user_summary", "")
            repo_rows.append(r)
        return repo_rows

    def _filtered(self) -> list[Repo]:
        pool = self._hydrate_topics(self._current_pool())
        if self.board == "hot":
            if self.lang_filter:
                pool = [r for r in pool if r.language == self.lang_filter]
            if self.tag_filter:
                pool = [r for r in pool
                        if self.tag_filter in (r.topics or [])
                        or self.tag_filter in (self.db.get_topics(r.name) or [])]
        if self.board != "lib" and self.domain_filter:
            # 领域筛选对热门/收藏/观察生效；工具库按分类组织
            pool = [r for r in pool
                    if getattr(r, "domain", "") == self.domain_filter]
        kw = self.kw_edit.text().strip().lower()
        if kw:
            if self.board == "fav":
                pool = [r for r in pool
                        if kw in r.name.lower() or kw in r.description.lower()
                        or kw in self.db.favorite_meta(r.name).get("note", "").lower()
                        or any(kw in t.lower()
                               for t in self.db.favorite_meta(r.name).get("tags", []))]
            elif self.board == "lib":
                pool = [r for r in pool
                        if kw in r.name.lower() or kw in r.description.lower()
                        or kw in self._lib_note_map.get(r.name, "").lower()
                        or any(kw in c.lower()
                               for c in self._lib_cat_map.get(r.name, []))]
            else:
                pool = [r for r in pool
                        if kw in r.name.lower() or kw in r.description.lower()]
        if self.board == "watch":
            pool.sort(key=lambda r: -r.growth_daily)   # 观察榜固定按日均增长
        else:
            pool.sort(key=sort_key(self.sort_name, self.days), reverse=True)
        return pool

    # ================= 渲染 =================
    def _clear_cards(self):
        for s in self._skeletons:
            s.stop()
        self._skeletons.clear()
        while self.cards_lay.count() > 1:      # 保留末尾 stretch
            item = self.cards_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)      # 立刻从画面移除；deleteLater 等事件循环
                w.deleteLater()

    def _refresh_view(self, *_):
        self._lib_names = library_svc.entry_names(self.db)
        self._clear_cards()
        self._rendered = 0
        self._sync_sidebar_mode()
        self._compute_rank_badges()
        self._refresh_sidebar()
        self._refresh_tag_combo()
        pool = self._filtered()
        if not pool:
            if self._worker is not None and self.board == "hot":
                self._show_skeletons()
            else:
                self._show_empty_hint()
            return
        self._render_more()

    def _show_empty_hint(self):
        """空态提示（对齐原版）：池空时给出明确的下一步指引，不再留白。"""
        has_filter = bool(self.lang_filter or self.domain_filter
                          or self.tag_filter or self.kw_edit.text().strip())
        if self.board == "lib":
            if self.kw_edit.text().strip():
                text = "当前关键词在工具库中没有匹配。\n清空搜索或切换分类试试。"
            elif self.library_category == "__uncat__":
                text = ("待分类里没有项目。\n去「🔥 本周热门」点卡片上的"
                        "「＋ 工具库」，或「📥 收录当前列表」。")
            elif self.library_category:
                text = ("这个分类还没有项目。\n在热门榜点「＋ 工具库」并勾选此分类；"
                        "或切到「待分类」批量整理。")
            else:
                text = ("工具库还是空的：去「🔥 本周热门」点卡片上的"
                        "「＋ 工具库」，或点工具栏「📥 收录当前列表」。\n"
                        "收录不等于收藏，也不会自动开启追踪。")
        elif has_filter and self.board == "hot":
            text = ("当前筛选组合没有数据。\n"
                    "试试调整语言/领域/标签/关键词，或点左侧「✖ 清除筛选」。")
        elif self.board == "watch":
            text = ("观察名单还是空的：点左侧「＋ 观察项目」手动添加，\n"
                    "或先「🔄 全部抓取」让程序自动收录。")
        elif self.board == "fav":
            text = "还没有收藏：点卡片上的 ☆ 即可收藏项目。"
        else:
            text = (f"数据源「{self.source_combo.currentText()}」暂无缓存数据。\n"
                    "点左侧「🎯 抓取当前通道」或工具栏「🔄 全部抓取」获取；\n"
                    "也可以切换到其他已有数据的数据源。")
            if self._is_gha_source():
                cov = self._gha_coverage_text()
                text += f"\n\nGH Archive 覆盖：{cov}\n左侧「🗄 补齐近{self.days}天」可下载缺失小时。"
        hint = QLabel(text, objectName="emptyHint")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        self.cards_lay.insertWidget(0, hint)
        self.status_lbl.setText("暂无数据")

    def _compute_rank_badges(self):
        """与次新快照对比生成 🆕/▲N/▼N 徽章（仅热门榜且非快照回看时）。"""
        self._rank_badges = {}
        if self.board != "hot" or self._snapshot_override is not None:
            return
        prev = self.db.latest_snapshot(self._hot_channel(), self.days,
                                       exclude_latest=True)
        if not prev:
            return
        cur = self._filtered()
        prev_rank = {r.name: i + 1 for i, r in enumerate(prev)}
        for i, r in enumerate(cur, 1):
            if r.name not in prev_rank:
                self._rank_badges[r.name] = "🆕"
            else:
                delta = prev_rank[r.name] - i
                if delta > 0:
                    self._rank_badges[r.name] = f"▲{delta}"
                elif delta < 0:
                    self._rank_badges[r.name] = f"▼{-delta}"

    def _render_more(self):
        pool = self._filtered()
        start = self._rendered
        end = min(start + config.PAGE_SIZE, len(pool))
        # 移除旧的"加载更多"按钮
        if self.cards_lay.count() > 1:
            last = self.cards_lay.itemAt(self.cards_lay.count() - 2)
            if last and last.widget() and last.widget().objectName() == "moreBtn":
                w = last.widget()
                self.cards_lay.removeWidget(w)
                w.deleteLater()
        for i in range(start, end):
            r = pool[i]
            fav = self.db.is_favorite(r.name)
            meta = self.db.favorite_meta(r.name) if fav else None
            card = RepoCard(
                i + 1, r, self.C, favorite=fav, meta=meta,
                rank_badge=self._rank_badges.get(r.name),
                in_library=(self.board == "lib" or r.name in self._lib_names),
                library_categories=self._lib_cat_map.get(r.name),
                library_board=(self.board == "lib"),
                library_note=self._lib_note_map.get(r.name, ""))
            card.favorite_clicked.connect(self._toggle_favorite)
            card.note_clicked.connect(self._open_note)
            card.translate_clicked.connect(self._translate_card)
            card.tag_clicked.connect(self._pick_tag)
            card.release_clicked.connect(self._fetch_release)
            card.library_clicked.connect(self._collect_one)
            card.library_remove_clicked.connect(self._remove_from_library)
            rel = self.db.get_release(r.name)
            if rel:
                card.set_release(rel["tag"], rel["date"])
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, card)
            card.fade_in(delay_ms=(i - start) * 22)
        self._rendered = end
        if end < len(pool):
            more = QPushButton(f"加载更多（{end}/{len(pool)}）", objectName="moreBtn")
            more.setProperty("class", "flat")
            more.clicked.connect(self._render_more)
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, more)
        # 状态栏口径说明
        if self._snapshot_override is not None:
            self.status_lbl.setText(
                f"正在回看历史快照（{len(pool)} 条）· 点「回到最新」恢复")
            return
        if self.board == "hot":
            _, fetched_at, period = self.db.load_cache(
                self._hot_channel(), self.days)
        else:
            _, fetched_at, period = [], 0, ""
        if self.board == "hot" and fetched_at:
            import datetime as _dt
            ts = _dt.datetime.fromtimestamp(fetched_at).strftime("%m-%d %H:%M")
            metrics = sorted({r.metric for r in pool if r.metric})
            if len(metrics) == 1:
                metric_txt = metric_label(metrics[0])
            elif metrics:
                metric_txt = "混合口径（逐条标注）"
            else:
                metric_txt = "未知口径（旧数据）"
            self.status_lbl.setText(
                f"{len(pool)} 条 · 数据时间 {ts} · {period or '本地数据'}"
                f" · 口径：{metric_txt}")
        elif self.board == "fav":
            self.status_lbl.setText(f"收藏 {len(pool)} 个项目")
        elif self.board == "watch":
            manual = len(self.db.watch_list(manual_only=True))
            self.status_lbl.setText(
                f"观察 {len(pool)} 个项目（手动 {manual}）· 日均增长按最近采样窗口计算")
        elif self.board == "lib":
            all_n = len(self._lib_names)
            known = sum(1 for r in pool if r.window_value(self.days) is not None)
            extra = f" · 含增长数据 {known}" if known else ""
            self.status_lbl.setText(
                f"工具库 {all_n} 个项目 · 当前 {len(pool)} 个{extra}"
                f" · 收录不等于收藏/追踪")

    def _show_skeletons(self):
        for _ in range(5):
            s = SkeletonCard(self.C)
            self._skeletons.append(s)
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, s)

    # ================= 侧栏 =================
    FAV_ROW = "__fav__"          # 语言列表里「⭐ 我的收藏」行的键

    def _sync_sidebar_mode(self):
        """工具库只显示分类侧栏；热门/GH Archive 只显示对应操作。"""
        lib = self.board == "lib"
        for w in (self.dom_title, self.dom_list, self.tag_title,
                  self.tag_combo, self.topics_btn,
                  self.fav_refresh_btn, self.watch_add_btn,
                  self.import_btn, self.upd_btn, self.hist_btn,
                  self.fetch_cur_btn):
            w.setVisible(not lib)
        self.lib_new_btn.setVisible(lib)
        self.lib_manage_btn.setVisible(lib)
        self.lib_add_btn.setVisible(lib)
        self.lib_batch_btn.setVisible(lib)
        self.lib_remove_batch_btn.setVisible(lib)
        self.collect_btn.setVisible(not lib)
        gha = (not lib and self.board == "hot" and self._is_gha_source())
        self.gha_btn.setVisible(gha)
        if gha and self._gha_backfill is None:
            self.gha_btn.setText(f"🗄 补齐近{self.days}天 GH Archive")
        self.source_combo.setVisible(self.board == "hot")

    def _refresh_sidebar(self):
        """语言/领域列表常驻（对齐原版）：收藏入口 + 全部 + 设置里跟踪的
        各语言/领域通道，数量取自各通道独立缓存——与当前筛选池无关，
        池空时列表也始终在。工具库榜改为分类列表。"""
        if self.board == "lib":
            counts = library_svc.counts(self.db)
            current = self.library_category or "__lib_all__"
            rows = [("全部工具", counts["total"], "__lib_all__"),
                    ("待分类", counts["uncategorized"], "__uncat__")]
            for cat in library_svc.categories(self.db):
                rows.append((cat["name"],
                             counts["by_category"].get(cat["id"], 0),
                             cat["id"]))
            self._fill_list(self.lang_list, rows, current)
            self.lang_title.setText("分类")
            return
        self.lang_title.setText("语言（单选）")
        self.lang_title.setToolTip("语言为单选筛选；与领域、标签按交集组合")
        days = self.days
        fav_n = len(self.db.list_favorites())
        # 侧栏计数必须来自当前热门池，而不是尚未抓取的语言/领域通道；
        # 否则 API 源关闭时会出现“全部语言 0，但列表有 33 条”的矛盾。
        pool = self._current_pool() if self.board == "hot" else self._pool(
            days, self._hot_channel())
        pool_langs: dict[str, int] = {}
        pool_doms: dict[str, int] = {}
        for r in pool:
            if r.language:
                pool_langs[r.language] = pool_langs.get(r.language, 0) + 1
            if getattr(r, "domain", ""):
                pool_doms[r.domain] = pool_doms.get(r.domain, 0) + 1
        lang_rows = [("⭐ 我的收藏", fav_n, self.FAV_ROW),
                     ("全部语言", len(pool), None)]
        tracked_langs = list(self.db.get_setting("languages", []) or [])
        for lang in tracked_langs:
            lang_rows.append((lang, pool_langs.get(lang, 0), lang))
        for lang, n in sorted(pool_langs.items(), key=lambda kv: -kv[1]):
            if lang not in tracked_langs:
                lang_rows.append((lang, n, lang))
        self._fill_list(
            self.lang_list, lang_rows,
            self.FAV_ROW if self.board == "fav" else self.lang_filter)

        self.dom_title.setToolTip("领域为单选筛选；与语言、标签按交集组合")
        dom_rows = [("全部领域", len(pool), None)]
        tracked_doms = list(self.db.get_setting("domains", []) or [])
        for dom in tracked_doms:
            dom_rows.append((dom, pool_doms.get(dom, 0), dom))
        for dom, n in sorted(pool_doms.items(), key=lambda kv: -kv[1]):
            if dom not in tracked_doms:
                dom_rows.append((dom, n, dom))
        self._fill_list(self.dom_list, dom_rows, self.domain_filter)

    def _fill_list(self, box: QListWidget,
                   rows: list[tuple[str, int, str | None]],
                   current: str | None):
        """重建列表并恢复选中；全程 _filling 抑制回声，杜绝"选中即被清除"。"""
        self._filling = True
        try:
            box.clear()
            for name, n, key in rows:
                it = QListWidgetItem(f"{name}  {n}")
                it.setData(Qt.UserRole, key)
                box.addItem(it)
            for i in range(box.count()):
                if box.item(i).data(Qt.UserRole) == current:
                    box.setCurrentRow(i)
                    break
            else:
                box.setCurrentRow(-1)
        finally:
            self._filling = False

    def _on_lang(self, item):
        """itemClicked：点击已选中行也会触发，toggle 取消语义可用。"""
        if self._filling or item is None:
            return
        key = item.data(Qt.UserRole)
        if self.board == "lib":
            if key == "__lib_all__":
                self.library_category = None
            elif key == "__uncat__":
                self.library_category = "__uncat__"
            elif key:
                self.library_category = key
            self._refresh_view()
            return
        if key == self.FAV_ROW:
            self._set_board("fav")
            return
        # 全部语言 = 清除语言筛选；再次点击同一语言 = 取消（toggle）
        self.lang_filter = None if key is None or key == self.lang_filter else key
        if self.board != "hot":
            self._set_board("hot")     # 语言筛选作用于热门池，先切回热门榜
        else:
            self._refresh_view()

    def _on_domain(self, item):
        if self._filling or item is None:
            return
        key = item.data(Qt.UserRole)
        # 全部领域 = 清除领域筛选；语言×领域可叠加（组合筛选，不对另一栏互斥）
        self.domain_filter = None if key is None or key == self.domain_filter else key
        self._refresh_view()

    def _clear_filters(self):
        self.lang_filter = None
        self.domain_filter = None
        self.tag_filter = None
        self.library_category = None
        self.kw_edit.clear()
        self.lang_list.setCurrentRow(-1)
        self.dom_list.setCurrentRow(-1)
        self.tag_combo.setCurrentIndex(0)
        self._refresh_view()

    # ================= 标签筛选 =================
    def _hydrate_topics(self, repos: list[Repo]) -> list[Repo]:
        """把 DB 缓存的 topics 合并到卡片对象，避免补齐后仍无标签可点。"""
        for r in repos:
            if r.topics:
                continue
            cached = self.db.get_topics(r.name)
            if cached:
                r.topics = cached
        return repos

    def _refresh_tag_combo(self):
        cands = topics_svc.candidates(
            self._hydrate_topics(self._current_pool()), db=self.db)
        cur = self.tag_filter
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem("（全部标签）", None)
        for t in cands:
            self.tag_combo.addItem(t, t)
        if cur:
            idx = self.tag_combo.findData(cur)
            self.tag_combo.setCurrentIndex(max(0, idx))
        else:
            self.tag_combo.setCurrentIndex(0)
        self.tag_combo.blockSignals(False)

    def _on_tag_combo(self, idx: int):
        self.tag_filter = self.tag_combo.itemData(idx)
        self._refresh_view()

    def _pick_tag(self, tag: str):
        """卡片标签点击即筛。"""
        self.tag_filter = tag
        idx = self.tag_combo.findData(tag)
        if idx < 0:
            self.tag_combo.addItem(tag, tag)
            idx = self.tag_combo.count() - 1
        self.tag_combo.setCurrentIndex(idx)
        self._refresh_view()

    def _enrich_visible_topics(self):
        """补齐当前视图前 30 个项目的 topics（ecosyste.ms，工作线程）。"""
        names = [r.name for r in self._current_pool()[:30]]
        if not names:
            return
        from PySide6.QtCore import QThread

        class _TopicsWorker(QThread):
            def __init__(self_, *a, **kw):
                super().__init__(*a, **kw)
                self_.cancel = threading.Event()

            def run(self_):
                self_.done_n = topics_svc.enrich(
                    self.db, names, cancel=self_.cancel,
                    log=lambda m: self.status_lbl.setText(m))

        self._topics_worker = _TopicsWorker(self)
        self._topics_worker.finished.connect(self._on_topics_done)
        self._tasks.track(self._topics_worker)
        self.status_lbl.setText("正在补齐标签…")
        self._topics_worker.start()

    def _on_topics_done(self):
        n = getattr(self._topics_worker, "done_n", 0)
        self._topics_worker.deleteLater()
        self._topics_worker = None
        self._refresh_view()
        # 完成提示放在刷新之后，避免被状态栏数据时间覆盖
        self.status_lbl.setText(f"标签补齐完成（{n} 个）")

    # ================= Release 查询 =================
    def _fetch_release(self, card: RepoCard):
        cached = self.db.get_release(card.repo.name)
        if cached:
            card.set_release(cached["tag"], cached["date"])
            return
        if not self._api_enabled():
            self.status_lbl.setText(
                "GitHub API 源已默认禁用；查询 Release 需要 API，请在设置中启用")
            return
        from PySide6.QtCore import QThread
        from app.sources import github_search
        name = card.repo.name

        class _RelWorker(QThread):
            def run(self_):
                try:
                    self_.result = ("ok", github_search.fetch_release(name))
                except Exception as ex:
                    self_.result = ("err", str(ex))

        w = _RelWorker(self)
        self._tasks.track(w)
        w.finished.connect(lambda: self._on_release(w, card))
        w.start()

    def _on_release(self, worker, card: RepoCard):
        status, result = getattr(worker, "result", ("err", "未知错误"))
        worker.deleteLater()
        if status == "ok":
            self.db.save_release(card.repo.name, result["tag"], result["date"])
            try:
                card.set_release(result["tag"], result["date"])
            except RuntimeError:
                pass
            self.status_lbl.setText(
                f"{card.repo.name} 最新版本：{result['tag']}")
        else:
            self.status_lbl.setText(
                f"{card.repo.name} 无 Release 或查询失败")

    # ================= 定时抓取 =================
    def _setup_scheduler(self):
        self._sched_timer = QTimer(self)
        self._sched_timer.timeout.connect(self._maybe_auto_fetch)
        self._sched_timer.start(60000)

    def _maybe_auto_fetch(self):
        if self._closing:
            return
        interval = float(self.db.get_setting("fetch_interval_hours", 0) or 0)
        if interval <= 0 or self._worker is not None:
            return
        import time as _time
        last = self.db.get_setting("__last_fetch__", 0.0) or 0.0
        if _time.time() - last >= interval * 3600:
            self.status_lbl.setText("⏰ 定时抓取启动…")
            self._start_fetch(None)

    # ================= 工具栏动作 =================
    def _set_board(self, key: str):
        self.board = key
        for k, b in self.board_btns.items():
            b.setChecked(k == key)
        self.source_combo.setVisible(key == "hot")
        self.fetch_cur_btn.setEnabled(key == "hot" and self._worker is None)
        if key == "lib":
            self.lang_filter = None
            self.domain_filter = None
            self.tag_filter = None
        self._sync_sidebar_mode()
        self._refresh_view()

    def _on_days(self, idx: int):
        self.days = self.days_combo.itemData(idx)
        self._snapshot_override = None   # 快照属于原时间档，切换后退出回看
        if self._is_gha_source():
            self.gha_btn.setText(f"🗄 补齐近{self.days}天 GH Archive")
        self._refresh_view()

    def _on_source(self, idx: int):
        """切换数据源：写入设置并立即切换该源的独立快照视图。"""
        key = self.source_combo.itemData(idx)
        if key in ("opengithubs", "search") and not self._api_enabled():
            self.status_lbl.setText("GitHub API 源已默认禁用，请先在设置中启用")
            self._sync_source_combo()
            return
        self.db.set_setting("hot_source", key)
        self._snapshot_override = None
        self._sync_sidebar_mode()
        self._refresh_view()

    def _api_enabled(self) -> bool:
        return bool(self.db.get_setting("github_api_enabled", False))

    def _sync_source_combo(self):
        """API 源默认禁用；仅禁用下拉项，不强制改用户已保存的选择。"""
        enabled = self._api_enabled()
        model = self.source_combo.model()
        for i in range(self.source_combo.count()):
            if self.source_combo.itemData(i) in ("opengithubs", "search"):
                item = model.item(i)
                if item is not None:
                    item.setEnabled(enabled)
                    item.setToolTip(
                        "" if enabled else "需先在设置中启用 GitHub API 源")

    def _is_gha_source(self) -> bool:
        return (self.source_combo.currentData() == "gharchive")

    def _gha_coverage_text(self) -> str:
        from app.services import gha_board
        try:
            ledger = gha_board.archive_coverage(self.db, self.days)
            covered, expected = gha_board.window_coverage(self.db, self.days)
        except Exception as e:
            return f"无法读取（{e}）"
        got = ledger["success"] + ledger["empty"]
        return (f"账本 {got}/{ledger['expected']} 小时"
                f"（失败 {ledger['failed']}，未取 {ledger['missing']}）"
                f"；实际覆盖 {covered}/{expected}")

    def _hot_channel(self) -> str:
        """当前视图应显示的热门榜通道键（自动源按 7/30 天分别记录，R04）。"""
        return hot_source.channel_for(self.db, self.days)

    def _on_sort(self, name: str):
        self.sort_name = name
        self._refresh_view()

    def _toggle_theme(self):
        new = "light" if self.db.get_setting("theme") == "dark" else "dark"
        self.db.set_setting("theme", new)
        self.C = theme_mod.THEMES[new]
        self._apply_theme()
        self._refresh_view()

    def _apply_token(self):
        """把设置里的 GitHub Token 注入 HTTP 层（R08）。

        环境变量 GITHUB_TOKEN 优先；清空设置只清除应用保存值，
        不再 pop 进程环境，避免覆盖用户显式提供的外部配置。
        """
        http_mod.set_persisted_token(self.db.get_setting("github_token", "") or "")

    def _open_settings(self):
        dlg = SettingsDialog(self.db, self)
        if dlg.exec():
            self._apply_token()
            self.C = theme_mod.THEMES[self.db.get_setting("theme", "dark")]
            self._apply_theme()
            # 设置里可能改了数据源：回写工具栏下拉框（阻断信号防重复写库）
            src = self.db.get_setting("hot_source", "auto")
            idx = self.source_combo.findData(src)
            if idx >= 0 and idx != self.source_combo.currentIndex():
                self.source_combo.blockSignals(True)
                self.source_combo.setCurrentIndex(idx)
                self.source_combo.blockSignals(False)
            self._sync_source_combo()
            self._pools.clear()
            self._refresh_view()
            self.status_lbl.setText("设置已保存")

    # ================= 收藏 =================
    def _toggle_favorite(self, name: str):
        if self.db.is_favorite(name):
            self.db.remove_favorite(name)
            self.status_lbl.setText(f"已取消收藏：{name}")
        else:
            repo = self._find_repo(name)
            if repo:
                self.db.add_favorite(repo)
                self.status_lbl.setText(f"已收藏：{name}")
        self._refresh_view()

    def _find_repo(self, name: str) -> Repo | None:
        if self.board == "fav":
            for r in self.db.list_favorites():
                if r.name == name:
                    return r
        for r in self._current_pool():
            if r.name == name:
                return r
        for pool in self._pools.values():
            for r in pool:
                if r.name == name:
                    return r
        return None

    def _open_note(self, name: str):
        dlg = NoteDialog(self.db, name, self)
        if dlg.exec():
            self._refresh_view()

    # ================= 工具库 =================
    def _collect_one(self, name: str):
        repo = self._find_repo(name)
        if repo is None:
            self.status_lbl.setText(f"找不到项目：{name}")
            return
        entry = library_svc.entry(self.db, name)
        selected = [c["id"] for c in entry.get("categories", [])] if entry else []
        dlg = LibraryAddDialog(self.db, [repo], selected_ids=selected, parent=self)
        dlg.setWindowTitle("调整工具库分类" if entry else "收录到工具库")
        if dlg.exec() != QDialog.Accepted:
            return
        if entry:
            library_svc.set_entry_categories(self.db, name, dlg.selected_ids())
            self.status_lbl.setText(f"已更新分类：{name}")
        else:
            library_svc.add_repo(self.db, repo, categories=dlg.selected_ids(),
                                 source="热门榜单")
            self.status_lbl.setText(f"已收录到工具库：{name}（收藏/追踪未改变）")
        self._refresh_view()

    def _collect_current(self):
        pool = self._filtered()
        if not pool:
            self.status_lbl.setText("当前列表为空，先抓取或切换榜单")
            return
        dlg = LibraryAddDialog(self.db, pool, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = dlg.selected_ids()
        for repo in pool:
            library_svc.add_repo(self.db, repo, categories=ids, source="热门批量")
        names = "、".join(c["name"] for c in
                         library_svc.categories(self.db) if c["id"] in ids)
        self.status_lbl.setText(
            f"已收录 {len(pool)} 个项目"
            + (f"到：{names}" if names else "（待分类）"))
        self._refresh_view()

    def _remove_from_library(self, name: str):
        ans = QMessageBox.question(
            self, "移出工具库",
            f"把「{name}」移出工具库？\n收藏、追踪、备注和历史都会保留。")
        if ans != QMessageBox.Yes:
            return
        if library_svc.remove_entry(self.db, name):
            self.status_lbl.setText(f"已移出工具库：{name}")
        else:
            self.status_lbl.setText(f"项目不在工具库：{name}")
        self._refresh_view()

    def _new_library_category(self):
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称：")
        if not ok or not name.strip():
            return
        try:
            library_svc.create_category(self.db, name)
        except ValueError as e:
            QMessageBox.warning(self, "无法新建", str(e))
            return
        self.status_lbl.setText(f"已新建分类：{name.strip()}")
        self._refresh_view()

    def _manage_library_categories(self):
        LibraryManagerDialog(self.db, self).exec()
        self._refresh_view()

    def _open_data_management(self):
        DataManagementDialog(self.db, self).exec()

    def _add_library_by_name(self):
        text, ok = QInputDialog.getText(
            self, "手动收录到工具库",
            "输入 owner/repo 或 GitHub URL（多个用空格/换行/逗号分隔）：")
        if not ok or not text.strip():
            return
        from app.services.importer import parse_repo_names
        names = parse_repo_names(text)
        if not names:
            QMessageBox.warning(self, "无法识别",
                                "没有识别到有效的 owner/repo 或 GitHub URL。")
            return
        repos = [Repo(name=n, url=f"https://github.com/{n}")
                 for n in names]
        dlg = LibraryAddDialog(self.db, repos, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = dlg.selected_ids()
        added = 0
        for name in names:
            library_svc.add_name(
                self.db, name, categories=ids, source="手动收录",
                metadata={"url": f"https://github.com/{name}"})
            added += 1
        self.status_lbl.setText(
            f"已手动收录 {added} 个项目"
            + ("（待分类）" if not ids else ""))
        self._refresh_view()

    def _batch_library_categories(self):
        pool = self._filtered()
        if not pool:
            self.status_lbl.setText("当前分类没有项目可批量调整")
            return
        # 默认勾选这些项目共有的分类，避免误改
        common: set[str] | None = None
        for r in pool:
            ids = {c["id"] for c in
                   (library_svc.entry(self.db, r.name) or {}).get("categories", [])}
            common = ids if common is None else (common & ids)
        dlg = LibraryAddDialog(self.db, pool,
                               selected_ids=sorted(common or []), parent=self)
        dlg.setWindowTitle(f"批量调整 {len(pool)} 个项目的分类")
        if dlg.exec() != QDialog.Accepted:
            return
        ids = dlg.selected_ids()
        for r in pool:
            library_svc.set_entry_categories(self.db, r.name, ids)
        self.status_lbl.setText(f"已调整 {len(pool)} 个项目的分类")
        self._refresh_view()

    def _batch_remove_library(self):
        pool = self._filtered()
        if not pool:
            self.status_lbl.setText("当前分类没有项目可移出")
            return
        ans = QMessageBox.question(
            self, "批量移出工具库",
            f"把当前 {len(pool)} 个项目全部移出工具库？\n"
            "收藏、追踪、备注和历史都会保留。")
        if ans != QMessageBox.Yes:
            return
        for r in pool:
            library_svc.remove_entry(self.db, r.name)
        self.status_lbl.setText(f"已移出 {len(pool)} 个项目")
        self._refresh_view()

    # ================= GH Archive 回填 =================
    def _start_gha_backfill(self):
        if self._gha_backfill is not None:
            self._gha_backfill.stop()
            self.status_lbl.setText("🗄 正在取消回填，等待当前小时结束…")
            self.gha_btn.setEnabled(False)
            return
        days = self.days
        from app.services import gha_board
        hours = gha_board.archive_coverage(self.db, days)["expected"] or days * 24
        ans = QMessageBox.question(
            self, "补齐 GH Archive",
            f"将下载最近 {days} 天约 {hours} 个小时包（约 "
            f"{hours * 5 / 1024:.1f} GB，视压缩率而定）。\n"
            "已下载的小时会跳过；可随时取消，已完成部分保留。\n继续吗？")
        if ans != QMessageBox.Yes:
            return
        worker = GhaBackfillThread(self.db, days, self)
        worker.status.connect(self._on_gha_status)
        worker.finished_stats.connect(self._on_gha_backfill_done)
        self._gha_backfill = worker
        self._tasks.track(worker)
        self.gha_btn.setEnabled(True)
        self.gha_btn.setText("⏹ 取消回填")
        self.status_lbl.setText(f"🗄 开始回填近 {days} 天 GH Archive…")
        worker.start()

    def _on_gha_backfill_done(self, stats: dict):
        self._gha_backfill = None
        self.gha_btn.setEnabled(True)
        self.gha_btn.setText(f"🗄 补齐近{self.days}天 GH Archive")
        if stats.get("error"):
            self.status_lbl.setText(f"GH Archive 回填失败：{stats['error']}")
            return
        self.status_lbl.setText(
            f"🗄 回填{'已取消' if stats.get('cancelled') else '完成'}：新增 "
            f"{stats.get('downloaded', 0)}，跳过 {stats.get('skipped', 0)}，"
            f"失败 {stats.get('failed', 0)}；正在重建榜单…")
        if self.board == "hot" and self._is_gha_source():
            self._start_fetch(config.HOT_KEY)
        else:
            self._refresh_view()

    # ================= 抓取 =================
    def _start_fetch(self, only: str | None):
        if self._closing:
            return
        if self._worker is not None:
            # 此前静默忽略，用户会以为"点了没反应/抓取失败"
            self.status_lbl.setText(
                "⏳ 抓取正在进行中，请等待完成，或先点「⏹ 取消」")
            return
        self._worker = FetchWorker(self.db, only=only, parent=self)
        self._worker.progress.connect(self.status_lbl.setText)
        self._worker.channel_done.connect(self._on_channel_done)
        self._worker.finished_all.connect(self._on_fetch_done)
        self._tasks.track(self._worker)
        self.fetch_btn.setEnabled(False)
        self.fetch_cur_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._refresh_view()     # 触发骨架屏（若无数据）
        self._worker.start()

    def _cancel_fetch(self):
        if self._worker is not None:
            self._worker.cancel.set()
            self.cancel_btn.setEnabled(False)
            self.status_lbl.setText("正在取消…")

    def _fetch_current(self):
        if self.board != "hot":
            self.status_lbl.setText("当前视图不支持抓取通道")
            return
        if (self.domain_filter or self.lang_filter) and not self._api_enabled():
            self.status_lbl.setText(
                "GitHub API 源已默认禁用：语言/领域通道不可抓取；"
                "可在设置中启用，或只抓热门/ Trending/GH Archive")
            return
        if self.domain_filter:
            self._start_fetch(config.DOMAIN_PREFIX + self.domain_filter)
        elif self.lang_filter:
            self._start_fetch(self.lang_filter)
        else:
            self._start_fetch(config.HOT_KEY)

    def _on_channel_done(self, days: int, channel: str):
        self._pools.pop((days, channel), None)
        self._pool(days, channel)          # 立即重载
        self._refresh_sidebar()            # 侧栏计数实时跟进（含非热门板）
        if days == self.days and self.board == "hot":
            self._refresh_view()

    def _on_fetch_done(self, ok: int, fail: int, failed: list):
        worker = self._worker
        self._worker = None
        if worker:
            worker.deleteLater()
        self.fetch_btn.setEnabled(True)
        self.fetch_cur_btn.setEnabled(self.board == "hot")
        self.cancel_btn.setEnabled(False)
        msg = f"抓取完成：成功 {ok} 通道"
        if fail:
            msg += f"，失败 {fail}（{'、'.join(failed[:5])}）"
        # 额度耗尽：状态栏明确提示恢复时间，别让用户误以为程序坏了
        rl = getattr(worker, "rate_limit_reset", None)
        if rl:
            import time as _t
            when = _t.strftime("%H:%M", _t.localtime(rl))
            wait = max(0, int((rl - _t.time()) / 60))
            msg += f" · ⚠️ API 额度已用完，预计 {when} 恢复（约 {wait} 分钟）"
        elif fail:
            msg += " · 若大量 403/限流，可在设置中配置 GitHub Token 提额"
        self._refresh_view()
        # 刷新可能覆盖状态栏（如空态提示），结果汇总必须最后写
        self.status_lbl.setText(msg)
        if fail and not ok:
            QMessageBox.warning(self, "抓取失败",
                                "全部通道失败，请检查网络或配置 GITHUB_TOKEN。")
        # 至少一个通道成功才推进定时抓取时间；全失败不推进（防虚假延后）
        if ok:
            import time as _time
            self.db.set_setting("__last_fetch__", _time.time())
            self._check_updates(silent=True)

    # ================= 收藏刷新 + 里程碑 =================
    def _refresh_favorites(self):
        if self._closing or self._fav_worker is not None:
            return
        if not self._api_enabled():
            self.status_lbl.setText(
                "GitHub API 源已默认禁用；刷新收藏需要 API，请在设置中启用")
            return
        if not self.db.list_favorites():
            self.status_lbl.setText("还没有收藏项目")
            return
        self._fav_worker = FavRefreshWorker(self.db, parent=self)
        self._fav_worker.progress.connect(self.status_lbl.setText)
        self._fav_worker.milestone_hit.connect(self._on_milestone)
        self._fav_worker.finished_all.connect(self._on_fav_refreshed)
        self._tasks.track(self._fav_worker)
        self.fav_refresh_btn.setEnabled(False)
        self._fav_worker.start()

    def _on_fav_refreshed(self, ok: int, fail: int):
        worker = self._fav_worker
        self._fav_worker = None
        if worker:
            worker.deleteLater()
        self.fav_refresh_btn.setEnabled(True)
        self.status_lbl.setText(f"收藏刷新完成：{ok} 成功" +
                                (f"，{fail} 失败" if fail else ""))
        if self.board == "fav":
            self._refresh_view()

    def _on_milestone(self, name: str, tier: int, stars: int):
        r = QMessageBox.question(
            self, "🎉 里程碑",
            f"{name} 突破 {tier:,} Star（当前 {stars:,}）！\n\n"
            f"是否把收藏基准更新为当前 Star 数？",
            QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes:
            self.db.reset_favorite_baseline(name, stars)

    # ================= 观察名单 =================
    def _add_watch(self):
        if not self._api_enabled():
            self.status_lbl.setText(
                "GitHub API 源已默认禁用；按仓库添加观察需要 API，请在设置中启用")
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "添加观察项目", "输入 owner/repo（如 ggml-org/llama.cpp）：")
        if not ok or not name.strip():
            return
        name = name.strip()
        # 网络请求放工作线程，避免阻塞 UI
        from PySide6.QtCore import QThread

        class _AddWorker(QThread):
            done = None  # 用实例属性传递结果

            def run(self_):
                try:
                    self_.done = ("ok", watchlist.add_manual(self.db, name))
                except Exception as ex:
                    self_.done = ("err", str(ex))

        self._add_worker = _AddWorker(self)
        self._add_worker.finished.connect(self._on_watch_added)
        self._tasks.track(self._add_worker)
        self.status_lbl.setText(f"正在查询 {name} ...")
        self.watch_add_btn.setEnabled(False)
        self._add_worker.start()

    def _on_watch_added(self):
        # 类属性 done=None 会遮蔽 getattr 默认值，用 or 兜底
        status, msg = getattr(self._add_worker, "done", None) or ("err", "未知错误")
        self._add_worker.deleteLater()
        self._add_worker = None
        self.watch_add_btn.setEnabled(True)
        self.status_lbl.setText(msg)
        if status == "err":
            QMessageBox.warning(self, "添加失败", msg)
        if self.board == "watch":
            self._refresh_view()

    # ================= 托盘 + 更新通知 =================
    def _setup_tray(self):
        from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
        from PySide6.QtWidgets import QSystemTrayIcon
        px = QPixmap(32, 32)
        px.fill(QColor(self.C["accent_soft"]))
        p = QPainter(px)
        p.setPen(QColor("#ffffff"))
        p.drawText(px.rect(), Qt.AlignCenter, "🔥")
        p.end()
        self.tray = QSystemTrayIcon(QIcon(px), self)
        self.tray.setToolTip("GitHub 热点项目浏览器")
        self.tray.activated.connect(
            lambda _r: (self.showNormal(), self.activateWindow()))
        self.tray.show()

    def _check_updates(self, silent: bool):
        if not self._api_enabled():
            if not silent:
                self.status_lbl.setText(
                    "GitHub API 源已默认禁用；检查更新需要 API，请在设置中启用")
            return
        if self._closing or self._upd_worker is not None:
            return
        hours = float(self.db.get_setting("update_interval_hours", 12) or 12)
        if silent and not check_due(self.db, hours):
            return
        self._upd_worker = UpdateCheckWorker(self.db, parent=self)
        self._upd_worker.progress.connect(self.status_lbl.setText)
        self._upd_worker.changes_found.connect(
            lambda changes: self._on_updates_found(changes, silent))
        self._upd_worker.finished_all.connect(self._on_updates_done)
        self._tasks.track(self._upd_worker)
        self.upd_btn.setEnabled(False)
        if not silent:
            self.status_lbl.setText("正在检查更新…")
        self._upd_worker.start()

    def _on_updates_found(self, changes: list, silent: bool):
        from PySide6.QtWidgets import QSystemTrayIcon
        text = "\n".join(f"• {n}：{d}" for n, d in changes[:10])
        self.tray.showMessage("跟踪项目有更新",
                              f"{len(changes)} 个项目有变化",
                              QSystemTrayIcon.MessageIcon.Information, 5000)
        if not silent:
            QMessageBox.information(
                self, "🔔 更新提醒",
                f"检出 {len(changes)} 个项目有变化：\n\n{text}"
                + ("\n……" if len(changes) > 10 else ""))

    def _on_updates_done(self, n: int):
        worker = self._upd_worker
        self._upd_worker = None
        if worker:
            worker.deleteLater()
        self.upd_btn.setEnabled(True)
        self.status_lbl.setText(f"更新检查完成（{n} 个项目）")

    # ================= 名单导入 =================
    def _import_watchlist(self):
        dlg = ImportDialog(self)
        if not dlg.exec():
            return
        names = dlg.names()
        if not names:
            QMessageBox.information(self, "导入名单", "未识别到任何 owner/repo")
            return
        from PySide6.QtCore import QThread

        class _ImportWorker(QThread):
            def __init__(self_, *a, **kw):
                super().__init__(*a, **kw)
                # 独立的取消信号：此前误用抓取线程的 cancel，关闭/抓取取消会互相干扰
                self_.cancel = threading.Event()

            def run(self_):
                try:
                    self_.result = ("ok", import_names(
                        self.db, names,
                        log=lambda m: self.status_lbl.setText(m),
                        cancel=self_.cancel))
                except Exception as ex:
                    self_.result = ("err", str(ex))

        self._imp_worker = _ImportWorker(self)
        self._imp_worker.finished.connect(self._on_import_done)
        self._tasks.track(self._imp_worker)
        self.status_lbl.setText(f"正在导入 {len(names)} 个项目…")
        self._imp_worker.start()

    def _on_import_done(self):
        status, result = getattr(self._imp_worker, "result", None) or ("err", "未知错误")
        self._imp_worker.deleteLater()
        self._imp_worker = None
        if status == "err":
            QMessageBox.warning(self, "导入失败", str(result))
            return
        added, skipped, failed = result
        self.status_lbl.setText(
            f"导入完成：新增 {added}，已存在 {skipped}，失败 {failed}")
        if self.board == "watch":
            self._refresh_view()

    # ================= 历史快照 =================
    def _open_history(self):
        if self.board != "hot":
            self.status_lbl.setText("历史快照仅适用于本周热门榜")
            return
        # 选中语言/领域时看对应通道历史（对齐原版），否则看热门榜通道
        if self.domain_filter:
            ch = config.DOMAIN_PREFIX + self.domain_filter
        elif self.lang_filter:
            ch = self.lang_filter
        else:
            ch = self._hot_channel()
        dlg = HistoryDialog(self.db, ch, self.days, self)
        if not dlg.exec() or dlg.selected_id is None:
            return
        repos = self.db.load_history_by_id(dlg.selected_id)
        if not repos:
            self.status_lbl.setText("快照读取失败")
            return
        self._snapshot_override = repos
        self._refresh_view()
        self.status_lbl.setText(
            f"正在回看历史快照（{len(repos)} 条）· 点「回到最新」恢复")
        self._show_back_to_latest()

    def _show_back_to_latest(self):
        btn = QPushButton("⏪ 回到最新", objectName="backBtn")
        btn.setProperty("class", "accent")
        btn.clicked.connect(self._back_to_latest)
        self.cards_lay.insertWidget(self.cards_lay.count() - 1, btn)

    def _back_to_latest(self):
        self._snapshot_override = None
        self._refresh_view()

    # ================= 导出 =================
    def _export_view(self):
        from PySide6.QtWidgets import QFileDialog
        pool = self._filtered()
        if not pool:
            self.status_lbl.setText("当前视图无数据可导出")
            return
        board_label = {"hot": "本周热门", "fav": "我的收藏",
                       "watch": "增长观察"}[self.board]
        path, filt = QFileDialog.getSaveFileName(
            self, "导出当前视图", f"export_{self.board}_{self.days}d",
            "Markdown (*.md);;CSV (*.csv);;HTML (*.html)")
        if not path:
            return
        try:
            if path.endswith(".csv"):
                exporter.to_csv(pool, path)
            elif path.endswith(".html"):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(exporter.to_html(pool, board_label, self.days,
                                             self.sort_name))
            else:
                if not path.endswith(".md"):
                    path += ".md"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(exporter.to_markdown(pool, board_label,
                                                 self.days, self.sort_name))
            self.status_lbl.setText(f"已导出 {len(pool)} 条 → {path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ================= 翻译 =================
    def _translate_card(self, card: RepoCard):
        text = card.repo.description
        cached = self.db.get_translation(text)
        if cached:
            card.set_translation(cached)
            return
        from PySide6.QtCore import QThread

        class _TrWorker(QThread):
            def run(self_):
                try:
                    self_.result = ("ok", translate_svc.translate(text, self.db))
                except Exception as ex:
                    self_.result = ("err", str(ex))

        w = _TrWorker(self)
        self._tasks.track(w)
        w.finished.connect(lambda: self._on_translated(w, card))
        self.status_lbl.setText(f"正在翻译 {card.repo.name} ...")
        w.start()

    def _on_translated(self, worker, card: RepoCard):
        status, result = getattr(worker, "result", ("err", "未知错误"))
        worker.deleteLater()
        if status == "ok":
            try:
                card.set_translation(result)
            except RuntimeError:
                pass   # 卡片可能已被刷新销毁
            self.status_lbl.setText("翻译完成")
        else:
            self.status_lbl.setText(f"翻译失败：{result}")

    # ================= 关闭（R02：等全部工作线程结束再关库） =================
    def closeEvent(self, e):
        self._closing = True
        if hasattr(self, "_sched_timer"):
            self._sched_timer.stop()
        if self._db_closed:
            e.accept()
            return
        if self._tasks.has_active():
            # 先协作取消，再异步等待；窗口隐藏但进程/连接保留，避免线程访问已关闭库
            self._tasks.cancel_all()
            self.hide()
            if hasattr(self, "tray"):
                self.tray.hide()
            self.status_lbl.setText("正在安全退出：等待后台任务结束…")
            import time
            self._shutdown_deadline = time.monotonic() + 120
            QTimer.singleShot(100, self._poll_shutdown)
            e.ignore()
            return
        self._finalize_shutdown()
        e.accept()

    def _poll_shutdown(self):
        if self._db_closed:
            return
        if not self._tasks.has_active():
            self._finalize_shutdown()
            if not self.close():
                # 窗口已隐藏时个别平台不会触发关闭事件：显式退出事件循环
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app.quit()
            return
        import time
        if time.monotonic() > self._shutdown_deadline:
            # 兜底：绝不 terminate 线程；继续等待，避免关闭后仍有写库访问
            self.status_lbl.setText(
                f"仍有 {len(self._tasks.active())} 个后台任务，继续等待…")
            self._shutdown_deadline = time.monotonic() + 120
        QTimer.singleShot(100, self._poll_shutdown)

    def _finalize_shutdown(self):
        if self._db_closed:
            return
        self._db_closed = True
        if hasattr(self, "tray"):
            self.tray.hide()
        self.db.close()
