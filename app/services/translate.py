# -*- coding: utf-8 -*-
"""中文翻译：可配置翻译接口 + SQLite 缓存（原文即键）。

提供方（设置 translate_provider）：
- mymemory（默认）：MyMemory 免费接口，匿名约 5000 字符/天；
  填写注册邮箱（translate_email）可提额至约 50000 字符/天。
- libre：LibreTranslate 兼容接口（translate_endpoint，可自建；
  translate_apikey 可选）。请求 POST JSON {q, source, target, api_key}。

中文原文直接跳过；有缓存直接返回。失败抛异常（调用方决定降级展示）。
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from app.core.db import Database

_CJK_RE = re.compile(r"[一-鿿]")
DEFAULT_LIBRE_ENDPOINT = "https://libretranslate.com/translate"


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _mymemory(text: str, cfg: dict) -> str:
    params = {"q": text, "langpair": "en|zh-CN"}
    email = (cfg.get("translate_email") or "").strip()
    if email:
        params["de"] = email          # 注册邮箱提额
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"https://api.mymemory.translated.net/get?{q}",
        headers={"User-Agent": "github-hot-browser/2.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    out = (d.get("responseData") or {}).get("translatedText") or ""
    if not out or out.upper() == text.upper():
        raise RuntimeError("MyMemory 未返回有效结果（可能已达当日限额）")
    return out


def _libre(text: str, cfg: dict) -> str:
    endpoint = (cfg.get("translate_endpoint") or "").strip() \
        or DEFAULT_LIBRE_ENDPOINT
    payload = {"q": text, "source": "en", "target": "zh", "format": "text"}
    key = (cfg.get("translate_apikey") or "").strip()
    if key:
        payload["api_key"] = key
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "github-hot-browser/2.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    out = d.get("translatedText") or ""
    if not out:
        err = d.get("error") or "未返回 translatedText"
        raise RuntimeError(f"LibreTranslate 接口异常：{err}")
    return out


def translate(text: str, db: Database,
              overrides: dict | None = None,
              skip_cache: bool = False) -> str:
    """英译中。overrides 可临时覆盖配置（设置页「测试翻译」用）。"""
    text = text.strip()
    if not text:
        return text
    if has_cjk(text):
        return text
    if not skip_cache:
        cached = db.get_translation(text)
        if cached is not None:
            return cached
    cfg = dict(db.all_settings())
    if overrides:
        cfg.update(overrides)
    provider = (cfg.get("translate_provider") or "mymemory").strip()
    out = _libre(text, cfg) if provider == "libre" else _mymemory(text, cfg)
    if not skip_cache:
        db.save_translation(text, out)
    return out
