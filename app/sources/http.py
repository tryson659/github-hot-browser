# -*- coding: utf-8 -*-
"""HTTP 公共层：统一 UA、GITHUB_TOKEN、超时、页面抓取与 GitHub API。"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core.config import USER_AGENT

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class RateLimitError(RuntimeError):
    """GitHub API 额度耗尽（403/429）。携带恢复时间，供 UI 提示用户等待。"""

    def __init__(self, reset_ts: int | None):
        self.reset_ts = reset_ts or None
        if self.reset_ts:
            when = time.strftime("%H:%M", time.localtime(self.reset_ts))
            wait = max(0, int((self.reset_ts - time.time()) / 60))
            msg = f"GitHub API 额度已用完，预计 {when} 恢复（约 {wait} 分钟后）"
        else:
            msg = ("GitHub API 触发限流（未返回恢复时间，"
                   "搜索类限流通常 1~5 分钟内自行恢复）")
        msg += "；在设置中配置 GitHub Token 可将限额提升至 5000 次/小时"
        super().__init__(msg)


def token() -> str | None:
    """当前使用的 Token：环境变量优先，其次应用设置注入的值（R08）。

    UI 不再修改进程环境；`set_persisted_token` 只更新设置值。
    """
    return os.environ.get("GITHUB_TOKEN") or _persisted_token or None


_persisted_token = ""


def set_persisted_token(tok: str | None):
    """注入设置里保存的 Token（空字符串表示清除应用设置值）。"""
    global _persisted_token
    _persisted_token = (tok or "").strip()


def token_source() -> str:
    """返回当前 Token 来源：env / settings / anonymous。"""
    if os.environ.get("GITHUB_TOKEN"):
        return "env"
    if _persisted_token:
        return "settings"
    return "anonymous"


def github_api(path: str, timeout: int = 30,
               headers: dict | None = None) -> dict | list:
    """GitHub REST API（api.github.com/<path>），自动带 token 与额外请求头。"""
    merged = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        **({"Authorization": f"Bearer {token()}"} if token() else {}),
    }
    merged.update(headers or {})
    req = urllib.request.Request(f"https://api.github.com/{path}", headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if e.code in (403, 429) and (
                e.headers.get("x-ratelimit-remaining") == "0"
                or "rate limit" in body.lower()):
            ts = e.headers.get("x-ratelimit-reset", "")
            raise RateLimitError(int(ts) if ts.isdigit() else None) from e
        raise RuntimeError(f"HTTP {e.code}: {body[:120] or e.reason}") from e


def search_repositories(query: str, page: int = 1, per_page: int = 100) -> dict:
    q = urllib.parse.urlencode({
        "q": query, "sort": "stars", "order": "desc",
        "per_page": per_page, "page": page,
    })
    return github_api(f"search/repositories?{q}")


def fetch_page(url: str, timeout: int = 30) -> str:
    """抓取普通网页（伪装浏览器 UA）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")
