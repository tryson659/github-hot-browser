# -*- coding: utf-8 -*-
"""数据源：GitHub Search API（语言通道 / 领域定向通道 / 热门近似 / 新晋）。

口径与旧版一致：
- 语言/领域/新晋：created:>since + 条件，按 Star 排序（新建仓库榜）
- 热门近似 hot：pushed:>since stars:>50（近期活跃，不限创建时间）
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from app.core.models import Repo
from app.core.classify import classify_domain, match_interest
from app.sources import http


def enrich(r: dict, lang: str, interests: dict | None = None) -> Repo:
    """搜索只负责发现候选与补元数据，不把"总 Star / 项目年龄"当窗口增长。"""
    desc = (r.get("description") or "").strip()
    tags, hits = match_interest(r["full_name"], desc, interests)
    return Repo(
        name=r["full_name"], url=r["html_url"],
        stars=r.get("stargazers_count"), forks=r.get("forks_count"),
        language=r.get("language") or lang,
        description=desc,
        created=(r.get("created_at") or "")[:10],
        pushed=(r.get("pushed_at") or "")[:10],
        topics=r.get("topics") or [],
        domain=classify_domain(r["full_name"], desc),
        license=(r.get("license") or {}).get("spdx_id") or "-",
        archived=bool(r.get("archived")),
        growth_7d=None,
        completeness="missing",       # 尚未做窗口测量
        interest_tags=tags, interest_hits=hits,
        source="GitHub Search",
    )


def search(days: int, page: int = 1, lang: str | None = None,
           free_text: str | None = None, hot: bool = False) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    if hot:
        cond = f"pushed:>{since} stars:>50"
        q = cond
    else:
        cond = f'language:"{lang}"' if lang else free_text
        q = f"created:>{since} {cond} stars:>1"
    return http.search_repositories(q, page=page)


def fetch_channel(days: int, kind: str, key: str, n_pool: int,
                  interests: dict | None = None,
                  cancel: threading.Event | None = None,
                  log=print) -> list[Repo]:
    """抓满一个通道（语言/领域/热门近似/新晋），自动翻页到 n_pool（封顶 10 页）。

    kind: "lang" | "dom" | "hot" | "new"
    """
    token = http.token()
    pause = 7 if not token else 1
    max_pages = min(10, max(1, (n_pool + 99) // 100))
    out: list[Repo] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        if cancel is not None and cancel.is_set():
            break
        if kind == "hot":
            data = search(days, page, hot=True)
        elif kind == "lang":
            data = search(days, page, lang=key)
        elif kind == "dom":
            data = search(days, page, free_text=key)
        else:  # new：按语言抓新建仓库
            data = search(days, page, lang=None if key == "any" else key,
                          free_text="stars:>1" if key == "any" else None)
        items = data.get("items", [])
        if not items:
            break
        for r in items:
            repo = enrich(r, r.get("language") or key, interests)
            if kind == "dom":
                repo = enrich(r, r.get("language") or "跨语言", interests)
            if repo.name not in seen:
                seen.add(repo.name)
                out.append(repo)
        if len(items) < 100 or len(out) >= n_pool:
            break
        if page < max_pages:
            time.sleep(pause)
    return out[:n_pool]


def fetch_repo(name: str) -> dict:
    """core API 获取单仓库当前状态（收藏刷新/手动观察用，60 次/时限额）。"""
    d = http.github_api(f"repos/{name}", timeout=20)
    return {
        "stars": d.get("stargazers_count", 0),
        "forks": d.get("forks_count", 0),
        "pushed": (d.get("pushed_at") or "")[:10],
        "license": (d.get("license") or {}).get("spdx_id") or "-",
        "archived": bool(d.get("archived")),
        "description": (d.get("description") or "").strip(),
        "language": d.get("language") or "-",
    }


def fetch_release(name: str) -> dict:
    """获取仓库最新 Release（无 Release 抛异常）。"""
    d = http.github_api(f"repos/{name}/releases/latest", timeout=20)
    return {"tag": d.get("tag_name", "?"),
            "date": (d.get("published_at") or "")[:10]}
