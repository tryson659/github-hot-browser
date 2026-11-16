# -*- coding: utf-8 -*-
"""批次 5：GH Archive 小时账本、缺口重试与覆盖诊断。"""
import io
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import Database
from app.services import archive_status, gha_board
from app.sources import gharchive
from app.storage.store import Store

SOURCE = gha_board.ARCHIVE_SOURCE


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


@pytest.fixture()
def store(db):
    return Store(db)


def _latest():
    return gharchive.latest_done_hour()


def _http_404():
    return urllib.error.HTTPError("https://x", 404, "Not Found", None,
                                  io.BytesIO(b"404"))


class TestLedgerStore:
    def test_status_and_coverage(self, db, store):
        hour = _latest()
        store.mark_archive_hour(SOURCE, hour, "success", event_count=3,
                                checksum="abc")
        store.mark_archive_hour(SOURCE, hour, "failed", error="timeout")
        row = store.archive_hour(SOURCE, hour)
        assert row["status"] == "failed" and row["error"] == "timeout"
        assert store.failed_archive_hours(SOURCE, hour, hour) == [hour]

        store.mark_archive_hour(SOURCE, hour, "empty")
        counts = store.archive_status_counts(SOURCE, hour, hour)
        assert counts == {"empty": 1}
        cov = gha_board.archive_coverage(db, 7)
        assert cov["expected"] > 0 and cov["tracked"] == 1
        assert cov["empty"] == 1

    def test_invalid_status_rejected(self, store):
        with pytest.raises(ValueError):
            store.mark_archive_hour(SOURCE, _latest(), "pending")


class TestCatchUpLedger:
    def test_success_and_empty_marked(self, db, monkeypatch):
        latest = _latest()
        cursor = (datetime.strptime(latest, "%Y-%m-%d-%H")
                  - timedelta(hours=2)).strftime("%Y-%m-%d-%H")
        db.gha_set_cursor(cursor)
        calls = []

        def fake(day, hour, cancel=None):
            calls.append(f"{day}-{hour:02d}")
            return {"a/b": 1} if len(calls) == 1 else {}

        monkeypatch.setattr(gharchive, "download_hour", fake)
        done, caught = gha_board.catch_up(db, max_hours=10, log=lambda *_: None)
        assert done == 2 and caught
        rows = Store(db).archive_hours(SOURCE)
        assert {r["status"] for r in rows} == {"success", "empty"}
        assert sum(r["event_count"] for r in rows) == 1

    def test_failure_kept_and_retried_without_gap_skip(self, db, monkeypatch):
        latest = _latest()
        cursor = (datetime.strptime(latest, "%Y-%m-%d-%H")
                  - timedelta(hours=2)).strftime("%Y-%m-%d-%H")
        db.gha_set_cursor(cursor)
        calls = []

        def flaky(day, hour, cancel=None):
            calls.append(hour)
            if len(calls) == 1:
                raise RuntimeError("network")
            return {"a/b": 2}

        monkeypatch.setattr(gharchive, "download_hour", flaky)
        done, caught = gha_board.catch_up(db, max_hours=10, log=lambda *_: None)
        assert done == 1 and not caught          # 一个失败，一个成功
        assert not db.gha_get_hour(
            (datetime.strptime(cursor, "%Y-%m-%d-%H")
             + timedelta(hours=1)).strftime("%Y-%m-%d-%H"))
        assert db.gha_get_cursor() == latest     # 后续成功推进了游标
        assert Store(db).failed_archive_hours(SOURCE, cursor, latest)

        # 游标已到 latest，但账本仍有失败缺口：下一轮优先补，不被追平吞掉
        monkeypatch.setattr(gharchive, "download_hour",
                            lambda day, hour, cancel=None: {"a/b": 3})
        done2, caught2 = gha_board.catch_up(db, max_hours=10,
                                            log=lambda *_: None)
        assert done2 == 1 and caught2
        assert Store(db).failed_archive_hours(SOURCE, cursor, latest) == []

    def test_404_not_marked_failed_and_cursor_kept(self, db, monkeypatch):
        latest = _latest()
        cursor = (datetime.strptime(latest, "%Y-%m-%d-%H")
                  - timedelta(hours=1)).strftime("%Y-%m-%d-%H")
        db.gha_set_cursor(cursor)

        def not_found(day, hour, cancel=None):
            raise _http_404()

        monkeypatch.setattr(gharchive, "download_hour", not_found)
        done, caught = gha_board.catch_up(db, max_hours=10, log=lambda *_: None)
        assert done == 0 and caught             # 发布延迟：不热循环
        assert db.gha_get_cursor() == cursor
        assert Store(db).archive_hours(SOURCE) == []

    def test_repeat_does_not_accumulate(self, db, monkeypatch):
        latest = _latest()
        cursor = (datetime.strptime(latest, "%Y-%m-%d-%H")
                  - timedelta(hours=1)).strftime("%Y-%m-%d-%H")
        db.gha_set_cursor(cursor)
        calls = []
        monkeypatch.setattr(gharchive, "download_hour",
                            lambda day, hour, cancel=None:
                            (calls.append(hour), {"a/b": 1})[1])
        gha_board.catch_up(db, max_hours=10, log=lambda *_: None)
        assert gha_board.window_counts(db, 7)["a/b"] == 1

        # 回拨游标重跑同一小时：账本成功且缓存存在，不重复下载/累计
        db.gha_set_cursor(cursor)
        before = len(calls)
        gha_board.catch_up(db, max_hours=10, log=lambda *_: None)
        assert len(calls) == before
        assert gha_board.window_counts(db, 7)["a/b"] == 1


class TestArchiveStatus:
    def test_report_failed_and_run(self, db, store):
        latest = _latest()
        store.mark_archive_hour(SOURCE, latest, "failed", error="timeout")
        run_id = store.start_source_run(SOURCE)
        store.finish_source_run(run_id, "partial", ok_count=0, fail_count=1)
        info = archive_status.report(db, 7)
        assert info["coverage"]["failed"] >= 1
        assert info["last_run"]["status"] == "partial"
        assert info["failed_total"] >= 1
        assert latest in info["failed_hours"]

    def test_no_runs(self, db):
        info = archive_status.report(db, 7)
        assert info["last_run"] is None
        assert info["coverage"]["expected"] > 0
