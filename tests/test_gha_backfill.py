# -*- coding: utf-8 -*-
"""GH Archive 手动回填：日历窗口、幂等跳过、取消保留。"""
import threading
from datetime import datetime, timezone

import pytest

from app.core.db import Database
from app.services import gha_board


class _FakeDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 15, 8, 5, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(gha_board, "datetime", _FakeDateTime)


def test_backfill_calendar_window_and_skip(db, monkeypatch):
    calls = []
    monkeypatch.setattr(gha_board.gharchive, "latest_done_hour",
                        lambda: "2026-11-15-06")
    monkeypatch.setattr(
        gha_board.gharchive, "download_hour",
        lambda day, hour, cancel=None: calls.append((day, hour)) or {"a/b": 2})
    stats = gha_board.backfill(db, 7, log=lambda *_: None)
    assert stats["failed"] == 0
    assert stats["downloaded"] == stats["expected"] > 0
    assert len(calls) == stats["expected"]
    # 再跑一次：已成功小时全部跳过
    stats2 = gha_board.backfill(db, 7, log=lambda *_: None)
    assert stats2["downloaded"] == 0
    assert stats2["skipped"] == stats2["expected"]


def test_backfill_cancel_keeps_completed(db, monkeypatch):
    cancel = threading.Event()
    monkeypatch.setattr(gha_board.gharchive, "latest_done_hour",
                        lambda: "2026-11-15-06")

    def fake(day, hour, cancel=None):
        state["n"] += 1
        if state["n"] == 2:
            cancel.set()
        return {"a/b": 1}

    state = {"n": 0}
    monkeypatch.setattr(gha_board.gharchive, "download_hour", fake)
    stats = gha_board.backfill(db, 7, log=lambda *_: None, cancel=cancel)
    assert stats["cancelled"] is True
    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
