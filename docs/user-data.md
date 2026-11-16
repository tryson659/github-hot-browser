# 用户数据导入导出（v2.2 批次 4）

实现：`app/services/user_data.py`；只处理个人追踪数据，不导出设置、Token、
翻译凭证或数据库全文。追踪表说明见 [存储说明](storage.md)。

## 1. 文件格式（schema_version = 1）

```json
{
  "schema_version": 1,
  "application": "github-hot-browser",
  "exported_at": "2026-11-15T12:00:00Z",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "favorite": true,
      "enabled": true,
      "paused": false,
      "removed": false,
      "note": "用户备注",
      "interval_hours": 6,
      "next_due_at": "2026-11-15T18:00:00Z",
      "last_sampled_at": "2026-11-15T12:00:00Z",
      "tags": {"user": ["引擎"], "github_topic": ["cpp"]}
    }
  ],
  "history": {
    "samples": {"owner/repo": [["2026-11-15T12:00:00Z", 150]]}
  }
}
```

- `history` 仅在 `export --history` 时出现；只含总量采样，不含观测/数据库设置。
- 旧收藏（尚未迁移到 tracking）导出为 `favorite=true, enabled=false`：保留收藏与
  备注，但默认不开启周期采样。
- `removed=true` 是删除墓碑，导入后仍保持停用，不会被自动发现复活。

## 2. 导入策略

| 模式 | 行为 |
|---|---|
| `merge`（默认） | 缺项新建；已有项保留非空备注与暂停/删除状态，收藏取并集、标签取并集；冲突写入 `conflicts` |
| `skip` | 已存在的仓库整项跳过，不修改任何状态 |
| `replace` | 用文件中的备注、收藏、启用/暂停/删除、间隔与标签替换 |

- 重复导入幂等：第二次 merge 无变化时计入 `unchanged`，不重复创建仓库/追踪。
- 逐项校验；单项非法（仓库名、时间、布尔、标签长度）计入 `errors`，不中断整批。
- `schema_version` 不匹配直接拒绝，避免静默误读。
- 备注不覆盖：merge 下已有非空备注保持，并把差异写入 `conflicts`。

## 3. 文件安全

- `write_json` 使用同目录临时文件 + `os.replace` 原子替换，失败清理临时文件。
- CSV 报告导出对以 `= + - @`、制表符或回车开头的文本加 `'` 前缀，防 Excel
  公式注入（见 `services/exporter.py::_csv_safe`）。
- 导出内容不含 `settings`、`github_token`、翻译 API Key/邮箱。

## 4. 命令

```powershell
# 导出（不含历史）
python -m app.services.user_data export user-data.json

# 导出并包含最近一年总量采样
python -m app.services.user_data export user-data.json --history

# 导入（merge / skip / replace）
python -m app.services.user_data import user-data.json --mode merge
```

## 5. 限制

- 尚未接入 Qt 文件选择/预览；当前用命令或服务函数。
- 历史只恢复总量采样，不恢复 `metric_observations` 与榜单缓存。
- 旧收藏与 tracking 同名合并时以 tracking 为准，旧备注可能需人工核对。
