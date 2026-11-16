# -*- coding: utf-8 -*-
"""OpenGithubs 周/月榜：目录发现 + 真实格式表格解析（不联网）。"""
import base64
from pathlib import Path

from app.sources import opengithubs

FIXTURE = Path(__file__).parent / "fixtures" / "opengithubs_weekly.md"


def test_weekly_directory_discovery_and_parse(monkeypatch):
    text = FIXTURE.read_text(encoding="utf-8")

    def fake_api(path, timeout=30, headers=None):
        if path.endswith("/contents/"):
            return [{"type": "dir", "name": "2026"}]
        if path.endswith("/contents/2026"):
            return [{"type": "dir", "name": "11"}]
        if path.endswith("/contents/2026/11"):
            return [{"name": "20261108.md"}]
        if path.endswith("/contents/2026/11/20261108.md"):
            return {"content": base64.b64encode(
                text.encode("utf-8")).decode("ascii")}
        raise AssertionError(path)

    monkeypatch.setattr(opengithubs.http, "github_api", fake_api)
    repos, period = opengithubs.fetch_rank(7)
    assert period == "周榜 20261108"
    assert [r.name for r in repos] == [
        "deepseek-ai/dsh-superagent-prototype",
        "Donchitos/Claude-Code-Game-Studios"]
    assert repos[0].stars == 16400
    assert repos[0].growth_7d == 14286
    assert repos[0].metric == "provider_reported"
    assert repos[1].created == "2026-10-20"


def test_monthly_directory_discovery(monkeypatch):
    text = FIXTURE.read_text(encoding="utf-8")

    def fake_api(path, timeout=30, headers=None):
        if path.endswith("/contents/"):
            return [{"type": "dir", "name": "2026"}]
        if path.endswith("/contents/2026"):
            return [{"name": "202610.md"}]
        if path.endswith("/contents/2026/202610.md"):
            return {"content": base64.b64encode(
                text.encode("utf-8")).decode("ascii")}
        raise AssertionError(path)

    monkeypatch.setattr(opengithubs.http, "github_api", fake_api)
    repos, period = opengithubs.fetch_rank(30)
    assert period == "月榜 20261001"
    assert len(repos) == 2
    assert repos[0].growth_7d is None
