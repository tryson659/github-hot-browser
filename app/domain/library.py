# -*- coding: utf-8 -*-
"""工具库领域规则：分类模板、名称规范化与稳定标识（阶段 A）。

分类是"用途"目录，与 GitHub topics、用户标签分开。默认模板使用稳定
seed_key + 版本；显示名可改，seed_key 不当主键，也不覆盖用户改名/排序。
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

CATEGORY_TEMPLATE_VERSION = 1
MAX_CATEGORY_NAME = 40
MAX_CATEGORY_DESCRIPTION = 200


@dataclass(frozen=True)
class CategorySeed:
    seed_key: str
    name: str
    description: str
    sort_order: int


# 默认分类只作为首次安装的初始数据；已删除的 seed 不会在升级时恢复。
DEFAULT_CATEGORIES = (
    CategorySeed("ppt", "PPT / 演示工具",
                 "演示文稿制作、生成、模板与格式转换", 10),
    CategorySeed("docs", "文档工具",
                 "文档编辑、格式转换、批处理、协作", 20),
    CategorySeed("pdf_ocr", "PDF / OCR",
                 "PDF 合并拆分、阅读、文字识别", 30),
    CategorySeed("cleanup", "清理工具",
                 "缓存清理、重复文件、卸载辅助、磁盘空间分析", 40),
    CategorySeed("files", "文件管理",
                 "搜索、整理、批量重命名、同步", 50),
    CategorySeed("backup", "备份与恢复工具",
                 "文件备份、同步恢复相关工具仓库（与软件自身数据恢复区分）", 60),
    CategorySeed("media", "图片 / 视频 / 音频",
                 "媒体编辑、转换、压缩、录制", 70),
    CategorySeed("data", "数据 / 表格",
                 "表格处理、数据清洗、可视化", 80),
    CategorySeed("automation", "自动化 / 效率",
                 "桌面自动化、剪贴板、启动器、流程工具", 90),
    CategorySeed("devtools", "开发工具",
                 "调试、API 工具、编辑器辅助", 100),
)


def seed_category_id(seed_key: str) -> str:
    """稳定 UUID：重复播种/升级不会产生新 ID。"""
    return str(uuid5(NAMESPACE_URL, f"github-hot-browser/category/{seed_key}"))


def clean_category_name(name: str) -> str:
    """显示名：去首尾空白、压缩连续空白；空名拒绝。"""
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ValueError("分类名称不能为空")
    if len(cleaned) > MAX_CATEGORY_NAME:
        raise ValueError(f"分类名称最多 {MAX_CATEGORY_NAME} 个字符")
    return cleaned


def category_name_key(name: str) -> str:
    """同层去重键：规范化大小写与空白（SQLite 侧再存一列）。"""
    return clean_category_name(name).casefold()


def clean_description(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) > MAX_CATEGORY_DESCRIPTION:
        raise ValueError(f"分类说明最多 {MAX_CATEGORY_DESCRIPTION} 个字符")
    return cleaned


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    normalized_name: str
    sort_order: int
    description: str = ""
    seed_key: str = ""
    hidden: bool = False
    deleted: bool = False
    template_version: int = 0
