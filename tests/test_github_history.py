# -*- coding: utf-8 -*-
"""批次 3：官方 Star History 契约解析（固定样本，不联网）。"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.sources import github_history

FIXTURE = Path(__file__).parent / "fixtures" / "star_history.json"


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestParse:
    def test_week_and_days(self):
        hist = github_history.parse_history(
            _payload(), repo="a/b",
            fetched_at=datetime(2026, 11, 15, tzinfo=timezone.utc))
        assert hist.repo == "a/b"
        assert len(hist.days) == 13              # 一个 null 不写入
        buckets = hist.buckets()
        assert buckets[date(2026, 11, 1)] == 10
        assert buckets[date(2026, 11, 8)] == 20
        assert date(2026, 11, 13) not in buckets  # null 日缺测，不是 0

    def test_dict_wrapper(self):
        hist = github_history.parse_history({"weeks": _payload()})
        assert len(hist.days) == 13

    def test_empty_history_is_valid(self):
        hist = github_history.parse_history([])
        assert hist.days == ()

    def test_zero_is_real_observation(self):
        hist = github_history.parse_history(
            [{"week": "2026-11-01", "days": [0]}])
        assert hist.buckets()[date(2026, 11, 1)] == 0

    def test_later_bucket_replaces_revision(self):
        hist = github_history.parse_history([
            {"week": "2026-11-01", "days": [5]},
            {"week": "2026-11-01", "days": [9]},
        ])
        assert hist.buckets()[date(2026, 11, 1)] == 9
        assert len(hist.days) == 1

    def test_unix_week_and_future_days_skipped(self):
        # 实测 2026-03-10 版：week 是 Unix 秒，当前周未来日期为 0
        week = int(datetime(2026, 11, 15, tzinfo=timezone.utc).timestamp())
        hist = github_history.parse_history(
            [{"week": week, "total": 0, "days": [0] * 7}],
            fetched_at=datetime(2026, 11, 15, 8, tzinfo=timezone.utc))
        # 当天未走完，未来日期也不写入；不能把 0 当完整观测
        assert hist.buckets() == {}

    def test_unix_week_with_data(self):
        week = int(datetime(2026, 11, 8, tzinfo=timezone.utc).timestamp())
        hist = github_history.parse_history(
            [{"week": week, "total": 12, "days": [1, 2, 3, None, 5, 6, 7]}],
            fetched_at=datetime(2026, 11, 15, 8, tzinfo=timezone.utc))
        assert hist.buckets()[date(2026, 11, 9)] == 2
        assert date(2026, 11, 11) not in hist.buckets()

    @pytest.mark.parametrize("payload", [
        "not-a-list",
        [{"days": [1]}],
        [{"week": "2026-11-01", "days": "x"}],
        [{"week": "2026-11-01", "days": [-1]}],
        [{"week": "2026-11-01", "days": [True]}],
        [{"week": "not-a-date", "days": [1]}],
        [{"week": "2026-11-01", "days": [1] * 8}],
        [{"week": True, "days": [1]}],
        ["not-an-object"],
    ])
    def test_malformed_raises(self, payload):
        with pytest.raises(github_history.HistoryParseError):
            github_history.parse_history(payload)


class TestFetch:
    def test_path_and_version_header(self, monkeypatch):
        captured = {}

        def fake_api(path, timeout=30, headers=None):
            captured.update(path=path, timeout=timeout, headers=headers)
            return _payload()

        monkeypatch.setattr(github_history.http, "github_api", fake_api)
        hist = github_history.fetch_history(
            "https://github.com/Owner/Repo/tree/main")
        assert captured["path"] == "repos/Owner/Repo/stargazers/history"
        assert captured["headers"]["X-GitHub-Api-Version"] == \
            github_history.API_VERSION
        assert hist.repo == "Owner/Repo"

    def test_invalid_repo_rejected_before_request(self, monkeypatch):
        monkeypatch.setattr(
            github_history.http, "github_api",
            lambda *a, **k: pytest.fail("无效仓库不应发请求"))
        with pytest.raises(ValueError):
            github_history.fetch_history("not-a-repo")
