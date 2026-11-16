# -*- coding: utf-8 -*-
"""抓取编排：QThread 工作线程，通道级进度信号，可取消。

职责：
- 语言通道 / 领域通道 / 本周热门（OpenGithubs → Trending → 本地搜索 降级链）
- 每完成一个通道立即写库并发 channel_done 信号（UI 增量刷新）
- 搜索类通道套用 6~8 天快照基线计算 growth_7d
- 每次抓取写历史快照（增长基线来源）
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.services import gha_board, hot_source, watchlist
from app.sources import github_search, opengithubs, trending


class FetchWorker(QThread):
    progress = Signal(str)                    # 状态日志
    channel_done = Signal(int, str)           # (days, channel) 单通道完成
    finished_all = Signal(int, int, list)     # (ok, fail, failed_keys)

    def __init__(self, db: Database, only: str | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.only = only          # 只抓某通道（语言名 / domain:X / HOT_KEY / None=全部）
        self.cancel = threading.Event()
        self._settings = db.all_settings()
        self._interests = self._settings.get("interests")
        self.rate_limit_reset: int | None = None   # 遇到额度耗尽时记录恢复时间

    def run(self):
        ok = fail = 0
        failed: list[str] = []
        plan = self._plan()
        if not self._preflight(plan):
            self.finished_all.emit(0, 0, [])
            return
        total = len(plan) * len(config.RANGES)
        done = 0
        for _, days in config.RANGES:
            for kind, key in plan:
                if self.cancel.is_set():
                    self.progress.emit("⏹ 已取消抓取")
                    self.finished_all.emit(ok, fail, failed)
                    return
                done += 1
                ch = self._channel_key(kind, key, days)
                label = {"hot": "热门榜", "rank": "热门榜", "trend": "热门榜",
                         "lang": "语言", "dom": "领域"}.get(kind, kind)
                self.progress.emit(
                    f"[{done}/{total}] 抓取 {label}:{key if kind not in ('hot', 'rank', 'trend') else ''} · 近{days}天 ...")
                try:
                    repos, period, ch_real, active_src = self._fetch_one(
                        days, kind, key, ch)
                    if not repos:
                        raise RuntimeError("未获取到数据")
                    ch = ch_real or ch     # 热门榜：实际成功的源决定通道键
                    self._store(days, ch, repos, period)
                    # R04：缓存写入成功后再提交本时间档的活动源，避免视图指向空通道
                    if active_src:
                        hot_source.set_active(self.db, days, active_src)
                    ok += 1
                    self.progress.emit(f"    ✔ {ch} 近{days}天：{len(repos)} 条"
                                       + (f"（{period}）" if period else ""))
                    self.channel_done.emit(days, ch)
                except Exception as e:
                    fail += 1
                    failed.append(ch)
                    from app.sources import http as _http
                    if isinstance(e, _http.RateLimitError) and e.reset_ts:
                        self.rate_limit_reset = e.reset_ts
                    self.progress.emit(f"    ✘ {ch} 近{days}天失败：{e}，保留旧数据")
                if (done < total and kind in ("lang", "dom")
                        and not self.cancel.is_set()):
                    time.sleep(7 if not self._token() else 1)
        self.finished_all.emit(ok, fail, failed)

    # ---------- 抓前额度预检 ----------
    def _preflight(self, plan: list[tuple[str, str]]) -> bool:
        """GitHub API 源默认禁用；启用后再检查未认证额度。"""
        if not self._api_enabled():
            src = self._settings.get("hot_source", "auto")
            needs = any(k in ("lang", "dom") for k, _ in plan)
            hot_api = any(k == "hot" for k, _ in plan) and src in (
                "opengithubs", "search")
            if needs or hot_api:
                self.progress.emit(
                    "⛔ GitHub API 源已默认禁用（设置「GitHub API 源」可手动启用），"
                    "本次跳过语言/领域/搜索类抓取；Trending/GH Archive 不受影响")
                return False
            return True
        src = self._settings.get("hot_source", "auto")
        hot_in_plan = any(k == "hot" for k, _ in plan)
        # auto 链含 API 源；gharchive/trending 单源不需要 API 额度
        hot_needs_api = hot_in_plan and src not in ("gharchive", "trending")
        needs_api = hot_needs_api or any(k in ("lang", "dom") for k, _ in plan)
        if not needs_api:
            return True
        try:
            from app.sources import http as _http
            info = _http.github_api("rate_limit", timeout=10)
            core = (info.get("resources") or {}).get("core") or {}
            remaining = int(core.get("remaining", 1))
            reset = int(core.get("reset", 0) or 0)
        except Exception:
            return True                    # 预检失败不阻塞正常抓取
        if remaining > 0:
            return True
        self.rate_limit_reset = reset or None
        import time as _t
        if reset:
            when = _t.strftime("%H:%M", _t.localtime(reset))
            wait = max(0, int((reset - _t.time()) / 60))
            hint = f"预计 {when} 恢复（约 {wait} 分钟）"
        else:
            hint = "稍等片刻自动恢复"
        self.progress.emit(
            f"⛔ GitHub API 额度已用完，{hint}，本次抓取已跳过"
            "（GH Archive 自建 / Trending 源不耗 API，可单独抓取）")
        return False

    # ---------- 计划 ----------
    def _plan(self) -> list[tuple[str, str]]:
        langs = self._settings.get("languages", [])
        domains = [d for d in self._settings.get("domains", [])
                   if d in config.DOMAIN_QUERY_KWS]
        plan: list[tuple[str, str]] = [("hot", config.HOT_KEY)]
        if self._api_enabled():
            plan += [("lang", l) for l in langs] + [("dom", d) for d in domains]
        if self.only is None:
            return plan
        if self.only == config.HOT_KEY:
            return [("hot", config.HOT_KEY)]
        if self.only.startswith(config.DOMAIN_PREFIX):
            key = self.only[len(config.DOMAIN_PREFIX):]
            return [("dom", key)] if ("dom", key) in plan else []
        # 侧栏可筛未跟踪语言，「抓取当前通道」也应支持（不再空计划）；
        # API 源禁用时语言通道不可抓。
        return [("lang", self.only)] if self._api_enabled() else []

    def _api_enabled(self) -> bool:
        return bool(self._settings.get("github_api_enabled", False))

    def _channel_key(self, kind: str, key: str, days: int) -> str:
        if kind == "lang":
            return key
        if kind == "dom":
            return config.DOMAIN_PREFIX + key
        # 热门榜：按当前数据源预取通道键（auto 时以实际成功的源为准）
        return hot_source.channel_for(self.db, days)

    def _token(self) -> bool:
        from app.sources import http
        return bool(http.token())

    # ---------- 单通道 ----------
    def _fetch_one(self, days: int, kind: str, key: str,
                   ch: str) -> tuple[list[Repo], str, str | None, str | None]:
        """返回 (repos, period, 实际通道键, 实际活动源)。"""
        if kind == "hot":
            return self._fetch_hot(days)
        n_pool = self._pool_n()
        if kind == "lang":
            repos = github_search.fetch_channel(
                days, "lang", key, n_pool, self._interests, self.cancel,
                lambda m: self.progress.emit(m))
        else:  # dom：定向词查询 + 本地精分类 + 领域内只保留本领域
            repos = github_search.fetch_channel(
                days, "dom", config.DOMAIN_QUERY_KWS[key], n_pool,
                self._interests, self.cancel, lambda m: self.progress.emit(m))
            repos = [r for r in repos if r.domain == key][:n_pool]
        return repos, "", None, None

    def _fetch_hot(self, days: int) -> tuple[list[Repo], str, str, str]:
        """本周热门：按设置的数据源抓取，返回 (repos, period, 通道键, 源名)。

        每个源 × 时间档独立落库；auto 走 OpenGithubs → GH Archive → 本地搜索，
        实际成功的源由调用方在写库成功后按时间档记录（R04）。
        """
        src = self._settings.get("hot_source", "auto")
        api_on = self._api_enabled()
        chains: dict[str, list[tuple[str, callable]]] = {
            "opengithubs": [("opengithubs", self._hot_opengithubs)],
            "gharchive": [("gharchive", self._hot_gharchive)],
            "trending": [("trending", self._hot_trending)],
            "search": [("search", self._hot_search)],
            "auto": ([("opengithubs", self._hot_opengithubs),
                      ("trending", self._hot_trending)]
                     if api_on else [("trending", self._hot_trending)]),
        }
        chain = chains.get(src, chains["auto"])
        last_err: Exception | None = None
        rl_err: Exception | None = None
        from app.sources import http as _http
        for name, fn in chain:
            try:
                repos, period = fn(days)
                ch = config.hot_channel(name, days)
                return repos, period, ch, name
            except Exception as e:
                last_err = e
                if isinstance(e, _http.RateLimitError) and rl_err is None:
                    rl_err = e
                self.progress.emit(f"    数据源 {name} 不可用：{e}，降级…")
        # 链路中有额度耗尽：保留 RateLimitError 类型（携带恢复时间）向上抛
        if rl_err is not None:
            raise rl_err
        raise RuntimeError(f"全部热门数据源失败（最后错误：{last_err}）")

    def _pool_n(self) -> int:
        """每通道条数：读设置（此前 GH Archive/搜索源硬编码 50，设置不生效）。"""
        return max(10, min(int(self._settings.get("pool_size", config.POOL_N)),
                           config.POOL_MAX))

    def _hot_opengithubs(self, days: int) -> tuple[list[Repo], str]:
        return opengithubs.fetch_rank(days, self._interests)

    def _hot_gharchive(self, days: int) -> tuple[list[Repo], str]:
        # 从本地聚合数据建榜（数据由后台同步线程持续积累，此步不下载）
        repos, period = gha_board.build_board(
            self.db, days, self._pool_n(), self._interests)
        self._enrich_gha(repos)
        return repos, period

    def _enrich_gha(self, repos: list[Repo], limit: int = 20):
        """GH Archive 榜无星数/简介：对前 limit 个补全详情（尽力而为）。

        GitHub API 源默认禁用时完全跳过，避免一开榜就消耗 core 配额。
        """
        if not self._api_enabled():
            self.progress.emit("    ℹ️ GitHub API 源已禁用，跳过 GH Archive 详情补全")
            return
        enriched = 0
        for r in repos[:limit]:
            if self.cancel.is_set():
                break
            try:
                info = github_search.fetch_repo(r.name)
            except Exception as e:
                self.progress.emit(f"    ⚠️ 详情补全中断（{e}），榜单保留原样")
                break
            r.stars = info["stars"]
            r.forks = info["forks"]
            r.pushed = info["pushed"]
            r.license = info["license"]
            r.archived = info["archived"]
            if info["description"]:
                r.description = info["description"]
            r.language = info["language"]
            enriched += 1
            if enriched < min(limit, len(repos)):
                time.sleep(0.6)
        if enriched:
            self.progress.emit(f"    ✔ 详情补全 {enriched} 个项目")

    def _hot_trending(self, days: int) -> tuple[list[Repo], str]:
        repos = trending.fetch_trending(
            days, self._settings.get("languages", []), self._interests,
            self.cancel, lambda m: self.progress.emit(m))
        return repos, "GitHub Trending"

    def _hot_search(self, days: int) -> tuple[list[Repo], str]:
        repos = github_search.fetch_channel(
            days, "hot", "", self._pool_n(), self._interests, self.cancel,
            lambda m: self.progress.emit(m))
        return repos, "本地搜索近似"

    # ---------- 落库 ----------
    def _store(self, days: int, channel: str, repos: list[Repo], period: str):
        # 榜单类通道自带真实增量；搜索类通道套用本地快照基线
        if config.needs_baseline(channel):
            self._apply_growth(days, channel, repos)
        self.db.save_cache(channel, days, repos, period)
        self.db.save_history(channel, days, repos)
        # 观察名单自动采样（收录全部池项目）
        watchlist.record_samples(self.db, repos)

    def _apply_growth(self, days: int, channel: str, repos: list[Repo]):
        """搜索类通道用本地 6~8 天快照差值，标注为部分覆盖的净增长。"""
        base, taken_at = self.db.baseline_snapshot(channel, days)
        today = datetime.now().strftime("%Y-%m-%d")
        for r in repos:
            r.metric = "net_stars"
            r.window_days = days
            r.window_end = today
            if r.stars is not None and r.name in base:
                delta = r.stars - base[r.name]
                r.growth_7d = delta
                r.growth_value = delta
                r.growth_window = "本地快照基线"
                r.window_start = taken_at[:10] if taken_at else ""
                r.completeness = "partial"   # 基线在 6~8 天区间，非精确 7 天
            else:
                r.growth_7d = None
                r.completeness = "missing"
