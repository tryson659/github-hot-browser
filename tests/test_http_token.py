# -*- coding: utf-8 -*-
"""R08：Token 优先级（环境变量 > 应用设置），UI 不再改进程环境。"""
import pytest

from app.sources import http


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    http.set_persisted_token("")
    yield
    http.set_persisted_token("")


def test_anonymous_by_default():
    assert http.token() is None
    assert http.token_source() == "anonymous"


def test_settings_token_used(monkeypatch):
    http.set_persisted_token(" settings-token ")
    assert http.token() == "settings-token"
    assert http.token_source() == "settings"


def test_env_overrides_settings(monkeypatch):
    http.set_persisted_token("settings-token")
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert http.token() == "env-token"
    assert http.token_source() == "env"


def test_clearing_settings_keeps_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    http.set_persisted_token("settings-token")
    http.set_persisted_token("")
    assert http.token() == "env-token"      # 清空设置不影响外部配置
