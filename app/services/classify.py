# -*- coding: utf-8 -*-
"""兼容导出：领域分类与兴趣匹配已迁移到 `app.core.classify`。

保留本模块只为旧调用方（外部脚本/旧测试）不中断；新代码请直接导入
`app.core.classify`，避免 sources → services 的反向依赖。
"""
from app.core.classify import classify_domain, match_interest

__all__ = ["classify_domain", "match_interest"]
