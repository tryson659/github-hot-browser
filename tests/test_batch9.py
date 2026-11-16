# -*- coding: utf-8 -*-
"""批9：pool_size 生效 / 抓前额度预检 / GH Archive 详情补全 回归测试。"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.services.fetcher import FetchWorker
from app.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.set_setting("github_api_enabled", True)   # 本套件覆盖 API 预检/补全
    yield d
    d.close()


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    d = Database(str(tmp_path / "t.db"))
    d.set_setting("github_api_enabled", True)
    w = MainWindow(d, start_sync=False)
    yield w
    d.close()


def _repo(name="a/b"):
    return Repo(name=name, url=f"https://github.com/{name}")


def _stub_quota_ok(monkeypatch):
    """预检桩：额度充足。"""
    from app.sources import http
    monkeypatch.setattr(http, "github_api", lambda path, timeout=30:
                        {"resources": {"core": {"remaining": 100, "reset": 0}}})


def _fast_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)


class TestPoolSize:
    def test_gharchive_board_uses_pool_size(self, db, monkeypatch):
        """设置 200 后 GH Archive 建榜 top=200（此前硬编码 50）。"""
        from app.services import gha_board
        from app.sources import github_search
        db.set_setting("pool_size", 200)
        db.set_setting("hot_source", "gharchive")
        seen = {}
        monkeypatch.setattr(gha_board, "build_board",
                            lambda d, days, top, interests=None, now=None:
                            (seen.setdefault("top", top), ([_repo()], "p"))[1])
        monkeypatch.setattr(github_search, "fetch_repo",
                            lambda name: {"stars": 1, "forks": 0, "pushed": "-",
                                          "license": "-", "archived": False,
                                          "description": "", "language": "-"})
        _fast_sleep(monkeypatch)
        wk = FetchWorker(db, only=config.HOT_KEY)
        wk._fetch_hot(7)
        assert seen["top"] == 200

    def test_search_hot_uses_pool_size(self, db, monkeypatch):
        """本地搜索兜底源同样读设置（此前硬编码 50）。"""
        from app.sources import github_search
        db.set_setting("pool_size", 200)
        db.set_setting("hot_source", "search")
        seen = {}
        monkeypatch.setattr(github_search, "fetch_channel",
                            lambda days, kind, key, n_pool, *a, **k:
                            (seen.setdefault("n", n_pool), [_repo()])[1])
        wk = FetchWorker(db, only=config.HOT_KEY)
        wk._fetch_hot(7)
        assert seen["n"] == 200

    def test_pool_size_clamped(self, db):
        db.set_setting("pool_size", 99999)
        wk = FetchWorker(db)
        assert wk._pool_n() == config.POOL_MAX
        db.set_setting("pool_size", 1)
        assert FetchWorker(db)._pool_n() == 10


class TestPreflight:
    def test_quota_zero_skips_fetch(self, db, monkeypatch):
        """额度 0：整条抓取计划跳过、记录恢复时间、不落库。"""
        from app.sources import http, github_search
        reset = int(time.time()) + 600
        monkeypatch.setattr(http, "github_api", lambda path, timeout=30:
                            {"resources": {"core": {"remaining": 0,
                                                    "reset": reset}}})
        monkeypatch.setattr(github_search, "fetch_channel",
                            lambda *a, **k: pytest.fail("不应进入通道抓取"))
        msgs, done = [], []
        wk = FetchWorker(db)           # 默认设置含语言+领域通道 → 需要 API
        wk.progress.connect(msgs.append)
        wk.finished_all.connect(lambda ok, fail, failed:
                                done.append((ok, fail, failed)))
        wk.run()
        assert wk.rate_limit_reset == reset
        assert done == [(0, 0, [])]
        assert any("额度已用完" in m and "已跳过" in m for m in msgs)
        assert db.cached_channels() == []

    def test_quota_ok_proceeds(self, db, monkeypatch):
        """额度充足：正常走抓取链路。"""
        from app.sources import opengithubs
        _stub_quota_ok(monkeypatch)
        monkeypatch.setattr(opengithubs, "fetch_rank",
                            lambda days, interests=None: ([_repo()], "周榜"))
        done = []
        wk = FetchWorker(db, only=config.HOT_KEY)
        wk.finished_all.connect(lambda ok, fail, failed:
                                done.append((ok, fail, failed)))
        wk.run()
        assert done == [(2, 0, [])]    # 7 天 + 30 天两个通道
        assert db.cached_channels()

    def test_gharchive_source_exempt(self, db, monkeypatch):
        """GH Archive 纯本地源不耗 API：无语言/领域通道时预检不查额度。"""
        from app.services import gha_board
        from app.sources import http, github_search
        db.set_setting("hot_source", "gharchive")
        db.set_setting("languages", [])
        db.set_setting("domains", [])
        monkeypatch.setattr(http, "github_api",
                            lambda *a, **k: pytest.fail("纯本地源不应查额度"))
        monkeypatch.setattr(gha_board, "build_board",
                            lambda d, days, top, interests=None, now=None:
                                ([_repo()], "p"))
        monkeypatch.setattr(github_search, "fetch_repo",
                            lambda name: {"stars": 5, "forks": 1, "pushed": "-",
                                          "license": "-", "archived": False,
                                          "description": "d", "language": "Go"})
        _fast_sleep(monkeypatch)
        done = []
        wk = FetchWorker(db, only=config.HOT_KEY)
        wk.finished_all.connect(lambda ok, fail, failed:
                                done.append((ok, fail, failed)))
        wk.run()
        assert done == [(2, 0, [])]

    def test_preflight_failure_not_blocking(self, db, monkeypatch):
        """预检请求本身异常（如断网）不阻塞正常抓取。"""
        from app.sources import http, opengithubs
        monkeypatch.setattr(http, "github_api",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("network down")))
        monkeypatch.setattr(opengithubs, "fetch_rank",
                            lambda days, interests=None: ([_repo()], "周榜"))
        done = []
        wk = FetchWorker(db, only=config.HOT_KEY)
        wk.finished_all.connect(lambda ok, fail, failed:
                                done.append((ok, fail, failed)))
        wk.run()
        assert done == [(2, 0, [])]


class TestEnrichGha:
    def test_enrich_fills_details(self, db, monkeypatch):
        from app.sources import github_search
        monkeypatch.setattr(github_search, "fetch_repo",
                            lambda name: {"stars": 123, "forks": 7,
                                          "pushed": "2026-11-01",
                                          "license": "MIT", "archived": False,
                                          "description": "好项目",
                                          "language": "Python"})
        _fast_sleep(monkeypatch)
        wk = FetchWorker(db)
        repos = [_repo("x/y"), _repo("c/d")]
        msgs = []
        wk.progress.connect(msgs.append)
        wk._enrich_gha(repos)
        assert repos[0].stars == 123 and repos[0].description == "好项目"
        assert repos[1].language == "Python"
        assert any("详情补全 2 个项目" in m for m in msgs)

    def test_enrich_failure_keeps_board(self, db, monkeypatch):
        """补全中途失败：榜单本体保留，已补的不回退。"""
        from app.sources import github_search
        calls = {"n": 0}

        def flaky(name):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("403")
            return {"stars": 9, "forks": 0, "pushed": "-", "license": "-",
                    "archived": False, "description": "", "language": "-"}
        monkeypatch.setattr(github_search, "fetch_repo", flaky)
        _fast_sleep(monkeypatch)
        wk = FetchWorker(db)
        repos = [_repo("x/y"), _repo("c/d"), _repo("e/f")]
        msgs = []
        wk.progress.connect(msgs.append)
        wk._enrich_gha(repos)
        assert repos[0].stars == 9               # 第一个补上了
        assert repos[1].stars is None            # 中断后保持原样
        assert any("详情补全中断" in m for m in msgs)


class TestFetchDoneQuotaHint:
    def test_preflight_skip_shows_hint(self, win):
        """预检跳过（0 成功 0 失败）也要提示额度恢复时间。"""
        reset = int(time.time()) + 900
        wk = FetchWorker(win.db)
        wk.rate_limit_reset = reset
        win._worker = wk
        win._on_fetch_done(0, 0, [])
        assert "额度已用完" in win.status_lbl.text()
        assert "恢复" in win.status_lbl.text()
