# -*- coding: utf-8 -*-
"""仓库根启动器：`python main.py` 等价于 `python -m app.main`。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
