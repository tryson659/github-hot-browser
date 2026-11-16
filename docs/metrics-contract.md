# 统计契约（v2.1 批次 2）

本文固定"发现候选 → 获取观测 → 计算榜单"的口径约定。实现以
`app/domain/` 与 `app/core/models.py` 为准；本文只写契约与边界，不替代源码。

## 1. 产品承诺与非目标

- 承诺：**候选增长榜**——从报告、Trending、搜索、事件与用户导入获得仓库集合，
  对候选做同口径、同窗口的比较与排序。
- 非目标：GitHub 全站完整增长排名。标签筛选、来源榜单只代表候选池内结果，
  不能表述为"全站某标签 Top N"。
- 候选发现与增长测量分离：搜索只负责找到仓库，不把"总 Star / 项目年龄"或
  `created/pushed` 条件当作窗口增长。

## 2. 指标（metric）

| 代码 | 定义 | 允许的展示与比较 |
|---|---|---|
| `net_stars` | 两个实际采样时刻的总 Star 差值 | 可为负；缺起点即未知，不填 0；追踪榜与本地快照基线使用 |
| `github_history_stars` | GitHub Star History 接口的日桶计数 | 按接口语义展示；不解释为“历史净增长”；真实结构已验，完整日桶/退星未验 |
| `observed_star_events` | 事件源实际捕获的 WatchEvent 数 | 必须同时展示采集范围/缺口；不等于总量差值 |
| `provider_reported` | 第三方报告声明的增长值 | 保留报告期、来源与原始说明，不推断为滚动窗口 |

指标不同不得相加，也不得在界面或导出中无提示混排。自动模式只在同指标来源之间
替代：`AUTO_SOURCE_CHAIN = ("opengithubs", "trending")`（均为 `provider_reported`）；
事件计数与搜索候选必须由用户显式选择。

## 3. 窗口（window）

- 滚动窗口目标为半开区间 `[T−N天, T)`，统一截止时刻 T（UTC）。
- 报告与日桶保留其原始区间；报告期未知时只显示"来源报告榜"，不冒充最近 30 天。
- 日桶只支持日粒度；跨来源桶边界不保证与 UTC 对齐时分开展示。
- 缺少起点/终点样本、缺桶或未结束日均不得填 0；用 `null` + 原因表示。
- 实际观测范围（`window_start`/`window_end`）与完整性状态必须随结果保存。

`Repo.window_days` 记录该值对应的窗口天数；跨窗口值不能用于当前时间档排序
（`Repo.window_value(days)` 返回 `None`）。

## 4. 完整性（completeness）

| 值 | 含义 |
|---|---|
| `valid` | 通过该来源契约校验（不代表全 GitHub 完整覆盖） |
| `partial` | 有数据，但覆盖不足、边界偏移或来源本身是报告近似 |
| `missing` | 缺测/无法计算，不能用 0 代替 |

排序默认只把 `valid` 放入可比区；`partial` 仅在显式允许时参与，否则与 `missing`
一起置于末尾。`stale`（样本明显滞后）同样置尾。

## 5. 排序

1. 按 metric/window/质量筛选；
2. 按窗口值降序，负值自然排在 0 之后；
3. 同分按稳定仓库标识（优先 GitHub id，其次规范化名称）；
4. 缺测、过期数据单列末尾；
5. 标签、语言等筛选在排序前应用。

实现：`app/domain/ranking.py::rank_candidates`；当前 UI 的"近7天增长"排序通过
`Repo.window_value(days)` 使用当前时间档，不再固定读取 `growth_7d`。

## 6. 来源分档

| 来源 | 指标 | 角色 |
|---|---|---|
| OpenGithubs 周/月榜 | `provider_reported` | 报告榜；保留报告期，缺语言/topics 为 `partial` |
| GitHub Trending | `provider_reported` | 发现/报告；页面声明值，不当作净增长 |
| GH Archive 自建 | `observed_star_events` | 事件证据；展示覆盖小时与事件数 |
| GitHub Search | 无成长指标 | 只发现候选与补元数据 |
| 收藏/追踪/本地快照 | `net_stars` | 实际采样差值；6~8 天基线标记 `partial` |

## 7. 官方历史与观测存储

- 适配器：`app/sources/github_history.py` 解析 `week/total/days`；实测
  `2026-03-10` 版返回 30 个周对象，`week` 是 Unix 秒（周起点），`days` 为
  7 个日计数。未提供的日不写入、不填 0；当前周未来日期跳过；同一天后出现
  的桶视为修订。
- 存储：`metric_observations` 以 `observation_key` 唯一，日桶键为
  `github_history:{repo_id}:{YYYY-MM-DD}`；`star_samples` 记录实际采样时刻。
- 计算：`app/services/observations.py` 复用 `history_growth` / `net_growth`；
  缺桶返回 `PARTIAL`，缺端点返回 `MISSING`。
- 表结构与写入事务见 [存储说明](storage.md)。

## 8. 未验证

- GitHub Star History 已用 Token 实测：203 个完整日桶（2026-04-26～11-14），
  最近 7 天合计 193；当天未走完/未来日期跳过。分页、限流重试与退星重写语义
  尚未验证。
- GitHub Search 实测 total=3136、100 条；OpenGithubs 周榜 20/月榜 30 实测通过。
- 报告来源的真实更新频率、Trending 页面结构与访问策略未做长期验证。
