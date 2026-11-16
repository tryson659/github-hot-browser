# -*- coding: utf-8 -*-
"""数据源：GitHub 官方 Star History（只读）。

接口：`GET /repos/{owner}/{repo}/stargazers/history`。实测 2026-03-10 版返回
30 个周对象，字段 `week`（Unix 秒，周起点）、`total`、`days`（7 个日计数，
星期日开始）。只做解析与标准化，不解释为“净增长”，也不操作数据库。

2026-11-15 曾用真实请求验证：`week` 是 int 时间戳而非 ISO 字符串；当前周
未来日期为 0，解析时跳过，避免把未发生的 0 当观测；当天未走完也跳过。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.domain.identity import normalize_repo_name
from app.sources import http

API_VERSION = "2026-03-10"
PARSER_VERSION = "github_history/1"


class HistoryParseError(ValueError):
    """响应不符合已记录契约（结构、日期或计数非法）。"""


@dataclass(frozen=True)
class HistoryDay:
    day: date
    count: int


@dataclass(frozen=True)
class StarHistory:
    repo: str
    days: tuple[HistoryDay, ...]
    fetched_at: datetime
    parser_version: str = PARSER_VERSION

    def buckets(self) -> dict[date, int]:
        return {d.day: d.count for d in self.days}


def _week_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("weeks", "data", "history"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise HistoryParseError("响应不是周数组，也不含 weeks/data/history 列表")


def _week_start(value) -> date:
    """官方响应用 Unix 秒（实测 2026-03-10 版），兼容 ISO 字符串样本。"""
    if isinstance(value, bool):
        raise HistoryParseError("week 不能是布尔值")
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).date()
        except (OverflowError, OSError, ValueError) as e:
            raise HistoryParseError(f"week 时间戳非法：{value!r}") from e
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError as e:
            raise HistoryParseError(f"week 日期非法：{value}") from e
    raise HistoryParseError("week 必须是 Unix 秒或 ISO 日期")


def parse_history(payload, repo: str = "",
                  fetched_at: datetime | None = None) -> StarHistory:
    """解析官方历史响应；未知结构抛出 HistoryParseError。

    实测响应是 30 个周对象的列表，week 为 Unix 秒；当前周未走完的
    未来日期不写入，避免把未发生的 0 当观测。
    """
    fetched_at = fetched_at or datetime.now(timezone.utc)
    today = fetched_at.astimezone(timezone.utc).date()
    buckets: dict[date, int] = {}
    for index, week in enumerate(_week_items(payload)):
        if not isinstance(week, dict):
            raise HistoryParseError(f"第 {index} 周不是对象")
        first = _week_start(week.get("week"))
        days = week.get("days")
        if not isinstance(days, list) or len(days) > 7:
            raise HistoryParseError(f"第 {index} 周 days 不是最多 7 项的数组")
        for offset, count in enumerate(days):
            day = first + timedelta(days=offset)
            if day >= today:
                continue              # 当天未走完/未来日期，不写成 0
            if count is None:          # 未提供该日
                continue
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise HistoryParseError(
                    f"第 {index} 周第 {offset} 日计数非法：{count!r}")
            # 后出现的桶视为修订，覆盖同一天
            buckets[day] = count

    return StarHistory(
        repo=repo,
        days=tuple(HistoryDay(day=d, count=buckets[d]) for d in sorted(buckets)),
        fetched_at=fetched_at,
    )


def fetch_history(name: str, timeout: int = 20) -> StarHistory:
    """请求单仓历史；网络/限流错误由 http 层抛出，解析错误由本模块抛出。"""
    repo = normalize_repo_name(name)
    payload = http.github_api(
        f"repos/{repo}/stargazers/history", timeout=timeout,
        headers={"X-GitHub-Api-Version": API_VERSION})
    return parse_history(payload, repo=repo)
