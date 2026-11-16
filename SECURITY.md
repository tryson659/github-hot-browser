# 安全策略

## 支持版本

| 版本 | 支持状态 |
|---|---|
| 2.0.x（开发中） | 接受安全报告 |
| 旧版 v17b（tkinter） | 仅维护关键问题 |

## 报告安全问题

请**不要**用公开 Issue 报告以下问题：

- 凭证泄露（GitHub Token、翻译 API Key、翻译邮箱）
- 数据库/备份可被其他用户读取或篡改
- 导入文本导致的任意文件/网络访问
- 依赖供应链问题

请使用 GitHub 仓库的 **Private vulnerability reporting**（Security → Report a
vulnerability）或私下联系维护者。请在报告中说明：版本、复现步骤、影响与建议修复；
不要在报告或截图中包含真实 Token 或用户数据。

## 本项目如何处理凭证

- GitHub Token 可来自环境变量 `GITHUB_TOKEN`（优先）或设置页保存到本地 SQLite。
- Token 只用于请求 GitHub API；日志、崩溃日志与导出文件不写入 Token。
- 设置页清空 Token 只删除应用保存值，不修改外部环境变量。
- 数据目录默认位于用户目录（`%LOCALAPPDATA%\GitHubHotBrowser`），不随程序安装目录分发。

## 用户自检

- 不要把 `data/` 目录、日志或包含 Token 的截图上传到公开 Issue。
- 若怀疑 Token 泄露，立即在 GitHub Settings → Developer settings 中撤销并重新生成。
- 便携版放在共享目录时，请自行限制目录访问权限。
