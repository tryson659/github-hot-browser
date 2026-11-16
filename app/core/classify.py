# -*- coding: utf-8 -*-
"""领域分类与兴趣匹配（纯函数，无状态）。

放在 core 层：sources（数据源）与 services（业务）都可依赖 core，
避免 sources → services 的反向依赖。
"""
from __future__ import annotations

from app.core.config import DOMAIN_RULES, DOMAIN_DEFAULT, INTEREST_KEYWORDS


def classify_domain(name: str, desc: str) -> str:
    text = f" {name.lower()} {desc.lower()} "
    for domain, kws in DOMAIN_RULES:
        for kw in kws:
            if kw in text:
                return domain
    return DOMAIN_DEFAULT


def match_interest(name: str, desc: str,
                   interests: dict | None = None) -> tuple[list[str], int]:
    """返回 (命中的方向标签列表, 命中词总数)。"""
    interests = interests or INTEREST_KEYWORDS
    text = f" {name.lower()} {desc.lower()} "
    tags, hits = [], 0
    for label, kws in interests.items():
        n = sum(1 for kw in kws if kw.lower() in text)
        if n:
            tags.append(label)
            hits += n
    return tags, hits
