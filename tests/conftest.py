# -*- coding: utf-8 -*-
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 整个测试会话保持一个 QApplication 引用。各测试模块自己创建 QApplication
# 时拿到的也是同一实例；避免 Python 提前回收 app 包装导致进程退出时崩溃。
_QAPP = None


@pytest.fixture(scope="session", autouse=True)
def _keep_qapp_alive():
    global _QAPP
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        yield
        return
    _QAPP = QApplication.instance() or QApplication([])
    yield _QAPP
    # 会话结束：关闭残留顶层窗口并让 Qt 处理删除事件，避免解释器退出时
    # 由 C++ 侧按任意顺序析构仍存活的窗口而崩溃。
    try:
        for widget in list(QApplication.topLevelWidgets()):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QApplication.processEvents()
        _QAPP.quit()
        QApplication.processEvents()
    except Exception:
        pass
