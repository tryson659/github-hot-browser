# 贡献指南

感谢参与改进。本项目是个人向桌面工具，优先保证数据可靠性与可维护性，避免
引入微服务、复杂治理或与当前功能无关的重构。

## 开发环境

- Python 3.11+
- Windows 10/11（CI 使用 windows-latest）
- 安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

## 运行与测试

```powershell
# 运行
python -m app.main

# 无头测试（必需）
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q

# 仅语法解析（快速自检）
python -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('app').rglob('*.py')]"
```

测试按功能命名（如 `test_growth_window.py`），不要继续新增 `test_batchN.py` 编号。
涉及数据库、迁移、备份、导入解析、增长口径的改动必须附带可重复的断言测试。

## 依赖锁定

`requirements*.txt` 只声明兼容区间。每次准备发布或升级依赖时，在干净的虚拟环境中：

```powershell
pip install -r requirements-dev.txt
pip freeze > requirements.lock.txt
```

`requirements.lock.txt` 属于发布产物，随版本提交；日常 PR 不需要更新它。

## 分支与提交

- 从 `main` 切出短生命周期分支：`fix/<主题>`、`feat/<主题>`。
- 一个提交只做一件事；提交信息用祈使句，说明"为什么"。
- 不提交 `data/`、`dist/`、`build/`、日志、Token、`.env` 或任何真实用户数据。
- 提交前执行完整 pytest；涉及界面的改动请在真实桌面环境手动点验一次。

## 代码约定

- 依赖方向：`ui → services → sources → core`；`sources` 不依赖 `services`。
- 领域规则（分类、增长、筛选）保持纯函数，放 `core` 或独立领域模块。
- QThread 只做适配：业务逻辑放在可独立调用的服务函数中；新增长任务必须在
  `TaskRegistry` 登记，确保关闭顺序安全。
- 数据库写操作走 `Database` 公开方法；不要在 UI/服务里直接持有 sqlite 连接。
- 网络请求必须有超时；取消用 `threading.Event`，不要用 `QThread.terminate()`。
- 中文字符串直接可读，注释解释取舍而不是复述代码。

## Pull Request

1. 说明问题、方案与影响面（数据/兼容/界面）。
2. 列出验证命令与结果；未验证项必须写"未验证"。
3. 关联 Issue；涉及安全的问题按 [SECURITY.md](SECURITY.md) 私下报告。
4. 至少一次自审：检查是否误提交数据文件或凭证。

## 版本与发布

- 版本号在 `app/__init__.py::__version__`，变更写入 [CHANGELOG.md](CHANGELOG.md)。
- 发布前：干净环境安装依赖 → 完整 pytest → 用当前提交重建 exe → 在新安装目录、
  升级、备份恢复、只读安装目录四种场景验收。
