# -*- coding: utf-8 -*-
"""增长观察榜服务：采样记录 + 按窗口的日均增长计算。

采样时机：抓取完成时（自动收录全部池项目）、收藏刷新时、手动添加时。
增长口径见 `app.services.growth`：按请求窗口取基线，真实统计采样跨度，
不足窗口或最新样本滞后时明确标记，不再把长期平均外推成"周增长"。
"""
from __future__ import annotations

from datetime import datetime

from app.core.db import Database
from app.core.models import Repo
from app.services import growth as growth_mod


def record_samples(db: Database, repos: list[Repo]):
    """把一批项目收录进观察名单（非手动）并记录今日采样。"""
    today = datetime.now().strftime("%Y-%m-%d")
    for r in repos:
        if r.stars is None:
            continue
        db.watch_add(r.name, url=r.url, desc=r.description,
                     lang=r.language, manual=False)
        db.watch_sample(r.name, r.stars, today)
    db.watch_cleanup(60)


def daily_growth(db: Database, name: str, window: int = 7) -> tuple[float, int]:
    """返回 (日均增长, 有效采样天数)；样本不足返回 (0.0, 0)。"""
    g = growth_mod.compute_growth(db.watch_samples(name, days=60), window=window)
    if g is None:
        return 0.0, 0
    return g.daily, g.span_days


def add_manual(db: Database, name: str) -> str:
    """手动添加观察项目（联网查元数据，须在工作线程调用）。

    返回状态信息字符串；失败抛异常。"""
    name = name.strip().removesuffix(".git").strip("/")
    if "/" not in name or name.count("/") != 1:
        raise ValueError(f"格式应为 owner/repo：{name}")
    from app.sources import github_search
    info = github_search.fetch_repo(name)
    db.watch_add(name, url=f"https://github.com/{name}",
                 desc=info["description"], lang=info["language"], manual=True)
    db.watch_sample(name, info["stars"])
    return f"已加入观察：{name}（⭐{info['stars']}）"


def board_rows(db: Database) -> list[Repo]:
    """观察榜：全部观察项目按日均增长排序（转化为 Repo 供卡片渲染）。"""
    out: list[Repo] = []
    for w in db.watch_list():
        samples = db.watch_samples(w["name"])
        if not samples:
            continue
        latest_stars = samples[sorted(samples)[-1]]
        g = growth_mod.compute_growth(samples, window=7)
        r = Repo(
            name=w["name"], url=w["url"] or f"https://github.com/{w['name']}",
            stars=latest_stars, language=w["lang"] or "-",
            description=w["desc"] or "",
            growth_daily=g.daily if g else 0.0,
            # 只有精确覆盖 7 天采样才写 7 天增长；长期平均/短样本不外推
            growth_7d=(g.delta if g and g.exact and g.span_days == 7 else None),
            growth_value=(g.delta if g else None),
            growth_window=(g.label if g else ""),
            coverage="",
            metric="net_stars",
            window_days=(g.span_days if g else 0),
            window_start=(g.baseline_date if g else ""),
            window_end=(g.latest_date if g else ""),
            completeness=("valid" if g and g.exact and g.span_days == 7
                          else "partial" if g else "missing"),
            source="手动添加" if w["manual"] else "自动收录",
        )
        out.append(r)
    out.sort(key=lambda r: -r.growth_daily)
    return out
