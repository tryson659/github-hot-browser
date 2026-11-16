# -*- coding: utf-8 -*-
"""领域层：指标、时间窗口、排序与仓库身份规则（纯 Python）。

本包不导入 Qt、SQLite、urllib 或上层服务，便于用固定时钟和样本独立验证。
"""
from app.domain.metrics import (
    METRIC_LABELS,
    Completeness,
    Granularity,
    Metric,
    WindowSpec,
    metric_label,
    rolling_window,
)
from app.domain.ranking import RankCandidate, rank_candidates
from app.domain.windows import WindowResult, history_growth, net_growth

__all__ = [
    "METRIC_LABELS", "Completeness", "Granularity", "Metric", "WindowSpec",
    "metric_label", "rolling_window", "RankCandidate", "rank_candidates",
    "WindowResult", "history_growth", "net_growth",
]
