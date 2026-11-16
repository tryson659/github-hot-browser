# -*- coding: utf-8 -*-
"""项目卡片：悬停浮起（阴影+上移+边框发光）、入场淡入、收藏切换动画。"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QVariantAnimation,
                            Qt, Signal)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from app.core.models import Repo
from app.ui import theme as theme_mod


def _fmt(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


class RepoCard(QWidget):
    favorite_clicked = Signal(str)
    note_clicked = Signal(str)
    translate_clicked = Signal(object)   # 传卡片自身，供主窗口异步翻译
    tag_clicked = Signal(str)            # topics 标签点击即筛
    release_clicked = Signal(object)     # 🚀 查询最新 Release（传卡片自身）
    library_clicked = Signal(str)        # 加入/调整工具库分类
    library_remove_clicked = Signal(str)  # 从工具库移除

    def __init__(self, rank: int, repo: Repo, C: dict,
                 favorite: bool = False, meta: dict | None = None,
                 rank_badge: str | None = None,
                 in_library: bool = False,
                 library_categories: list[str] | None = None,
                 library_board: bool = False,
                 library_note: str = "",
                 parent=None):
        super().__init__(parent)
        self.repo = repo
        self.C = C
        self._favorite = favorite
        self._hover_t = 0.0
        self._translated: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)
        self._lay = lay

        self.frame = QFrame()
        self._shadow = QGraphicsDropShadowEffect(self.frame)
        self._shadow.setBlurRadius(10)
        self._shadow.setOffset(0, 2)
        self._shadow.setColor(QColor(0, 0, 0, 90))
        self.frame.setGraphicsEffect(self._shadow)
        lay.addWidget(self.frame)

        fl = QVBoxLayout(self.frame)
        fl.setContentsMargins(14, 10, 14, 10)
        fl.setSpacing(5)

        # ---- 第一行：排名 + 名称 + 徽章 + 右侧数据 ----
        top = QHBoxLayout()
        top.setSpacing(8)
        rank_lbl = QLabel(f"#{rank}")
        rank_lbl.setStyleSheet(f"color: {C['text2']}; font-size: 12px;")
        top.addWidget(rank_lbl)

        # 历史对比徽章（🆕新上榜 / ▲N / ▼N）
        if rank_badge:
            color = C["ok"] if (rank_badge.startswith(("🆕", "▲"))) else C["danger"]
            b = QLabel(rank_badge)
            b.setStyleSheet(f"color: {color}; font-size: 11px;")
            top.addWidget(b)

        name_lbl = QLabel(f'<a href="{repo.url}" style="color: {C["accent"]}; '
                          f'text-decoration: none; font-weight: 600;">{repo.name}</a>')
        name_lbl.setOpenExternalLinks(True)
        name_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        top.addWidget(name_lbl)

        for tag in repo.interest_tags:
            b = QLabel(f"🎯 {tag}")
            b.setStyleSheet(f"color: {C['star']}; font-size: 11px;")
            top.addWidget(b)
        if repo.archived:
            b = QLabel("⚠️ 已归档")
            b.setStyleSheet(f"color: {C['danger']}; font-size: 11px;")
            top.addWidget(b)
        growth = (repo.growth_7d if repo.growth_7d is not None
                  else repo.growth_value)
        if growth is not None:
            win = repo.growth_window or "7天"
            sign = "+" if growth > 0 else ""
            b = QLabel(f"{'📈' if growth >= 0 else '📉'} {sign}{_fmt(growth)}/{win}")
            b.setStyleSheet(
                f"color: {C['ok'] if growth >= 0 else C['danger']}; font-size: 11px;")
            top.addWidget(b)

        # 收藏基准对比徽章
        if favorite and meta and meta.get("saved_stars") is not None and repo.stars is not None:
            delta = repo.stars - (meta.get("saved_stars") or 0)
            sign = "▲" if delta >= 0 else "▼"
            color = C["ok"] if delta >= 0 else C["danger"]
            b = QLabel(f"📌 基准 {sign}{abs(delta)}")
            b.setStyleSheet(f"color: {color}; font-size: 11px;")
            top.addWidget(b)

        top.addStretch(1)

        stats = (f"⭐ {_fmt(repo.stars)}   🍴 {_fmt(repo.forks)}   "
                 f"{repo.language}   {repo.domain}")
        stats_lbl = QLabel(stats)
        stats_lbl.setStyleSheet(f"color: {C['text2']}; font-size: 12px;")
        top.addWidget(stats_lbl)
        fl.addLayout(top)

        # ---- 描述 ----
        desc = repo.description or "（无描述）"
        self.desc_lbl = QLabel(desc)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet(f"color: {C['text']}; font-size: 12.5px;")
        fl.addWidget(self.desc_lbl)

        # ---- 底部行：topics（可点击即筛）+ 备注 + 操作按钮 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        for t in (repo.topics or [])[:5]:
            chip = QPushButton(t[:18])
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f"按标签 {t} 筛选")
            chip.setStyleSheet(
                f"QPushButton {{ background: {C['hover_soft']}; color: {C['text2']};"
                f" border: none; border-radius: 8px; padding: 1px 8px;"
                f" font-size: 11px; }}"
                f"QPushButton:hover {{ color: {C['accent']}; }}")
            chip.clicked.connect(lambda _=False, tag=t: self.tag_clicked.emit(tag))
            bottom.addWidget(chip)
        if favorite and meta and meta.get("note"):
            note_lbl = QLabel("📝 " + meta["note"][:60])
            note_lbl.setStyleSheet(f"color: {C['star']}; font-size: 11px;")
            bottom.addWidget(note_lbl)
        if library_categories:
            cat_lbl = QLabel("🧰 " + "、".join(library_categories[:3]))
            cat_lbl.setStyleSheet(f"color: {C['accent']}; font-size: 11px;")
            cat_lbl.setToolTip("、".join(library_categories))
            bottom.addWidget(cat_lbl)
        elif library_note:
            note_lbl = QLabel("🧰 " + library_note[:50])
            note_lbl.setStyleSheet(f"color: {C['text2']}; font-size: 11px;")
            bottom.addWidget(note_lbl)
        bottom.addStretch(1)

        self.lib_btn = QPushButton("🗂 分类" if in_library else "＋ 工具库")
        self.lib_btn.setCursor(Qt.PointingHandCursor)
        self.lib_btn.setToolTip("加入工具库或调整分类（不影响收藏/追踪）")
        self.lib_btn.setStyleSheet(
            f"background: {C['btn']}; color: {C['text']}; border: none;"
            f" border-radius: 6px; padding: 3px 10px;")
        self.lib_btn.clicked.connect(
            lambda: self.library_clicked.emit(repo.name))
        bottom.addWidget(self.lib_btn)
        if library_board and in_library:
            rm_btn = QPushButton("移出")
            rm_btn.setCursor(Qt.PointingHandCursor)
            rm_btn.setToolTip("从工具库移除；收藏、追踪、备注和历史保留")
            rm_btn.setStyleSheet(
                f"background: {C['btn']}; color: {C['danger']}; border: none;"
                f" border-radius: 6px; padding: 3px 10px;")
            rm_btn.clicked.connect(
                lambda: self.library_remove_clicked.emit(repo.name))
            bottom.addWidget(rm_btn)

        self.fav_btn = QPushButton("★ 已收藏" if favorite else "☆ 收藏")
        self.fav_btn.setProperty("class", "flat")
        self.fav_btn.setCursor(Qt.PointingHandCursor)
        self.fav_btn.setStyleSheet(
            f"background: {'#3d2e00' if favorite else C['btn']};"
            f" color: {C['star'] if favorite else C['text']};"
            f" border: none; border-radius: 6px; padding: 3px 10px;")
        self.fav_btn.clicked.connect(lambda: self.favorite_clicked.emit(repo.name))
        bottom.addWidget(self.fav_btn)

        if favorite:
            note_btn = QPushButton("📝 备注")
            note_btn.setCursor(Qt.PointingHandCursor)
            note_btn.setStyleSheet(
                f"background: {C['btn']}; color: {C['text']}; border: none;"
                f" border-radius: 6px; padding: 3px 10px;")
            note_btn.clicked.connect(lambda: self.note_clicked.emit(repo.name))
            bottom.addWidget(note_btn)

        rel_btn = QPushButton("🚀")
        rel_btn.setCursor(Qt.PointingHandCursor)
        rel_btn.setToolTip("查询最新 Release")
        rel_btn.setStyleSheet(
            f"background: {C['btn']}; color: {C['text']}; border: none;"
            f" border-radius: 6px; padding: 3px 10px;")
        rel_btn.clicked.connect(lambda: self.release_clicked.emit(self))
        bottom.addWidget(rel_btn)
        self.rel_btn = rel_btn

        if repo.description:
            self.tr_btn = QPushButton("译")
            self.tr_btn.setCursor(Qt.PointingHandCursor)
            self.tr_btn.setToolTip("翻译成中文")
            self.tr_btn.setStyleSheet(
                f"background: {C['btn']}; color: {C['text']}; border: none;"
                f" border-radius: 6px; padding: 3px 10px;")
            self.tr_btn.clicked.connect(
                lambda: self.translate_clicked.emit(self))
            bottom.addWidget(self.tr_btn)

        open_btn = QPushButton("🔗 打开")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.setStyleSheet(
            f"background: {C['btn']}; color: {C['text']}; border: none;"
            f" border-radius: 6px; padding: 3px 10px;")
        open_btn.clicked.connect(lambda: webbrowser.open(repo.url))
        bottom.addWidget(open_btn)
        fl.addLayout(bottom)

        self._apply_style(0.0)

        # 悬停动画：t ∈ [0,1]，控制上移/阴影/边框
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    # ---------- 主题 / 动画 ----------
    def _apply_style(self, t: float):
        C = self.C
        bg = theme_mod.mix(C["card"], C["card_hover"], t)
        border = theme_mod.mix(C["border"], C["accent"], t * 0.85)
        self.frame.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border};"
            f" border-radius: 10px; }}")
        self._shadow.setBlurRadius(10 + 16 * t)
        self._shadow.setOffset(0, 2 + 3 * t)
        self._lay.setContentsMargins(8, 4 - 3 * t, 8, 4 + 3 * t)

    def _on_anim(self, v):
        self._hover_t = float(v)
        self._apply_style(self._hover_t)

    def enterEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._hover_t)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._hover_t)
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(e)

    def fade_in(self, delay_ms: int = 0):
        """入场淡入。透明度作用在外层容器上，避免替换 frame 上的阴影效果
        （setGraphicsEffect 会删除旧效果，直接换会导致阴影对象被销毁）。"""
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(260)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: self.setGraphicsEffect(None))
        self._fade_anim = anim  # 防 GC
        if delay_ms:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(delay_ms, anim.start)
        else:
            anim.start()

    def set_favorite(self, fav: bool):
        self._favorite = fav
        C = self.C
        self.fav_btn.setText("★ 已收藏" if fav else "☆ 收藏")
        self.fav_btn.setStyleSheet(
            f"background: {'#3d2e00' if fav else C['btn']};"
            f" color: {C['star'] if fav else C['text']};"
            f" border: none; border-radius: 6px; padding: 3px 10px;")

    def set_translation(self, text: str):
        """翻译完成后回填描述（就地更新，不重建卡片）。"""
        self._translated = text
        self.desc_lbl.setText(text)
        self.desc_lbl.setStyleSheet(
            f"color: {self.C['text']}; font-size: 12.5px; font-style: italic;")
        if hasattr(self, "tr_btn"):
            self.tr_btn.setEnabled(False)
            self.tr_btn.setText("✓")

    def set_release(self, tag: str, date: str):
        """Release 查询完成回填。"""
        self.rel_btn.setText(f"🚀 {tag}")
        self.rel_btn.setToolTip(f"最新 Release：{tag}（{date}）")
        self.rel_btn.setStyleSheet(
            f"background: {self.C['btn']}; color: {self.C['ok']}; border: none;"
            f" border-radius: 6px; padding: 3px 10px;")


class SkeletonCard(QWidget):
    """骨架占位卡：抓取中脉动呼吸。"""

    def __init__(self, C: dict, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        frame = QFrame()
        frame.setFixedHeight(86)
        frame.setStyleSheet(
            f"QFrame {{ background: {C['skeleton']}; border-radius: 10px;"
            f" border: 1px solid {C['border']}; }}")
        lay.addWidget(frame)
        eff = QGraphicsOpacityEffect(frame)
        frame.setGraphicsEffect(eff)
        self.anim = QPropertyAnimation(eff, b"opacity", self)
        self.anim.setDuration(750)
        self.anim.setStartValue(0.35)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.InOutSine)
        self.anim.setLoopCount(-1)
        # 往返脉动
        self.anim.setDirection(QPropertyAnimation.Forward)
        self.anim.finished.connect(self._reverse)
        self.anim.start()

    def _reverse(self):
        self.anim.setDirection(
            QPropertyAnimation.Backward
            if self.anim.direction() == QPropertyAnimation.Forward
            else QPropertyAnimation.Forward)
        self.anim.start()

    def stop(self):
        self.anim.stop()
