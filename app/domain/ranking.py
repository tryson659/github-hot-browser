# -*- coding: utf-8 -*-
"""排序规则（技术路线 6.3）。

先按 metric/window/质量筛选，再按窗口值降序；同分按稳定仓库标识排序；
负值自然排在零之后；缺测、过期数据单独放在末尾，不用 0 冒充。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.metrics import Completeness, Metric


@dataclass(frozen=True)
class RankCandidate:
    repo_key: str
    value: int | None
    metric: Metric
    window_days: int
    completeness: Completeness = Completeness.VALID
    stale: bool = False
    source: str = ""
    tie_break: str = ""

    @property
    def comparable(self) -> bool:
        return (self.value is not None and not self.stale
                and self.completeness is Completeness.VALID)


def rank_candidates(items: list[RankCandidate],
                    metric: Metric | None = None,
                    days: int | None = None,
                    include_partial: bool = False,
                    include_missing: bool = True) -> list[RankCandidate]:
    """返回可比值在前、缺测/过期在后；同分按 tie_break 或 repo_key 稳定排序。"""
    scoped = [c for c in items
              if (metric is None or c.metric is metric)
              and (days is None or c.window_days == days)]

    usable: list[RankCandidate] = []
    missing: list[RankCandidate] = []
    for c in scoped:
        if c.value is None or c.stale:
            missing.append(c)
        elif c.completeness is Completeness.VALID:
            usable.append(c)
        elif c.completeness is Completeness.PARTIAL and include_partial:
            usable.append(c)
        else:
            missing.append(c)

    usable.sort(key=lambda c: (-(c.value or 0), c.tie_break or c.repo_key))
    missing.sort(key=lambda c: (c.tie_break or c.repo_key))
    return usable + missing if include_missing else usable
