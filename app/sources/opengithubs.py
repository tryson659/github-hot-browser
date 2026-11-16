# -*- coding: utf-8 -*-
"""数据源：OpenGithubs 周榜/月榜（真实 Star 增量）。

榜单仓库：OpenGithubs/github-weekly-rank、OpenGithubs/github-monthly-rank
通过 contents API 自动发现最新 年/月/榜单文件，解析表格行 + 明细块。
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timezone

from app.core.models import Repo
from app.core.classify import classify_domain, match_interest
from app.sources import http


def _star_int(s: str) -> int:
    return int(float(s[:-1]) * 1000) if s.lower().endswith("k") else int(float(s))


def fetch_rank(days: int, interests: dict | None = None) -> tuple[list[Repo], str]:
    """返回 (repos, 榜单期标签)。days=7 周榜，否则月榜。"""
    if days == 7:
        repo, kind, label = "OpenGithubs/github-weekly-rank", "周", "周榜"
    else:
        repo, kind, label = "OpenGithubs/github-monthly-rank", "月", "月榜"

    years = sorted((x["name"] for x in http.github_api(f"repos/{repo}/contents/")
                    if x["type"] == "dir" and x["name"].isdigit()), reverse=True)
    if not years:
        raise RuntimeError("榜单仓库无年份目录")
    year = years[0]
    if days == 7:
        months = sorted((x["name"] for x in http.github_api(f"repos/{repo}/contents/{year}")
                         if x["type"] == "dir" and x["name"].isdigit()), reverse=True)
        if not months:
            raise RuntimeError("周榜无月份目录")
        files = sorted((x["name"] for x in http.github_api(
            f"repos/{repo}/contents/{year}/{months[0]}")
            if x["name"].endswith(".md")), reverse=True)
        if not files:
            raise RuntimeError("周榜无榜单文件")
        rel = f"{year}/{months[0]}/{files[0]}"
        period = files[0][:-3]
    else:
        months = sorted((x["name"] for x in http.github_api(f"repos/{repo}/contents/{year}")
                         if x["name"].endswith(".md")), reverse=True)
        if not months:
            raise RuntimeError("月榜无榜单文件")
        rel = f"{year}/{months[0]}"
        stem = months[0][:-3]              # 202610
        period = f"{year}{stem[-2:].zfill(2)}01"

    d = http.github_api(f"repos/{repo}/contents/{rel}")
    text = base64.b64decode(d["content"]).decode("utf-8")

    rows = re.findall(
        r"\|\s*(\d+)\s*\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*([\d.]+k?)\s*\|\s*🔺?([\d,]+)\s*\|",
        text)
    dates = re.findall(r"📅\s*开源时间：\s*(\d{4}-\d{2}-\d{2})", text)
    descs = re.findall(r"📝\s*项目描述：\s*(.+)", text)

    repos: list[Repo] = []
    for i, (_, name, url, stars, inc) in enumerate(rows):
        inc_n = int(inc.replace(",", ""))
        desc = descs[i].strip() if i < len(descs) else ""
        created = dates[i] if i < len(dates) else ""
        if created:
            age = max((datetime.now(timezone.utc) -
                       datetime.fromisoformat(created + "T00:00:00+00:00")).days, 1)
        else:
            age = 30
        tags, hits = match_interest(name, desc, interests)
        repos.append(Repo(
            name=name, url=url, stars=_star_int(stars), forks=0,
            language="-", description=desc, created=created, pushed="-",
            domain=classify_domain(name, desc),
            growth_daily=round(inc_n / (7 if days == 7 else 30), 1),
            growth_7d=inc_n if days == 7 else None,
            growth_value=inc_n, growth_window=f"{label} {period}",
            metric="provider_reported",
            window_days=days,
            completeness="partial",     # 榜单期近似，且缺语言/forks/topics
            interest_tags=tags, interest_hits=hits,
            source=f"OpenGithubs{label}",
        ))
    if not repos:
        raise RuntimeError(f"OpenGithubs{label}解析为空")
    return repos, f"{label} {period}"
