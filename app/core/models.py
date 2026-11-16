# -*- coding: utf-8 -*-
"""数据模型：统一的项目（Repo）结构。

所有数据源（搜索/榜单/Trending）都归一化为 Repo，UI 与存储只面向它。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Repo:
    name: str                       # owner/repo
    url: str
    stars: int | None = None
    forks: int | None = None
    language: str = "-"
    description: str = ""
    created: str = ""               # YYYY-MM-DD
    pushed: str = "-"
    topics: list[str] = field(default_factory=list)
    domain: str = "其他"
    license: str = "-"
    archived: bool = False
    growth_daily: float = 0.0       # 日均（由来源口径决定，可能为近似值）
    growth_7d: int | None = None    # 近7天真实增长（快照差值或榜单数据）
    growth_value: int | None = None # 当前窗口/报告期的原始增量
    growth_window: str = ""         # 增量窗口标签（周/月/本周/本月）
    coverage: str = ""              # 样本覆盖范围（GH Archive 等非完整窗口）
    metric: str = ""                # 指标口径（net_stars/...；空=旧数据未知）
    window_days: int = 0            # 该值对应的窗口天数（0=未知/报告期）
    window_start: str = ""          # 实际观测起始日 YYYY-MM-DD（未知留空）
    window_end: str = ""            # 实际观测结束日
    completeness: str = ""          # valid / partial / missing
    interest_tags: list[str] = field(default_factory=list)
    interest_hits: int = 0
    source: str = ""                # 数据来源标签

    def window_value(self, days: int) -> int | None:
        """当前时间档可比较的窗口值；不可比时返回 None，不用 0 顶替。

        - net_stars 只认精确的 7 天快照差值（追踪榜的跨窗口值不冒充周增长）；
        - 报告/事件/历史指标使用随缓存一起保存的 growth_value；
        - 旧数据无 metric 时按 growth_value、growth_7d 顺序兼容。
        """
        if self.metric == "net_stars":
            return self.growth_7d if days == 7 else None
        if self.window_days and self.window_days != days:
            return None
        if self.growth_value is not None:
            return self.growth_value
        if days == 7:
            return self.growth_7d
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Repo":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def sort_key(name: str, days: int = 7):
    """排序键函数工厂；缺测/不可比值一律排最后，负值保留。"""
    if name == "近7天增长":
        def value(r):
            v = r.window_value(days)
            return v if v is not None else float("-inf")
        return value
    if name == "日均增长":
        return lambda r: r.growth_daily or 0
    if name == "兴趣匹配":
        return lambda r: r.interest_hits or 0
    if name == "Fork 数":
        return lambda r: r.forks or 0
    if name == "最近更新":
        return lambda r: r.pushed or ""
    if name == "总 Star":
        return lambda r: r.stars if r.stars is not None else float("-inf")
    if name == "最近收录":
        return lambda r: getattr(r, "library_added_at", "") or ""
    raise ValueError(f"未知排序: {name}")
