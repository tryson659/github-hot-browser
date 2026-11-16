# 变更记录

格式参考 Keep a Changelog；版本遵循语义化版本。

## [2.5.0] - 未发布

### 新增（工具库 Qt 闭环）

- 第 4 个一级入口「🧰 工具库」；分类侧栏（全部工具/待分类/自定义）、
  多分类项目卡片、分类标签、移出工具库。
- 热门/收藏/观察卡片「＋ 工具库」；工具栏「📥 收录当前列表」批量收录；
  收录时多选分类、可新建分类，不选进入待分类。
- 分类管理对话框：新建、重命名、隐藏/显示、上移/下移、删除（只删关系，
  可选转移目标）；删除后项目、收藏、追踪、备注、历史保留。
- 工具库「＋ 手动收录」：owner/repo 或 GitHub URL，多值解析校验后选分类。
- 工具库「批量分类」「批量移出」对当前分类/筛选结果生效。
- 分类内「近7天增长」只读取同窗口、valid 的 net_stars 观测；新增总 Star/
  最近收录排序；无数据显示未知/排后。
- 工具库离线搜索（名称/描述/用途/分类）；收录不自动收藏或追踪。
- 工具栏 **💾 数据管理**：创建一致性备份、只读校验包、登记下次启动恢复；
  启动时在打开数据库前整库替换并生成恢复点；每日启动备份最多保留 7 份。
- 升级/启动自动播种默认分类；已删除/改名的 seed 不恢复。

### 新增（GH Archive 回填可见）

- GH Archive 数据源显示账本覆盖：已取得/应有小时、失败、未取、实际覆盖。
- 侧栏「🗄 补齐近7天/30天 GH Archive」：后台按日历窗口回填，支持取消，
  已下载小时跳过；完成后自动重建榜单。
- 数据源标签缩短，避免工具栏挤压；`auto` 仍不包含 GH Archive/搜索。

### 新增（GitHub API 源默认关闭）

- 新增设置 `github_api_enabled`，默认 `False`；未认证 core 60 次/小时，
  Search 10 次/分钟，实际共享出口经常为 0。
- `auto` 在关闭时只走 GitHub Trending；OpenGithubs/Search 下拉禁用。
- 语言/领域抓取、收藏刷新、添加观察、检查更新、Release 查询、GH Archive
  详情补全在关闭时跳过并提示原因；GH Archive 建榜/回填、工具库、备份不受影响。
- 设置页新增「GitHub API 源」勾选；启用后 OpenGithubs/Search 才可选。
- 新增 `docs/api-sources.md`。

### 修复

- 清空卡片时立即从画面移除旧控件，避免刷新瞬态重影。
- 回填取消保留已完成小时；回填统计写 `source_runs`。
- 官方 Star History 解析按真实响应修正：`week` 为 Unix 秒、跳过当前周未来
  日期，避免 `week` 字符串假设导致解析失败。
- 官方 GitHub Search 真实请求已验证（Token 复验 total=3136、100 条）。
- OpenGithubs 月榜周期格式修复：文件名 `202610.md` 不再拼成
  `202620261001`，改为 `20261001`；新增周/月目录发现与真实表格 fixture 测试。
- 筛选侧栏计数改为当前热门池（“全部语言 0 但列表有数据”修正）；语言/领域/标签
  明确标注为交集/单选。
- 标签下拉合并 `repo_topics` 缓存并回填卡片 topics，补齐后标签可筛选；
  新增 `docs/filters.md`。
- 使用用户 Token 真实复验：Star History 203 个完整日（最近 7 天 193）、
  OpenGithubs 周榜 20/月榜 30、GitHub Search total=3136；当前日/未来日已跳过。

### 验证

- 完整 pytest **378 passed**（PySide6 6.12，Qt offscreen，退出码 0）。
- 当前 2.5.0 已重建 `dist/GitHub热点浏览器/` 并生成
  `GitHubHotBrowser-2.5.0-win64.zip`（约 46 MB）与 SHA-256；旧 2026-11-09
  构建、`build/`、probe 脚本和日志已删除。
- 离屏/桌面 Qt 渲染核对工具库与 GH Archive 空态布局。

## [2.4.0] - 未发布

### 新增（阶段 A 工具库）

- v5 表 `library_entries`、`categories`、`library_categories`；仓库元数据补充
  stars/forks/pushed/license/created；schema 版本 5。
- 默认 10 类工具库分类（稳定 seed_key），自定义分类、重命名、排序、隐藏、
  删除墓碑；删除分类只移除关系，可选转移到其他分类。
- 收录 ≠ 收藏 ≠ 追踪：移出工具库保留收藏/追踪/观测；重复收录只保留一条。
- 离线分类/待分类/关键词浏览；服务与 CLI（Qt 页面待接线）。
- `docs/library.md`。

### 新增（阶段 B 本地备份恢复）

- 自包含 ZIP（manifest + 一致性 SQLite），manifest 含格式/schema/版本/时间/
  大小/SHA-256/业务计数/脱敏声明。
- 备份自动删除 `github_token` / `translate_apikey` / `translate_email`，
  `secure_delete` + `VACUUM` 重建并扫描确认凭证不残留。
- 恢复预检拒绝损坏/截断/过新 schema/路径穿越/异常压缩/哈希不符；恢复前生成
  恢复点，临时文件原子替换，失败回滚；旧 WAL/SHM 不套到新库。
- `docs/backup-restore.md`。

### 已知限制（2.4.0）

- 工具库 Qt 页面、分类内增长排序、自动分类建议（阶段 D）未实现。
- 备份恢复未接 UI/自动轮转/加密；凭证仍保存在业务库，仅备份脱敏。
- 恢复需用户先关闭应用；运行中任务协调未实现。
- 官方历史/真实网络/真实用户库迁移/Qt 测试仍未验证。

### 修复（2.4.0 验证阶段）

- 迁移读取旧 JSON 时接受 UTF-8 BOM（`utf-8-sig`），避免 Windows 工具写出的
  BOM 文件被误判为损坏。
- 测试会话结束关闭残留 Qt 顶层窗口并保持 QApplication 引用，修复完整套件
  偶发 `0xC0000409` 退出崩溃。

### 验证（2026-11-15）

- 完整 pytest 351 passed（PySide6 6.12 临时离线加载，Qt offscreen，退出码 0）。
- 真实开发库副本 v1→v5 迁移、工具库、备份/恢复回退通过。
- GH Archive 两小时真实下载、Trending 页面、MyMemory、ecosyste.ms 实测通过。
- 未验证：未认证 GitHub API 配额为 0 导致官方 Star History/OpenGithubs/Search
  契约未完成；真实桌面/exe/旧 v17b 数据迁移未验收。

## [2.3.0] - 未发布

### 新增（批次 5 事件小时账本）

- v4 表 `archive_hours`：`source + hour` 唯一，状态 success/empty/failed，
  记录事件数、下载时间与错误；schema 版本 4。
- `gha_board.catch_up` 写入账本；失败不推进数据覆盖，但游标可继续调度；
  游标已追平仍有失败缺口时下一轮优先重试，不再永久忽略。
- 已成功且缓存存在的同一小时不重复下载/累计；缓存被清理时重新下载。
- `archive_coverage` 与 `python -m app.services.archive_status --days 7`
  输出成功/空包/失败/未取与最近一次 source_run。
- GH Archive 周期同步写入 `source_runs`（success/partial/failed）。

### 已知限制（2.3.0）

- 未实现小时包压缩文件校验和与账本长期清理策略。
- 事件账本“已取得小时数”不等于事件完整率；未接入多事件源合并。
- 官方历史/真实网络/用户库迁移/Qt 视图仍未验证。

## [2.2.0] - 未发布

### 新增（批次 4 独立追踪与用户数据）

- v3 增量表：`tracking`、`user_tags`、`repository_tags`；schema 版本 3。
- `app/services/tracking.py`：独立 `next_due` 调度、暂停/恢复、删除墓碑、
  总量采样更新、失败推迟（不写入伪造 0 采样）、用户标签与 GitHub topics 分离。
- `app/services/user_data.py`：schema_version=1 的 JSON 导出/导入，支持
  merge/skip/replace、备注不覆盖、标签并集、重复导入幂等、逐项错误统计、
  可选总量采样历史；临时文件 + 原子替换。
- CSV 导出对公式前缀文本做转义，防 Excel 公式注入。
- 用户数据格式见 `docs/user-data.md`。

### 已知限制（2.2.0）

- 追踪调度只完成服务/存储层，未接入 Qt 定时器与后台采样；用户数据未接入
  文件选择/预览控件。
- 官方历史接口真实契约、真实用户库 v2/v3 迁移仍未验证。
- 界面/线程类测试需安装 PySide6 的干净环境重跑；exe 未用当前源码重建。

## [2.1.0] - 未发布

### 新增（批次 2 业务模型）

- `app/domain/`：Metric / WindowSpec / WindowResult / 排序 / 仓库身份规则（纯 Python）。
- `Repo` 增加 metric/window_days/window_start/window_end/completeness；
  `window_value(days)` 统一当前时间档排序；导出增加指标类型、窗口范围与完整性。
- 来源分档：auto 仅在 OpenGithubs/Trending 报告源之间替代；GH Archive 事件计数
  与搜索候选必须显式选择；搜索不再用"总 Star/年龄"冒充窗口增长。
- 统计契约见 `docs/metrics-contract.md`。

### 新增（批次 3 官方历史与观测存储）

- v2 SQLite 表（只增不改旧表）：`repositories`、`repository_aliases`、
  `candidate_sources`、`star_samples`、`metric_observations`、`source_runs`；
  数据库 schema 版本 2。
- `app/storage/`：仓库身份、总量采样、指标观测、数据源任务记录的公开仓储操作；
  批量观测单事务写入，失败整体回滚。
- `app/sources/github_history.py`：官方 Star History 只读适配器，解析
  `week/total/days`，未提供日不填 0；契约用固定 JSON 样本测试。
- `app/services/observations.py`：历史日桶与总量采样的写入/窗口计算；
  支持 `python -m app.services.observations owner/repo --days 7` 只读诊断。
- 数据库表说明见 `docs/storage.md`。

### 已知限制（2.1.0）

- 官方历史接口未用真实请求验证：响应结构、分页、限流、桶边界与退星语义均为
  未验证；UI 尚未接入官方日桶视图。
- 界面/线程类测试与真实网络契约需在安装 PySide6 的干净环境重跑。
- exe 未用当前源码重建；旧版 v17b 功能对等矩阵未完成。

## [2.0.0] - 未发布

### 新增

- PySide6 + SQLite 模块化重构（`core / services / sources / ui`）。
- 多源热门榜、组合筛选、收藏/备注/里程碑、增长观察、GH Archive 增量同步、
  名单导入、多格式导出、翻译与更新提醒。
- 运行时加固：单实例锁、崩溃日志、WAL 一致性启动备份、异常退出提示。
- 开源工程脚手架：README、MIT LICENSE、依赖清单、贡献/安全/行为准则、
  Issue/PR 模板、GitHub Actions CI、`.gitignore`、数据目录与便携模式。

### 修复（2026-11-15 审阅 R01–R12）

- R01：启动备份改用 SQLite 在线备份接口，锁内校验后原子替换，失败可见并保留
  上一份有效副本；迁移入口共用同一锁与备份流程。
- R02：统一登记后台线程；关闭窗口先协作取消，等全部线程结束才关闭数据库。
- R03：观察增长按请求窗口选择基线，返回真实采样跨度，不再把长期平均外推成
  周增长；不足窗口/样本滞后明确标注。
- R04：自动数据源的活动源按 7/30 天分别记录，缓存写入成功后才提交。
- R05：修正正常退出/异常退出提示反向的问题。
- R06：迁移历史以 `(channel, days, taken_at)` 唯一，重复迁移不新增；设置与备注
  不覆盖迁移后的手工修改；损坏 JSON、缺目录计入错误并返回非零退出码。
- R07：名单解析按完整 token 边界识别，`a/b/c` 不再截取为 `a/b`；GitHub 深层
  链接归一到仓库根；其他域名忽略。
- R08：环境变量 `GITHUB_TOKEN` 优先，UI 不再修改进程环境；清空设置不影响外部配置。
- R09：导出新增来源、指标、窗口、数值与覆盖率；未知值留空而非 0。
- R10：GH Archive 统计有效覆盖小时数，按覆盖时长折算日均；样本不足明确标注，
  不再按整个 7/30 天窗口直除。
- R11：分类规则移入 `core`；GH Archive 聚合移入 `services`；迁移改用公开数据库
  方法；数据目录解析与模块导入解耦；主窗口拆出任务登记与数据源选择规则。
- R12：默认数据目录改为用户可写位置，支持显式便携模式；补齐开源维护材料与 CI。

### 已知限制

- 界面/线程类测试与真实网络契约需在安装 PySide6 的干净环境重跑。
- exe 未用当前源码重建；旧版 v17b 功能对等矩阵未完成。
