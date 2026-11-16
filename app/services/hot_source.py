# -*- coding: utf-8 -*-
"""本周热门"当前显示哪个源"的规则（纯逻辑，UI 与抓取编排共用）。

R04：自动模式的活动源必须按时间档（7/30 天）分别记录。此前只存一个
全局 `__hot_active__`，周榜与月榜分别降级到不同源时会互相覆盖视图。
"""
from __future__ import annotations

from app.core import config
from app.core.db import Database

AUTO_ORDER = config.AUTO_SOURCE_CHAIN
LEGACY_ACTIVE_KEY = "__hot_active__"


def auto_order(db: Database) -> tuple[str, ...]:
    """API 源默认禁用：auto 只用 Trending（网页，不耗 GitHub API）。

    用户在设置显式启用 GitHub API 源后，才允许 OpenGithubs 参与 auto。
    """
    if db.get_setting("github_api_enabled", False):
        return AUTO_ORDER
    return ("trending",)


def active_key(days: int) -> str:
    return f"__hot_active_{days}d__"


def get_active(db: Database, days: int) -> str:
    """该时间档最近一次成功的数据源；无记录时兼容旧版全局键。"""
    value = db.get_setting(active_key(days), "") or ""
    return value or (db.get_setting(LEGACY_ACTIVE_KEY, "") or "")


def set_active(db: Database, days: int, source: str):
    """缓存写入成功后再调用；只影响本时间档。"""
    db.set_setting(active_key(days), source)


def channel_for(db: Database, days: int, source: str | None = None) -> str:
    """视图应读取的通道键。

    单源：该源专属键；auto：跟随本时间档最后成功的源，无记录时按降级链
    顺序取第一个有缓存的源。
    """
    src = source if source is not None else db.get_setting("hot_source", "auto")
    if src != "auto":
        return config.hot_channel(src, days)
    active = get_active(db, days)
    # 只有同口径的报告源可以被 auto 跟随；事件/搜索必须显式选择。
    # API 源默认禁用，auto 只跟随 Trending。
    order = auto_order(db)
    if active in order:
        return config.hot_channel(active, days)
    for candidate in order:
        channel = config.hot_channel(candidate, days)
        repos, _, _ = db.load_cache(channel, days)
        if repos:
            return channel
    return config.hot_channel(order[0], days)
