# 工具库（服务 + Qt）

工具库按"能解决什么问题"组织仓库，与热门发现、收藏、追踪是不同状态：
**收录不等于收藏，收藏也不自动开启追踪**。实现以 `app/domain/library.py`、
`app/storage/library.py`、`app/services/library.py` 与
`app/ui/library_dialogs.py` 为准。

## 1. 状态与关系

| 状态 | 存储 | 说明 |
|---|---|---|
| 工具库收录 | `library_entries` | 项目在个人工具库中，可属于多个分类 |
| 收藏 | `favorites` | 与工具库独立；移出工具库不取消收藏 |
| 追踪 | `tracking` | 与工具库独立；移出工具库不停止追踪 |

删除工具库条目只删除 `library_entries` 与分类关系；仓库身份、收藏、追踪、
观测与采样保留。

## 2. 默认分类与模板

- 默认分类定义在 `DEFAULT_CATEGORIES`，使用稳定 `seed_key`（ppt/docs/pdf_ocr/…）。
- 首次启动调用 `ensure_defaults()` 播种；重复执行不重复创建。
- 已删除（墓碑 `deleted=1`）或已改名的 seed 不会在升级时恢复；模板版本记录在
  设置 `__library_template_version__`。
- 分类 ID：默认分类由 `seed_key` 生成稳定 UUID，自定义分类使用 UUID4。
- 分类名称显示值可改；同层去重使用规范化名称（压缩空白 + casefold），
  非删除分类有部分唯一索引。

## 3. 自定义分类

- 新建、重命名、排序、说明/图标、隐藏、删除；名称不能为空、最长 40 字符。
- 删除分类只删除关系并保留条目；服务层可选 `move_to` 把受影响项目转到另一分类。
  不指定目标时，条目进入动态“待分类”（无有效分类关系）。当前 Qt 删除确认
  暂不提供转移目标选择。
- 同一项目可属于多个分类；`library_categories` 以 `(repo_id, category_id)` 唯一。
- 分类关系写入在事务内；人工分类优先于未来自动建议（阶段 D）。

## 4. Qt 界面

- 主窗口第 4 个一级入口「🧰 工具库」；侧栏为「全部工具 / 待分类 / 自定义分类」，
  显示每个分类的项目数；支持关键词离线搜索。
- 热门、收藏、观察卡片有「＋ 工具库」；已在库内显示「🗂 分类」和分类标签，
  工具库榜内另有「移出」。
- 「📥 收录当前列表」把当前筛选结果批量收录；弹窗可多选分类、新建分类，
  不选则进入待分类。
- 「＋ 手动收录」输入 owner/repo 或 GitHub URL（可多个），先解析校验再选分类。
- 「🗂 批量分类」对当前分类/筛选结果统一改分类；「🗑 批量移出」只移出工具库。
- 「🗂 管理分类」支持新建、重命名、隐藏/显示、上移/下移、删除；删除前可选择
  把项目转移到另一分类，确认框显示影响项目数；只删关系，不删项目/收藏/
  追踪/备注/历史。
- 工具库榜复用项目卡片：收藏、备注、Release、翻译、打开仍可用；增长严格按
  metric/window，缺数据不显示假增长。

## 5. 收录与浏览

```powershell
# 服务/CLI（无界面时也可用）
python -m app.services.library categories
python -m app.services.library add owner/repo --category "文档工具"
python -m app.services.library list
python -m app.services.library remove owner/repo
```

- `add_repo(repo, categories)` 复用 `repositories`，写入 stars/forks/pushed/
  license/created 元数据；重复收录只保留一条条目并刷新元数据。
- `entries(category_id=..., uncategorized=..., query=..., sort=recent|stars|name)`
  支持离线搜索（仓库名/描述/用途说明）与分类/待分类视图。
- 当前排序支持最近收录、总 Star、名称、最近更新；分类内「近7天增长」只读取
  **同窗口、`completeness=valid` 的 `net_stars` 观测**，没有数据就留空/排后，
  不用 0 或总 Star 替代。30 天 `net_stars` 通常没有合法观测，此时同样显示未知。
  候选发现、自动分类建议属于阶段 D，尚未实现。

## 6. 表

| 表 | 关键字段 |
|---|---|
| `library_entries` | `repo_id` 主键、added_at、user_summary、source、updated_at |
| `categories` | id、name、normalized_name、sort_order、description、seed_key、hidden、deleted、template_version |
| `library_categories` | repo_id + category_id 唯一、origin、confirmed_at |

阶段 D 的 `category_rules`、`category_suggestions` 尚未建表；自动分类规则与
建议/拒绝状态需在该阶段单独设计，不覆盖人工分类。

## 7. 未验证 / 限制

- 候选发现、自动分类建议（阶段 D）未实现。
- 30 天增长排序依赖合法的 `net_stars` 观测；没有数据时按未知处理，不做跨口径比较。
- 工具库大量分类/项目的性能、真实用户库播种未验收。
- 备份恢复 UI 已支持创建/校验/登记重启恢复；自动加密/凭证分离仍未做。
