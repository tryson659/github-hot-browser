# -*- coding: utf-8 -*-
"""名单导入：解析文本/文件中的仓库名，经 ecosyste.ms 批量查元数据后加入观察名单。

解析规则（R07 重写）：
- GitHub 链接用 URL 规则识别，`/tree/...`、`/issues/...` 等子路径归一到仓库根；
- 裸名称必须是完整的两段 token（`a/b/c` 不再截取成虚假的 `a/b`）；
- 其他域名链接、`http:` 等伪段、保留路径（features/topics/...）一律忽略；
- 去 `.git` 后缀、去句尾标点、按大小写不敏感去重并保持首次出现的写法；
- 单次上限 500 条（防打满 ecosyste.ms 5000 次/时限额）。
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request

from app.core.db import Database
from app.domain.identity import is_valid_repo_name

CAP = 500

# GitHub 链接（scheme 可选），路径部分在解析时取前两段
_URL_RE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?github\.com/([^\s)\]}>'\"`]+)",
    re.IGNORECASE)
_TOKEN_RE = re.compile(r"[^\s,;，；、|()\[\]{}<>\"'`，。；：！？]+")
_TRIM_CHARS = " \t\r\n.,;:!?*_~'\"()[]{}<>，。；：、！？"


def _valid(owner: str, repo: str) -> bool:
    return is_valid_repo_name(f"{owner}/{repo}")


def _path_parts(path: str) -> list[str]:
    path = path.split("?", 1)[0].split("#", 1)[0]
    return [p for p in path.split("/") if p]


def _add(owner: str, repo: str, out: list[str], seen: set[str]):
    repo = repo.removesuffix(".git").rstrip(".")
    if not _valid(owner, repo):
        return
    key = f"{owner}/{repo}".lower()
    if key in seen:
        return
    seen.add(key)
    out.append(f"{owner}/{repo}")


def parse_repo_names(text: str) -> list[str]:
    """从任意文本中解析 owner/repo 列表（按出现位置去重、保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    found: list[tuple[int, str, str]] = []

    # 1) URL：识别后整段屏蔽（保留字符位置），避免 /tree/main 被当成裸名称
    spans: list[tuple[int, int]] = []
    for m in _URL_RE.finditer(text):
        parts = _path_parts(m.group(1))
        if len(parts) >= 2:
            found.append((m.start(), parts[0], parts[1]))
        spans.append(m.span())
    masked = list(text)
    for start, end in spans:
        for i in range(start, end):
            masked[i] = " "
    rest = "".join(masked)

    # 2) 裸 token：必须是完整两段；其他域名与裸三级路径不截取
    for m in _TOKEN_RE.finditer(rest):
        token = m.group(0).strip(_TRIM_CHARS)
        if not token:
            continue
        low = token.lower()
        if low.startswith("www.github.com/"):
            token = token[len("www."):]
            low = token.lower()
        if low.startswith("github.com/"):
            parts = _path_parts(token[len("github.com/"):])
            if len(parts) >= 2:
                found.append((m.start(), parts[0], parts[1]))
            continue
        if "://" in token:
            continue
        parts = token.split("/")
        if len(parts) != 2:
            continue
        found.append((m.start(), parts[0], parts[1]))

    # 3) 按原文出现顺序输出，去重保持首次写法
    for _, owner, repo in sorted(found):
        _add(owner, repo, out, seen)
    return out


def eco_repo(name: str) -> dict:
    """ecosyste.ms 单仓元数据（5000 次/时，远宽于 GitHub core 60/时）。"""
    url = ("https://repos.ecosyste.ms/api/v1/hosts/GitHub/repositories/"
           + urllib.parse.quote(name, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": "github-hot-browser/2.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    return {
        "stars": d.get("stargazers_count") or 0,
        "language": d.get("language") or "-",
        "description": (d.get("description") or "").strip(),
        "pushed": (d.get("pushed_at") or "")[:10],
        "archived": bool(d.get("archived")),
    }


def import_names(db: Database, names: list[str], log=print,
                 cancel: threading.Event | None = None,
                 cap: int = CAP) -> tuple[int, int, int]:
    """批量导入观察名单。返回 (新增, 已存在跳过, 失败)。"""
    if len(names) > cap:
        raise ValueError(f"单次最多导入 {cap} 条（当前 {len(names)}），请分批导入")
    added = skipped = failed = 0
    existing = {w["name"].lower() for w in db.watch_list()}
    for i, name in enumerate(names, 1):
        if cancel is not None and cancel.is_set():
            log("⏹ 导入已取消")
            break
        if name.lower() in existing:
            skipped += 1
            continue
        try:
            info = eco_repo(name)
            db.watch_add(name, url=f"https://github.com/{name}",
                         desc=info["description"], lang=info["language"],
                         manual=True)
            db.watch_sample(name, info["stars"])
            existing.add(name.lower())
            added += 1
        except Exception as e:
            failed += 1
            log(f"  ⚠️ {name}：{e}")
        if i % 25 == 0:
            log(f"  导入进度 {i}/{len(names)}（新增 {added}）")
    return added, skipped, failed
