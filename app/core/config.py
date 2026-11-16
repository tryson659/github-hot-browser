# -*- coding: utf-8 -*-
"""全局常量、默认设置、领域分类规则与兴趣关键词。

只放"不随运行变化"的静态配置；可变状态一律走 db/settings。

数据目录（R12）：
- 默认写入用户可写目录 `%LOCALAPPDATA%/GitHubHotBrowser/data`，避免安装目录只读时启动失败；
- 便携模式：程序目录存在 `portable.flag` 或设置环境变量 `GHB_PORTABLE=1`；
- 显式覆盖：环境变量 `GHB_DATA_DIR`（测试与高级用户用）；
- 兼容旧布局：程序目录 `data/browser.db` 已存在且用户目录尚无数据库时，继续使用程序目录。

导入本模块不创建任何目录（模块导入无副作用），目录由 `ensure_data_dirs()` 显式创建。
"""
import os
import sys

# ---------------- 路径 ----------------
APP_NAME = "GitHubHotBrowser"
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable))
           if getattr(sys, "frozen", False)
           else os.path.dirname(os.path.dirname(os.path.dirname(
               os.path.abspath(__file__)))))


def _user_data_root(env=None) -> str:
    env = os.environ if env is None else env
    base = (env.get("LOCALAPPDATA") or env.get("APPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, APP_NAME)


def resolve_data_dir(app_dir: str | None = None, env=None) -> str:
    """按优先级解析数据目录：显式覆盖 > 便携模式 > 既有旧布局 > 用户目录。"""
    app_dir = os.path.abspath(app_dir or APP_DIR)
    env = os.environ if env is None else env
    override = (env.get("GHB_DATA_DIR") or "").strip()
    if override:
        return os.path.abspath(override)
    portable = (env.get("GHB_PORTABLE", "").strip().lower()
                in ("1", "true", "yes", "on")
                or os.path.exists(os.path.join(app_dir, "portable.flag")))
    if portable:
        return os.path.join(app_dir, "data")
    legacy = os.path.join(app_dir, "data")
    user = os.path.join(_user_data_root(env), "data")
    if (os.path.exists(os.path.join(legacy, "browser.db"))
            and not os.path.exists(os.path.join(user, "browser.db"))):
        return legacy
    return user


DATA_DIR = resolve_data_dir()
DB_PATH = os.path.join(DATA_DIR, "browser.db")
CRASH_DIR = os.path.join(DATA_DIR, "crash_logs")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")


def ensure_data_dirs(path: str | None = None) -> str:
    """创建数据目录及其子目录；返回数据目录。GUI/迁移入口启动时调用。"""
    path = path or DATA_DIR
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "crash_logs"), exist_ok=True)
    os.makedirs(os.path.join(path, "backups"), exist_ok=True)
    return path


# ---------------- 抓取口径 ----------------
POOL_N = 50            # 每通道默认抓取条数
POOL_MAX = 1000        # GitHub 单次查询物理上限
PAGE_SIZE = 30         # 每页渲染卡片数
RANGES = (("近7天", 7), ("近30天", 30))

HOT_KEY = "🔥本周热门"
TREND_KEY = "📈Trending抓取"
NEW_KEY = "🆕新晋项目"
RANK_WEEK_KEY = "📊OpenGithubs周榜"
RANK_MONTH_KEY = "📊OpenGithubs月榜"
GH_ARC_KEY = "🗄GHArchive自建"
DOMAIN_PREFIX = "domain:"

# 本周热门数据源。指标口径不同，界面与导出必须显示实际来源，不能静默混排。
HOT_SOURCES = (
    ("自动（默认 Trending）", "auto"),
    ("OpenGithubs 报告（需启用 API）", "opengithubs"),
    ("GH Archive 事件（需补齐）", "gharchive"),
    ("GitHub Trending", "trending"),
    ("本地搜索候选（需启用 API）", "search"),
)

# auto 只允许同指标（provider_reported）的来源互相替代；
# 事件计数/net_stars 必须由用户显式选择，避免「报告失败 → 搜索榜」的静默换口径。
AUTO_SOURCE_CHAIN = ("opengithubs", "trending")


def hot_channel(source: str, days: int) -> str:
    """数据源 → 独立快照通道键。每个源 × 7/30 天各占独立缓存槽位。

    OpenGithubs 周榜/月榜本就是两个榜单，天然分键；其余源跨时间档同键
    （7/30 天由 cache 表的 days 维度区分）。
    """
    if source == "opengithubs":
        return RANK_WEEK_KEY if days == 7 else RANK_MONTH_KEY
    if source == "gharchive":
        return GH_ARC_KEY
    if source == "trending":
        return TREND_KEY
    return HOT_KEY          # search（本地搜索近似，沿用旧键兼容迁移数据）


# 无增长真实值、需用本地快照基线计算 growth_7d 的通道
def needs_baseline(channel: str) -> bool:
    return not channel.startswith(("📊", "🗄", "📈"))

SORTS = ("近7天增长", "日均增长", "兴趣匹配", "Fork 数", "最近更新",
         "总 Star", "最近收录")

# ---------------- 语言 / 领域 ----------------
COMMON_LANGS = ["C++", "C", "Rust", "C#", "Go", "Python", "TypeScript", "Java",
                "JavaScript", "Zig", "Kotlin", "Swift", "Lua"]

DOMAIN_RULES = [
    ("AI / Agent", [" ai ", "llm", "agent", "gpt", "claude", "gemini", "deepseek",
                    "prompt", " rag ", "mcp", "neural", "inference", "openai",
                    "copilot", "chatbot", "voice", "model"]),
    ("开发工具", ["cli", "compiler", "debug", "lsp", "formatter", "framework", "sdk",
                  "ide", "editor", "devtools", "runtime", "package", "build",
                  "linter", "codegen", "脚手架"]),
    ("游戏 / 模拟器", ["game", "emulator", "switch", "zelda", "nintendo", "steam",
                      "engine", "pixel", "voxel", "player", "recomp", "remake"]),
    ("媒体 / 创意", ["music", "video", "gif", "image", "photo", "3d", "render",
                    "design", "animation", "film", "avatar", "wallpaper", "meme"]),
    ("桌面 / 系统", ["desktop", "windows", "macos", "linux", "taskbar", "gpu",
                    "os ", "filesystem", "driver", "monitor", "widget"]),
    ("Web / 前端", ["web", "react", "vue", "browser", "frontend", "css",
                    "website", "tailwind", "ui", "组件"]),
    ("安全 / 网络", ["security", "hack", "reverse", "proxy", "vpn", "encrypt",
                    "password", "bypass", "sniffer", "firewall"]),
    ("数据 / 效率", ["data", "dashboard", "automation", "workflow", "note", "rss",
                    "bot", "sync", "crawl", "scraper", "excel", "csv"]),
]
DOMAIN_DEFAULT = "其他"

# GitHub 搜索多词为 AND 语义，故每领域选最具代表性的一个定向词
DOMAIN_QUERY_KWS = {
    "AI / Agent": "agent",
    "开发工具": "cli",
    "游戏 / 模拟器": "game",
    "媒体 / 创意": "3d",
    "桌面 / 系统": "desktop",
    "Web / 前端": "react",
    "安全 / 网络": "security",
    "数据 / 效率": "data",
}

INTEREST_KEYWORDS = {
    "游戏引擎": ["engine", "vulkan", "opengl", "directx", "metal", "render", "shader",
               "game", "ecs", "physics", "unity", "unreal", "gamedev", "graphics",
               "sprite", "粒子", "animation system", "scene", "帧率"],
    "C++服务端": ["server", "rpc", "grpc", "http server", "socket", "network",
                "microservice", "redis", "mysql", "rpc framework", "高并发",
                "coroutine", "asio", "muduo", "skynet", "gateway", "proxy server",
                "分布式", "cluster"],
    "底层技术": ["memory", "allocator", "malloc", "compile", "linker", "stl",
              "template", "constexpr", "simd", "kernel", "driver", "runtime",
              "ffi", "assembly", "cache", "atomic", "lock-free", "context switch"],
}

# ---------------- 默认设置 ----------------
DEFAULT_SETTINGS = {
    "languages": ["C++", "C"],
    "custom_langs": [],
    "domains": [d for d, _ in DOMAIN_RULES],
    "pool_size": POOL_N,
    "theme": "dark",
    "hot_source": "auto",
    "interests": INTEREST_KEYWORDS,
    "gha_hourly": True,        # GH Archive 小时包后台自适应增量同步（约 5MB/小时）
    "gha_backfill_hours": 0,   # 首次同步回填小时数（0=只从最近已结束小时开始）
    "github_api_enabled": False,  # GitHub API 源默认禁用，需用户显式启用
    "fetch_interval_hours": 0,     # 定时抓取间隔（0=关闭，2~24 小时）
    "update_interval_hours": 12,   # 静默更新检查最小间隔
    "github_token": "",            # 可选，API 限额 60 → 5000 次/小时（环境变量优先）
    "translate_provider": "mymemory",   # mymemory | libre
    "translate_endpoint": "",      # LibreTranslate 兼容接口地址
    "translate_apikey": "",        # LibreTranslate API Key（可选）
    "translate_email": "",         # MyMemory 注册邮箱（提额，可选）
}

# 里程碑档位（收藏 Star 跨档提醒，批2 接入弹窗；存储层先保留字段）
MILESTONES = (1000, 5000, 10000, 50000, 100000, 500000)

USER_AGENT = "github-hot-browser/2.0"
