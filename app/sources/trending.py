# -*- coding: utf-8 -*-
"""数据源：GitHub Trending 官方趋势页抓取（近7天=weekly，近30天=monthly）。

全语言 + 各勾选语言各抓一页（约 25 个/页）合并去重；
页面自带本期增长数（"N stars this week"）。
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse

from app.core.models import Repo
from app.core.classify import classify_domain, match_interest
from app.sources import http

_ARTICLE_RE = re.compile(r'<article class="Box-row">.*?</article>', re.S)
_NAME_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="/([^"]+)"', re.S)
_DESC_RE = re.compile(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', re.S)
_LANG_RE = re.compile(r'itemprop="programmingLanguage">([^<]*)<')
_STARS_RE = re.compile(r'/stargazers"[^>]*>.*?([\d,]+)\s*</a>', re.S)
_FORKS_RE = re.compile(r'/forks"[^>]*>.*?([\d,]+)\s*</a>', re.S)
_INC_RE = re.compile(r'([\d,]+)\s*stars\s*this\s*(?:day|week|month)')


def parse_trending(html: str, since_label: str,
                   interests: dict | None = None) -> list[Repo]:
    out: list[Repo] = []
    for art in _ARTICLE_RE.findall(html):
        m = _NAME_RE.search(art)
        if not m:
            continue
        name = m.group(1).strip()
        dm = _DESC_RE.search(art)
        desc = re.sub(r"<[^>]+>", "", dm.group(1)).strip() if dm else ""
        lang = _LANG_RE.search(art)
        stars = _STARS_RE.search(art)
        forks = _FORKS_RE.search(art)
        inc = _INC_RE.search(art)
        inc_n = int(inc.group(1).replace(",", "")) if inc else None
        tags, hits = match_interest(name, desc, interests)
        out.append(Repo(
            name=name, url=f"https://github.com/{name}",
            stars=int(stars.group(1).replace(",", "")) if stars else None,
            forks=int(forks.group(1).replace(",", "")) if forks else None,
            language=lang.group(1).strip() if lang else "-",
            description=desc, pushed="-",
            domain=classify_domain(name, desc),
            growth_daily=round(inc_n / (7 if since_label == "本周" else 30), 1)
            if inc_n else 0,
            growth_7d=inc_n if since_label == "本周" else None,
            growth_value=inc_n, growth_window=since_label,
            metric="provider_reported",
            window_days=7 if since_label == "本周" else 30,
            completeness="partial",     # 页面声明值，非净增长契约
            interest_tags=tags, interest_hits=hits,
            source="GitHub Trending",
        ))
    return out


def fetch_trending(days: int, languages: list[str],
                   interests: dict | None = None,
                   cancel: threading.Event | None = None,
                   log=print) -> list[Repo]:
    since = {7: "weekly", 30: "monthly"}.get(days, "weekly")
    label = {7: "本周", 30: "本月"}.get(days, "本周")
    langs = ["any"] + list(languages)
    merged: list[Repo] = []
    seen: set[str] = set()
    for lang in langs:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("已取消")
        query = f"?since={since}"
        if lang != "any":
            query += "&l=" + urllib.parse.quote(lang, safe="")
        try:
            html = http.fetch_page(f"https://github.com/trending{query}")
        except Exception as e:
            log(f"  ⚠️ Trending {lang} 抓取失败：{e}")
            continue
        repos = parse_trending(html, label, interests)
        log(f"  Trending {lang}：{len(repos)} 条")
        for r in repos:
            if r.name not in seen:
                seen.add(r.name)
                merged.append(r)
        time.sleep(1.5)  # 页面对抓取敏感，保守节流
    if not merged:
        raise RuntimeError("Trending 未抓到任何项目（可能被限流，稍后重试）")
    return merged
