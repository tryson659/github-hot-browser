# -*- coding: utf-8 -*-
"""存储层：公开仓储操作。

旧 `core/db.py` 继续承担兼容读取；新业务（仓库身份、观测、采样、任务记录、
追踪、工具库）通过本包访问，避免服务层直接拼 SQL 或持有连接。
"""
from app.storage.library import LibraryStore
from app.storage.store import Store, since_days

__all__ = ["Store", "LibraryStore", "since_days"]
