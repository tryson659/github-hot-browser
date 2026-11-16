# GitHub 热点项目浏览器 v2

个人 GitHub 热点项目浏览器：聚合多数据源热门榜、组合筛选、收藏与增长观察，
Windows 桌面应用（PySide6 + SQLite）。

> 当前版本：**2.5.0**。工具库、GH Archive 回填、数据管理、API 源开关已实现；
> 完整测试 378 passed。真实桌面人工操作、exe 安装/升级矩阵与退星/分页边界仍待验收。
> 代码仓库：<https://github.com/tryson659/github-hot-browser>

## 下载与安装

普通用户：

1. 打开 [Releases](https://github.com/tryson659/github-hot-browser/releases)；
2. 下载 `GitHubHotBrowser-2.5.0-win64.zip` 并解压；
3. 双击 `GitHub热点浏览器.exe`，无需 Python。

源码用户：

```powershell
git clone https://github.com/tryson659/github-hot-browser.git
cd github-hot-browser
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.main
```

首次启动默认数据目录为 `%LOCALAPPDATA%\GitHubHotBrowser\data\`；便携模式见下文。

## 功能

- **多源候选与报告榜**：OpenGithubs 周/月报告、GH Archive 事件计数、GitHub
  Trending、本地搜索候选；自动模式只在报告源之间替代，不静默切换统计口径。
- **指标与窗口契约**：净增长、官方日桶、事件计数、报告值分别建模；`Repo`
  保存指标、窗口天数、实际范围与完整性，排序按当前时间档取值（见
  [统计契约](docs/metrics-contract.md)）。
- **官方 Star History（只读，已用 Token 验证）**：解析官方按周/日的 Star 历史，
  已实测 203 个完整日桶；写入 v2 观测表并按窗口汇总；接口契约与存储见
  [存储说明](docs/storage.md)。
- **筛选与排序**：语言（单选）× 领域 × 关键词 × 标签交集，分页渲染，历史快照回看；
  侧栏计数来自当前热门池，标签补齐后可从 DB 缓存筛选。
- **收藏跟踪**：备注、标签、里程碑提醒、收藏基准、数据刷新。
- **增长观察**：按窗口计算日均增长，明确标注采样跨度与估计值，不把长期平均
  外推成周增长。
- **独立追踪**：收藏/导入的仓库按自己的 `next_due` 采样，暂停/删除状态不会被
  自动发现重置；支持用户标签与 GitHub topics 分开存储。
- **用户数据往返**：版本化 JSON 导出/导入追踪、备注与标签（可选总量采样），
  支持 merge/skip/replace，默认不覆盖用户备注；不导出凭证。
- **GitHub API 源默认禁用**：未认证额度极低；默认热门 `auto` 只用 Trending，
  OpenGithubs/Search 需在设置显式启用；语言/领域/收藏刷新/观察/更新/Release
  在关闭时跳过或提示，GH Archive 与工具库不受影响。
- **工具库**：第 4 个一级入口；默认 PPT/文档/PDF/清理等 10 类，支持自定义分类、
  多分类、热门卡片/整列表/手动 URL 收录、批量分类/移出、分类管理、离线搜索；
  分类内增长只取同窗口 valid `net_stars`；收录与收藏、追踪互相独立。
- **GH Archive 回填**：热门源显示账本覆盖与失败/未取小时；可一键补齐近 7/30 天，
  后台进度、取消、已下载跳过，完成后重建榜单。
- **本地备份恢复**：工具栏 **💾 数据管理** 创建/校验备份，登记下次启动恢复；
  每日启动备份最多保留 7 份；自包含 ZIP（manifest + 一致性 SQLite），备份自动去除设置中
  的 Token/API Key 并 VACUUM 验证；恢复前预检哈希/完整性/schema，生成恢复前
  备份并支持失败回滚，需先关闭应用。
- **GH Archive 增量同步**：小时包自适应追平（10→60 分钟），凑满 24 小时合并
  为天级数据；首次同步是否回填历史由设置 `gha_backfill_hours` 决定（默认 0，
  只取最近小时）；`archive_hours` 账本区分成功/空包/失败/未取，缺口优先重试；
  榜单展示窗口覆盖率，冷启动样本不足时明确标注。
- **名单导入 / 导出 / 翻译 / 更新提醒**：Markdown、CSV、HTML 导出保留来源、
  指标口径、窗口与覆盖率。
- **运行时加固**：单实例锁、崩溃日志、WAL 一致性启动备份、异常退出检测。

## 环境要求

- Windows 10/11（其他桌面平台理论可用，未验证）
- Python 3.11 及以上
- 依赖见 [requirements.txt](requirements.txt)；开发与测试依赖见
  [requirements-dev.txt](requirements-dev.txt)

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 开发运行（仓库根目录）
python -m app.main
```

打包（可选，生成当前 2.5.0）：

```powershell
pip install -r requirements-dev.txt
pyinstaller --clean --noconfirm "GitHub热点浏览器.spec"
Compress-Archive -Path "dist\GitHub热点浏览器\*" `
  -DestinationPath "GitHubHotBrowser-2.5.0-win64.zip" -Force
```

当前目录已有 `dist\GitHubHotBrowser-2.5.0-win64.zip` 与 `.sha256`。

## 数据目录与便携模式

默认数据写入用户可写目录，避免安装到只读目录后启动失败：

```text
%LOCALAPPDATA%\GitHubHotBrowser\data\
├── browser.db          SQLite 主库（WAL）
├── backups\            启动时的一致性备份（保留最近一份有效副本）
├── crash_logs\         未捕获异常日志
└── .clean_exit         运行标记（正常退出时删除）
```

优先级（`app/core/config.py::resolve_data_dir`）：

1. 环境变量 `GHB_DATA_DIR`（测试或自定义位置）；
2. 便携模式：程序目录存在 `portable.flag` 或设置 `GHB_PORTABLE=1` → `<程序目录>\data`；
3. 兼容旧布局：`<程序目录>\data\browser.db` 已存在且用户目录尚无数据库；
4. 默认用户目录 `%LOCALAPPDATA%\GitHubHotBrowser\data`。

便携版：在 exe 同目录创建空文件 `portable.flag` 即可。

## 迁移旧版数据

```powershell
python -m app.main --migrate "D:\path\to\旧版 program 目录"
```

迁移会先取得单实例锁并生成一致性备份；可重复执行（历史按
`channel + days + taken_at` 唯一，收藏/缓存/观察数据不重复导入），不会覆盖迁移后
手工修改的设置与备注。损坏 JSON、目录不存在计入错误并返回非零退出码：
`0` 成功、`1` 有错误、`2` 参数错误、`3` 应用正在运行、`4` 备份失败。

## 官方 Star History 诊断（只读，需联网）

```powershell
python -m app.services.observations owner/repo --days 7
```

命令拉取单仓官方 Star 历史，写入 v2 观测表并打印窗口值、完整性、实际桶范围；
不修改旧收藏/榜单数据。真实结构已用 Token 验证（203 个完整日桶）；分页、
限流与退星语义仍未验证，输出只作为只读参考，不能当作精确净增长。

## 用户数据导入导出

```powershell
python -m app.services.user_data export user-data.json --history
python -m app.services.user_data import user-data.json --mode merge
```

格式、合并策略与文件安全见 [用户数据说明](docs/user-data.md)。

## GH Archive 账本诊断

```powershell
python -m app.services.archive_status --days 7
```

输出期望小时数、成功/空包/失败/未取、最近一次同步运行与失败小时列表；不触发下载。
“已取得文件小时数”是采集覆盖，不是 GitHub 全站事件完整率。

## 工具库与本地备份

```powershell
# 工具库（Qt 主界面，或服务/CLI）
python -m app.services.library categories
python -m app.services.library add owner/repo --category "文档工具"
python -m app.services.library list

# 备份/恢复（恢复前必须退出应用）
python -m app.services.backup create D:\backup\ghb.zip
python -m app.services.backup inspect D:\backup\ghb.zip
python -m app.services.backup restore D:\backup\ghb.zip --yes
```

格式与限制见 [工具库说明](docs/library.md)、[备份恢复说明](docs/backup-restore.md)。

## 测试

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

- `tests/test_batch*.py` 为历史批次回归，保留但不再新增 batch 编号；
- 审阅问题修复的正式断言在 `tests/test_runtime_backup.py`、`test_growth_window.py`、
  `test_hot_source.py`、`test_migrate_idempotent.py`、`test_importer_parse.py`、
  `test_export_metrics.py`、`test_gha_coverage.py`、`test_http_token.py`、
  `test_data_dir.py`、`test_shutdown.py`；
- 批次 2 业务模型断言在 `tests/test_domain_windows.py`、`test_domain_ranking.py`、
  `test_domain_identity.py`、`test_metric_model.py`；
- 批次 3 官方历史与存储在 `tests/test_github_history.py`、`test_storage.py`、
  `test_observations.py`（固定 JSON 样本，不联网）；
- 批次 4 追踪与用户数据在 `tests/test_tracking.py`、`test_user_data.py`；
- 批次 5 事件账本/覆盖诊断在 `tests/test_archive_ledger.py`；
- 工具库在 `tests/test_library.py`，UI/回填在 `tests/test_library_ui.py`、
  `tests/test_gha_backfill.py`，备份恢复在 `tests/test_backup.py`；
- 界面/线程测试使用 `QT_QPA_PLATFORM=offscreen`，需要 PySide6；
- **GitHub API 源**默认关闭；需要 OpenGithubs/Search/官方历史时在设置手动启用，
  并建议配置 Token。详见 [API 源与额度](docs/api-sources.md)。
- 2026-11-15 集中验证：完整套件 **378 passed**（PySide6 6.12 由本机 pip 缓存
  离线加载到临时目录，Qt offscreen，进程退出码 0）。这只是验证环境，项目依赖
  仍以 `requirements.txt` 为准。

## 目录结构

```text
app/
├── main.py          入口与运行时加固（GUI / --migrate）
├── runtime.py       单实例锁、崩溃日志、一致性备份、退出标记（无 Qt 导入）
├── domain/          指标/窗口/排序/仓库身份（纯 Python，无 Qt/SQLite/网络）
├── core/            config（数据目录）、db（SQLite v1+v2 表）、models、classify
├── storage/         仓库身份、观测、采样、source_runs、追踪、工具库仓储
├── services/        抓取编排、增长/观察、追踪、工具库、备份、用户数据……
├── sources/         GitHub API/页面、OpenGithubs、Trending、GH Archive、官方历史
└── ui/              主窗口、卡片、对话框、任务登记（TaskRegistry）

docs/metrics-contract.md   指标、窗口、完整性与排序契约
docs/storage.md            v2/v3/v4/v5 表、身份规则、写入事务与兼容策略
docs/user-data.md          用户数据 JSON、合并策略与文件安全
docs/library.md            工具库分类、收录、删除语义与阶段限制
docs/backup-restore.md     备份包格式、预检、恢复事务与回退
docs/api-sources.md        GitHub API 默认禁用、额度策略与启用方法
docs/filters.md            语言/领域/标签交集语义、侧栏计数与标签缓存
```

依赖方向：`ui → services → domain`，`sources/storage → core/domain`（`sources`
不依赖 `services`；`domain` 不导入 Qt、SQLite、urllib）。Qt 工作线程仅作适配，
关闭窗口时统一取消并等待全部结束后才关闭数据库。

## 质量状态与已知限制

- 审阅（2026-11-15）提出的 R01–R12 已逐项处理，修复位置与验证结果见
  `../2026-11-15_审阅修复记录.md`。
- 批次 2 业务模型、批次 3 v2 观测存储与官方历史、批次 4 独立追踪与用户数据、
  批次 5 事件小时账本、工具库 A/B 与 Qt 闭环均已完成；GH Archive 增加手动回填；
  契约见 [统计](docs/metrics-contract.md)、[存储](docs/storage.md)、
  [用户数据](docs/user-data.md)、[工具库](docs/library.md)、[备份恢复](docs/backup-restore.md)。
- 官方 Star History 已用 Token 实测：203 个完整日桶（2026-04-26～11-14），
  最近 7 天合计 193；当天未走完/未来日期跳过；退星/分页语义仍未验。
  OpenGithubs 周榜 20/月榜 30、GitHub Search 3136 条均已实测；界面尚未接入
  官方日桶与追踪调度。
- 分类内增长排序已接入 valid `net_stars`；候选发现、自动分类建议（阶段 D）、
  工具库交换未做；备份恢复 UI 已支持创建/校验/登记重启恢复，每日备份保留 7 份。
- 2026-11-15 集中验证：完整 pytest **378 passed**（PySide6 6.12 临时加载，
  Qt offscreen，退出码 0）；真实 GH Archive 两小时下载、Trending 页面、
  MyMemory 翻译与 ecosyste.ms 元数据已实测通过；工具库/GH Archive/数据管理
  界面已渲染核对，重启恢复集成验证通过。
- 未验证：官方 Star History 的退星重写/分页边界、OpenGithubs 长期更新频率；
  真实桌面人工操作、exe 发布与旧库原地迁移仍待验收。
- 备份已脱敏但不加密；设置中的 Token 仍保存在业务数据库，凭证物理分离未实现。
- exe：当前 2.5.0 已用 PyInstaller 6.22.3 + PySide6 6.12.0 重建到
  `dist\GitHub热点浏览器\`，并生成 `GitHubHotBrowser-2.5.0-win64.zip`；离屏启动
  成功。真实桌面/正常退出仍需人工验收；`dist/` 被 `.gitignore` 排除，应作为
  Release 资产而不是源码提交。
- 旧版 v17b 功能对等矩阵未完成。
- GH Archive 自 2025 年中起 Star 事件漏报，绝对增量偏低，仅相对排名可用。
- 定时抓取与 GH Archive 同步需要程序常驻。

## 开源与协作

- 许可证：[MIT](LICENSE)
- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全问题：[SECURITY.md](SECURITY.md)
- 行为准则：[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- 变更记录：[CHANGELOG.md](CHANGELOG.md)

公开仓库前请确认：不提交 `data/`、日志、`dist/`、`build/`、`.venv` 与任何
Token；`.gitignore` 已覆盖这些路径，但历史提交仍需单独扫描。
