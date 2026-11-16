# GitHub API 源与额度策略

未认证 GitHub API 的 core 只有 60 次/小时，Search 只有 10 次/分钟（共享出口 IP
时经常直接被其他进程耗尽）。因此 **GitHub API 源默认禁用**，只有用户在设置中
显式勾选后才启用。Trending、GH Archive、MyMemory、ecosyste.ms 不消耗 GitHub
core 额度，不受该开关影响。

## 默认行为

| 功能 | 默认（API 关） | 启用 API 后 |
|---|---|---|
| 本周热门 `auto` | 只用 **GitHub Trending** | OpenGithubs → Trending |
| OpenGithubs / 本地搜索数据源 | 下拉项禁用；旧设置不会自动抓取 | 可选 |
| 语言/领域通道抓取 | 跳过，并提示原因 | 正常 Search |
| 收藏刷新 / 添加观察 / 检查更新 / Release | 提示需要 API，不请求 | 正常 |
| GH Archive 详情补全（前 20） | 跳过 | 尽力补全 |
| GH Archive 建榜 / 回填 | 可用（不耗 GitHub API） | 可用 |
| 工具库、备份、导出 | 可用 | 可用 |

## 启用方法

主窗口 **⚙ 设置** → 勾选「启用 GitHub API 源（Search / OpenGithubs /
官方历史；未认证额度极低，默认关闭）」→ 保存。之后数据源下拉中的
OpenGithubs/Search 变为可选。

即使启用，也建议配置 Token（60 → 5000 次/小时）；没有 Token 时优先使用
Trending / GH Archive，不要依赖 GitHub Search 做全量频道抓取。

## 设计约束

- 默认 `hot_source=auto` 的自动链在 API 关闭时只有 Trending，避免启动即打
  OpenGithubs 的 core 配额。
- 旧数据库里若 `hot_source` 是 OpenGithubs/Search，下拉仍显示旧值但抓取会被
  预检拦截；用户手动切到 Trending 即可恢复。
- GH Archive 的窗口榜单是事件计数，不等于 Star 净增长；详情补全跳过不影响榜单。
- 关闭 API 不会删除已缓存数据，历史榜单仍可离线查看/导出。

## 未验证

- 真实 Token 下的 5000 次/小时额度与长期调用预算未验收（本次 Token 调用已确认
  core 5000、Search 30）。
- 多来源发现、请求预算、OpenDigger 去重仍未实现。
- OpenGithubs contents API 已用用户 Token 实测：周榜 20 条、月榜 30 条，
  周期分别 `20261109` / `20261101`；未验证长期更新频率与目录滚动边界。
- 本次 Token 仅作为进程环境变量用于验证，未写入项目数据库/文档；如需应用内使用，
  请在设置中自行填入。
