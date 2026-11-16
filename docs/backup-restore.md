# 本地一致性备份与恢复（阶段 B）

实现：`app/services/backup.py`。备份包是自包含 ZIP；恢复必须在应用停止、
数据库连接关闭后执行。该功能只防逻辑损坏/误操作，不防磁盘损坏，也不替代
异地备份或加密。

## 1. 备份包格式

```text
backup.zip
├── manifest.json
└── database.sqlite
```

```json
{
  "backup_format_version": 1,
  "db_schema_version": 5,
  "app_version": "2.5.0",
  "created_at": "2026-11-15T12:00:00Z",
  "excluded_secrets": ["github_token", "translate_apikey", "translate_email"],
  "counts": {"categories": 10, "library_entries": 1, "favorites": 2,
             "tracking": 1, "star_samples": 12},
  "files": [{"name": "database.sqlite", "size": 123456,
             "sha256": "..."}]
}
```

生成流程：

1. 用 SQLite 在线备份接口生成一致性副本（不是只复制主库文件）。
2. 在副本中删除 `github_token` / `translate_apikey` / `translate_email`
   设置，执行 `PRAGMA secure_delete=ON` 与 `VACUUM` 重建干净文件。
3. 用原凭证值扫描备份库字节，仍能命中则中止，避免静默泄露。
4. 计算大小/SHA-256，写 manifest，ZIP 先写临时文件再原子替换。

## 2. 预检（只读）

`inspect_backup(path)` 拒绝：

- 非 ZIP、重复条目、缺 manifest/database.sqlite、目录/加密/符号链接条目；
- 绝对路径、父目录穿越（`../`）、解压总量超过 1 GiB、异常压缩比；
- manifest 超大、格式版本不支持、schema 高于当前程序；
- 文件大小/SHA-256 不一致；
- SQLite `integrity_check` 失败、manifest 与库内 schema 版本不一致。

通过后返回 manifest 与业务计数（分类/收录/收藏/追踪/采样），供 UI 展示；
不修改当前数据库。

## 3. 恢复事务

1. 预检并确认备份。
2. 为当前库生成恢复前备份（`backups/pre_restore_*.sqlite`，同样走 SQLite
   在线备份）。
3. 解包到目标目录临时文件并复核哈希/完整性。
4. 停止任务、关闭连接后，移除旧库 `-wal`/`-shm`，原库改名为 `.old`，
   新库原子替换；失败恢复原库。
5. 打开新库做完整性检查，失败回滚；成功后删除 `.old`，保留恢复前备份。
6. 凭证需重新配置（备份中已脱敏）；追踪任务应在 UI 恢复后按状态重启，
   避免立即请求洪峰。

## 4. Qt 数据管理

- 主窗口工具栏 **💾** 打开「数据管理 · 本地备份与恢复」。
- 创建备份：选择路径 → 生成 ZIP，显示格式/schema/计数/SHA-256。
- 校验备份：只读检查格式、版本、大小、哈希、完整性与计数；不修改当前库。
- 安排恢复：校验通过后登记 `pending_restore.json`；**关闭应用并重新启动**时，
  在打开数据库前执行整库替换，先生成恢复点，失败回滚；恢复完成弹窗提示
  重新配置凭证。
- 运行中不做热替换，避免线程/连接与数据库文件竞争。

## 5. 命令

```powershell
# 需先退出应用
python -m app.services.backup create D:\backup\ghb-20261115.zip
python -m app.services.backup inspect D:\backup\ghb-20261115.zip
python -m app.services.backup restore D:\backup\ghb-20261115.zip --yes
```

`restore` 默认替换 `config.DB_PATH` 指向的数据库；可用 `--db` 指定测试库。

## 6. 未实现 / 未验证

- 自动轮转已做每日副本（`browser-YYYYMMDD.db.bak`，最多 7 份）；外置盘、
  备份记录索引（阶段 E）未做。
- 备份包加密；当前只去除凭证，不加密个人备注。
- 当前业务数据库仍保存设置中的 Token/API Key；备份已脱敏，但“凭证与业务库
  物理分离”未实现，不能把原始数据库直接分享。
- 恢复只支持整库替换 + 重启完成；“合并项目”走用户数据 JSON 导入。
- 恢复向导已做创建/校验/登记，但真实磁盘空间不足、替换失败、运行中强退
  等场景仍需人工验收；pytest 使用临时库。
