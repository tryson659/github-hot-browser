## 变更说明

<!-- 这个 PR 解决什么问题？为什么这样改？ -->

## 影响面

- [ ] 数据库/SQLite 结构或迁移
- [ ] 数据备份、单实例锁、退出流程
- [ ] 抓取源或网络请求
- [ ] 界面交互
- [ ] 依赖 / 打包 / CI
- [ ] 仅文档

## 验证

<!-- 列出实际执行的命令与结果；未验证项请明确写“未验证”。 -->

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

## 检查清单

- [ ] 未提交 `data/`、日志、`dist/`、`build/`、Token 或真实用户数据
- [ ] 新增/变更的行为有对应测试（或说明为何无法测试）
- [ ] 依赖方向保持 `ui → services → sources → core`
- [ ] 后台线程已在 TaskRegistry 登记，关闭顺序安全
- [ ] 已更新 CHANGELOG / 文档（如适用）

## 关联 Issue

Closes #
