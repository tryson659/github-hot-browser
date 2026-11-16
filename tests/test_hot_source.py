# -*- coding: utf-8 -*-
"""R04：自动数据源的活动源按 7/30 天分别记录，互不覆盖。"""
import pytest

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.services import hot_source


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.set_setting("github_api_enabled", True)   # 本套件覆盖报告源 auto 行为
    yield d
    d.close()


def _repo(name):
    return Repo(name=name, url=f"https://github.com/{name}", stars=1)


def test_active_source_isolated_by_days(db):
    hot_source.set_active(db, 7, "opengithubs")
    hot_source.set_active(db, 30, "trending")
    assert hot_source.channel_for(db, 7, "auto") == config.RANK_WEEK_KEY
    assert hot_source.channel_for(db, 30, "auto") == config.TREND_KEY
    assert hot_source.get_active(db, 7) == "opengithubs"
    assert hot_source.get_active(db, 30) == "trending"


def test_week_success_not_lost_when_month_falls_back(db):
    """7 天 OpenGithubs 成功、30 天 Trending，切时间档各看各的报告。"""
    db.save_cache(config.RANK_WEEK_KEY, 7, [_repo("week/1")])
    db.save_cache(config.TREND_KEY, 30, [_repo("month/1")])
    hot_source.set_active(db, 7, "opengithubs")
    hot_source.set_active(db, 30, "trending")
    week = db.load_cache(hot_source.channel_for(db, 7, "auto"), 7)[0]
    month = db.load_cache(hot_source.channel_for(db, 30, "auto"), 30)[0]
    assert [r.name for r in week] == ["week/1"]
    assert [r.name for r in month] == ["month/1"]


def test_legacy_global_key_still_read_but_not_auto_selected(db):
    db.set_setting("__hot_active__", "search")
    assert hot_source.get_active(db, 7) == "search"
    # 搜索候选不是报告源，auto 不跟随
    assert hot_source.channel_for(db, 7, "auto") == config.RANK_WEEK_KEY


def test_fallback_first_report_channel(db):
    db.save_cache(config.TREND_KEY, 7, [_repo("t/1")])
    assert hot_source.channel_for(db, 7, "auto") == config.TREND_KEY


def test_event_cache_not_selected_by_auto(db):
    """事件榜必须显式选择；auto 无活动源时不得回落到事件缓存。"""
    db.save_cache(config.GH_ARC_KEY, 7, [_repo("g/1")])
    assert hot_source.channel_for(db, 7, "auto") == config.RANK_WEEK_KEY


def test_single_source_ignores_active(db):
    hot_source.set_active(db, 7, "gharchive")
    assert hot_source.channel_for(db, 7, "trending") == config.TREND_KEY


def test_api_disabled_auto_only_trending(db):
    """默认禁用 GitHub API：即使有 OpenGithubs 缓存/活动源，auto 也只看 Trending。"""
    db.set_setting("github_api_enabled", False)
    db.save_cache(config.RANK_WEEK_KEY, 7, [_repo("week/1")])
    hot_source.set_active(db, 7, "opengithubs")
    assert hot_source.channel_for(db, 7, "auto") == config.TREND_KEY
    # 显式选择仍可查看已缓存的报告源
    assert hot_source.channel_for(db, 7, "opengithubs") == config.RANK_WEEK_KEY
    # 用户启用后才跟随 OpenGithubs
    db.set_setting("github_api_enabled", True)
    assert hot_source.channel_for(db, 7, "auto") == config.RANK_WEEK_KEY
