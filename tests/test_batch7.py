# -*- coding: utf-8 -*-
"""批7：API 额度反馈 / 翻译接口可配置 / GitHub Token 设置 回归测试。"""
import io
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.core.db import Database
from app.core.models import Repo
from app.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    db = Database(str(tmp_path / "t.db"))
    w = MainWindow(db, start_sync=False)
    yield w
    db.close()


def _http_error(code, headers=None, body=b""):
    import urllib.error
    from email.message import Message
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://api.github.com/x", code,
                                  "err", msg, io.BytesIO(body))


class TestRateLimit:
    def test_403_with_headers_raises_ratelimit(self, monkeypatch):
        from app.sources import http
        import time
        reset = int(time.time()) + 900
        monkeypatch.setattr(
            http.urllib.request, "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(_http_error(
                403, {"x-ratelimit-remaining": "0",
                      "x-ratelimit-reset": str(reset)},
                b'{"message":"API rate limit exceeded"}')))
        with pytest.raises(http.RateLimitError) as ei:
            http.github_api("search/repositories?q=x")
        assert ei.value.reset_ts == reset
        assert "预计" in str(ei.value) and "恢复" in str(ei.value)

    def test_body_only_rate_limit_also_detected(self, monkeypatch):
        from app.sources import http
        monkeypatch.setattr(
            http.urllib.request, "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(_http_error(
                403, {}, b'{"message":"You have exceeded a secondary rate limit"}')))
        with pytest.raises(http.RateLimitError):
            http.github_api("repos/a/b")

    def test_normal_403_not_ratelimit(self, monkeypatch):
        from app.sources import http
        monkeypatch.setattr(
            http.urllib.request, "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(_http_error(
                403, {}, b'{"message":"Forbidden"}')))
        with pytest.raises(RuntimeError) as ei:
            http.github_api("repos/a/b")
        assert not isinstance(ei.value, http.RateLimitError)
        assert "HTTP 403" in str(ei.value)

    def test_fetcher_records_reset_and_status_warns(self, win, monkeypatch):
        import time
        from app.services import gha_board
        from app.sources import opengithubs, trending, github_search, http
        reset = int(time.time()) + 600
        err = http.RateLimitError(reset)
        monkeypatch.setattr(   # 预检桩：额度充足，让流程走到通道抓取
            http, "github_api",
            lambda path, timeout=30:
                {"resources": {"core": {"remaining": 100, "reset": 0}}})
        monkeypatch.setattr(opengithubs, "fetch_rank",
                            lambda *a: (_ for _ in ()).throw(err))
        monkeypatch.setattr(trending, "fetch_trending",
                            lambda *a, **k: (_ for _ in ()).throw(err))
        monkeypatch.setattr(gha_board, "build_board",
                            lambda *a, **k: (_ for _ in ()).throw(err))
        monkeypatch.setattr(github_search, "fetch_channel",
                            lambda *a, **k: (_ for _ in ()).throw(err))
        from app.services.fetcher import FetchWorker
        from app.core import config
        wk2 = FetchWorker(win.db, only=config.HOT_KEY)
        wk2.run()
        assert wk2.rate_limit_reset == reset
        # 主窗口汇总提示
        win._worker = wk2
        win._on_fetch_done(0, 2, ["🔥本周热门"])
        assert "额度已用完" in win.status_lbl.text()
        assert "恢复" in win.status_lbl.text()


class TestTranslateProviders:
    def test_mymemory_with_email(self, win, monkeypatch):
        from app.services import translate as tr
        seen = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self):
                return b'{"responseData":{"translatedText":"\\u4f60\\u597d"}}'

        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            return Resp()
        monkeypatch.setattr(tr.urllib.request, "urlopen", fake_urlopen)
        out = tr.translate("hello", win.db,
                           overrides={"translate_provider": "mymemory",
                                      "translate_email": "a@b.com"},
                           skip_cache=True)
        assert out == "你好"
        assert "de=a%40b.com" in seen["url"]

    def test_libre_provider(self, win, monkeypatch):
        from app.services import translate as tr
        seen = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self):
                return b'{"translatedText":"\xe4\xbd\xa0\xe5\xa5\xbd\xe4\xb8\x96\xe7\x95\x8c"}'

        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            seen["body"] = req.data.decode()
            return Resp()
        monkeypatch.setattr(tr.urllib.request, "urlopen", fake_urlopen)
        out = tr.translate("hello world", win.db,
                           overrides={"translate_provider": "libre",
                                      "translate_endpoint": "http://x/translate",
                                      "translate_apikey": "K1"},
                           skip_cache=True)
        assert out == "你好世界"
        assert seen["url"] == "http://x/translate"
        assert '"api_key": "K1"' in seen["body"]

    def test_libre_error_propagates(self, win, monkeypatch):
        from app.services import translate as tr

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self):
                return b'{"error":"Invalid API key"}'
        monkeypatch.setattr(tr.urllib.request, "urlopen",
                            lambda *a, **k: Resp())
        with pytest.raises(RuntimeError, match="Invalid API key"):
            tr.translate("hello", win.db,
                         overrides={"translate_provider": "libre",
                                    "translate_endpoint": "http://x/translate"},
                         skip_cache=True)

    def test_default_provider_is_mymemory(self, win):
        assert win.db.get_setting("translate_provider") == "mymemory"

    def test_cjk_and_cache_shortcut(self, win):
        from app.services import translate as tr
        assert tr.translate("已经是中文", win.db) == "已经是中文"
        win.db.save_translation("cached text", "缓存译文")
        assert tr.translate("cached text", win.db) == "缓存译文"


class TestSettingsAndToken:
    def test_settings_translate_roundtrip(self, win):
        from app.ui.dialogs import SettingsDialog
        dlg = SettingsDialog(win.db, win)
        dlg.tr_provider.setCurrentIndex(1)           # libre
        dlg.tr_endpoint.setText("http://my-libre/translate")
        dlg.tr_apikey.setText("K9")
        dlg.tr_email.setText("me@x.com")
        dlg.token_edit.setText("test-token-123")
        dlg._save()
        s = win.db.all_settings()
        assert s["translate_provider"] == "libre"
        assert s["translate_endpoint"] == "http://my-libre/translate"
        assert s["translate_apikey"] == "K9"
        assert s["translate_email"] == "me@x.com"
        assert s["github_token"] == "test-token-123"

    def test_apply_token_env(self, win, monkeypatch):
        """R08：设置 Token 注入 HTTP 层，不改进程环境；环境变量优先级另行覆盖。"""
        from app.sources import http
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        win.db.set_setting("github_token", "abc-token")
        win._apply_token()
        assert http.token() == "abc-token"
        assert os.environ.get("GITHUB_TOKEN") is None
        win.db.set_setting("github_token", "")
        win._apply_token()
        assert http.token() is None

    def test_import_dialog_opens(self, win):
        """回归：QPushButton 未导入导致打开即 NameError。"""
        from app.ui.dialogs import ImportDialog
        dlg = ImportDialog(win)
        assert dlg.count_lbl.text() == "识别到 0 个项目"
        dlg.text_edit.setPlainText("a/b\nhttps://github.com/c/d")
        assert dlg.names() == ["a/b", "c/d"]
