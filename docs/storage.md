# 存储与数据迁移（v2.1 批次 3）

SQLite 仍是唯一持久化后端，模块化单体，不引入服务或缓存层。旧表继续只读兼容；
v2 表只新增，不修改 `cache/history/favorites` 的结构。实现以
`app/core/db.py` 与 `app/storage/store.py` 为准。

## 1. 版本

- `Database.schema_version` 来自设置键 `__schema_version__`，当前为 `5`。
- 旧库启动时通过 `CREATE TABLE IF NOT EXISTS` 自动补齐 v2–v5 表，并对
  `repositories` 只增列（stars/forks/pushed/license/created）；不删除旧数据。
- 迁移前仍由 `runtime.backup_database` 生成一致性备份（R01）。

## 2. v2 表

| 表 | 关键字段 | 用途 |
|---|---|---|
| `repositories` | `id`、`github_id` 可空唯一、`full_name`（小写唯一）、url、元数据时间 | 稳定仓库身份；名称存储为小写，展示名可由来源保留 |
| `repository_aliases` | `provider + name` 唯一、`repo_id` | 仓库改名/迁移后继续关联同一身份 |
| `candidate_sources` | `repo_id + source` 唯一、first/last_seen | 候选来源追溯，不累加来源增长值 |
| `star_samples` | `repo_id + sampled_at` 唯一、stars ≥0、run_id | 总量采样净增长基线；失败请求不写入 0 |
| `metric_observations` | `observation_key` 唯一、metric/source、窗口、value、完整性、版本 | 报告/官方日桶/事件聚合；同日修订覆盖，不重复累计 |
| `source_runs` | run_id、source、起止、status、error、成功/失败数 | 同步诊断与恢复 |
| `tracking` | `repo_id` 主键、favorite/enabled/paused/removed、note、采样间隔、next_due_at/last_sampled_at | 个人追踪状态；删除留墓碑，自动发现不能复活 |
| `user_tags` | tag 主键、color | 用户标签登记 |
| `repository_tags` | `repo_id + tag + origin` 主键 | 区分用户标签与 GitHub topics |
| `archive_hours` | `source + hour` 主键、status（success/empty/failed）、event_count、downloaded_at、error | 事件小时账本；缺包/失败可重试，游标只调度 |
| `library_entries` | `repo_id` 主键、added_at、user_summary、source | 工具库收录；与收藏/追踪独立 |
| `categories` | id、name、normalized_name、sort_order、seed_key、hidden、deleted、template_version | 默认/自定义分类；删除墓碑 |
| `library_categories` | `repo_id + category_id` 主键、origin | 项目多分类关系 |

暂未建表：阶段 D 的 `category_rules`、`category_suggestions`、`backup_records`。

## 3. 写入约定

- 服务层通过 `Store` 访问，不直接拼 SQL；`Store` 只使用 `Database.write/query`
  公开入口，不持有连接。
- 批量观测（官方历史一整个响应）使用 `save_observations`，在一个事务内
  `executemany`；校验失败在任何写入前抛出，避免"完成但只有一半"。
- 采样时刻统一保存为 UTC ISO（`YYYY-MM-DDTHH:MM:SSZ`），按字典序可直接排序。
- `metric_observations.value` 允许 `None` 表示缺测；写入层拒绝非整数与 bool。
- 历史日桶的 `observation_key = github_history:{repo_id}:{YYYY-MM-DD}`，
  同日修订会覆盖旧值。

## 4. 身份与去重

- 规范化规则见 `app/domain/identity.py`：GitHub URL/深层路径归一，裸三级路径
  拒绝，owner/repo 大小写不敏感。
- `Store.upsert_repository` 以规范化小写名称作为唯一键；`github_id` 可后续补齐。
- 仓库改名时写 `repository_aliases`，`Store.repository` 先按当前名、再按别名查找。
- 一个仓库的多源“计数”不相加；只合并身份与来源证据（`candidate_sources`）。

## 5. 读取与计算

- `Store.history_buckets(repo_id)` 返回 `{date: count}`，`None` 观测跳过。
- `app/services/observations.py`：
  - `record_history` / `sync_history`：写入日桶并记录 `source_runs`；
  - `history_window`：按滚动窗口汇总，缺桶返回 `PARTIAL`；
  - `record_sample` / `net_window`：总量采样净增长，缺端点返回 `MISSING`。
- 窗口/排序规则见 [统计契约](metrics-contract.md)。

## 6. 追踪与用户数据

- `tracking` 与榜单缓存独立：`next_due_at` 决定采样时间，应用关闭期间不采样，
  重启后按持久化时间继续。
- `removed=1` 是删除墓碑：自动发现只调用 `ensure_tracking`，不会把删除/暂停项
  重新启用；只有用户显式 `add_tracking` 才清除墓碑。
- 用户数据 JSON（`app/services/user_data.py`）只导出追踪状态、备注、用户标签与
  可选总量采样；不导出设置、Token 或翻译凭证。导入默认 merge，不覆盖非空备注
  与暂停状态；支持 `merge` / `skip` / `replace`。格式见 [用户数据说明](user-data.md)。

## 7. 事件小时账本（批次 5）

- `archive_hours` 每个 `source + hour` 一行；状态 `success`（有事件）、`empty`
  （成功但 0 事件）、`failed`（抓取失败，保留错误）。未取小时不建行。
- `gha_board.catch_up` 下载成功后写账本；失败写入 `failed` 并继续尝试其他小时，
  游标只表示调度进度。游标已追平但账本仍有失败缺口时，下一轮优先重试缺口，
  不会永久忽略。
- 已成功且缓存仍存在的同一小时不会重复下载/累计；缓存被清理时重新下载。
- `archive_coverage` 输出成功/空包/失败/未取；`window_coverage` 仍表示数据覆盖，
  两者都不等于 GitHub 全站事件完整率。诊断命令：

```powershell
python -m app.services.archive_status --days 7
```

## 8. 工具库存储

- `library_entries` 只表示“已收录”，不修改 `favorites`/`tracking`。
- `categories` 默认 seed 由 `seed_key` 生成稳定 UUID；删除为 `deleted=1` 墓碑，
  升级不恢复；名称去重依赖 `normalized_name` 的部分唯一索引。
- 删除分类在一个事务内删除 `library_categories` 关系；可选转移到目标分类；
  条目本身保留，无关系时进入动态“待分类”。
- 详细语义见 [工具库说明](library.md)。

## 9. 兼容与回滚

- 旧应用读取旧表；新表为空不影响旧功能。
- 新版本启动旧库时自动增加 v2 表；回退旧版本只需忽略新表，数据不丢失。
- 不可靠的旧增长值保留原记录与来源，不升级为“已验证净增长”。

## 10. 未验证

- 官方 Star History 真实日桶已实测（203 个完整日，最近 7 天 193）；分页/限流重试/
  退星重写语义未实测。
- SQLite 仅在本机 pytest 中使用临时库与真实开发库副本验证；未在真实用户库上执行 v2–v5 原地迁移。
- 追踪调度只验证了纯逻辑与持久化，未接入 Qt 定时器/后台采样。
- 事件账本未实现压缩文件校验和，也未做长期容量/清理策略验证。
