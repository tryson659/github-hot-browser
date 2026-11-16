# -*- coding: utf-8 -*-
"""仓库身份规则（候选去重与导入共用）。

稳定身份以 GitHub repo id 为主；只有名称时先规范化，大小写不敏感去重。
本模块只做纯规则，不访问网络或数据库。
"""
from __future__ import annotations

import re

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_URL_PREFIX_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/", re.IGNORECASE)
_RESERVED_OWNERS = {
    "about", "apps", "collections", "contact", "customer-stories", "enterprise",
    "events", "explore", "features", "issues", "join", "login", "marketplace",
    "new", "notifications", "orgs", "pricing", "pulls", "readme", "search",
    "security", "settings", "site", "sponsors", "topics", "trending", "users",
}


def is_valid_repo_name(name: str) -> bool:
    parts = name.split("/")
    if len(parts) != 2:
        return False
    owner, repo = parts
    return (bool(_OWNER_RE.match(owner))
            and owner.lower() not in _RESERVED_OWNERS
            and bool(_REPO_RE.match(repo))
            and repo not in (".", ".."))


def normalize_repo_name(raw: str) -> str:
    """规范化 owner/repo 或 GitHub URL；裸三级路径不截取，链接子路径归一。"""
    text = (raw or "").strip()
    was_url = bool(_URL_PREFIX_RE.match(text))
    text = _URL_PREFIX_RE.sub("", text)
    text = text.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    parts = [p for p in text.split("/") if p]
    if len(parts) == 2:
        pass
    elif was_url and len(parts) > 2:
        parts = parts[:2]          # /tree/main、/issues/1 等归一到仓库根
    else:
        raise ValueError(f"不是 owner/repo：{raw!r}")
    owner, repo = parts[0], parts[1].removesuffix(".git").rstrip(".")
    name = f"{owner}/{repo}"
    if not is_valid_repo_name(name):
        raise ValueError(f"不是有效仓库名：{raw!r}")
    return name


def try_normalize_repo_name(raw: str) -> str | None:
    try:
        return normalize_repo_name(raw)
    except ValueError:
        return None


def repo_key(raw: str) -> str:
    """大小写不敏感的稳定键；无效名称返回规范化小写原文。"""
    name = try_normalize_repo_name(raw)
    return (name or (raw or "").strip()).lower()
