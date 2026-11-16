# -*- coding: utf-8 -*-
"""主题系统：深浅双配色 + 全局 QSS 生成 + 颜色混合工具。"""

THEMES = {
    "dark": {
        "bg": "#0d1117", "card": "#161b22", "text": "#e6edf3", "text2": "#8b949e",
        "accent": "#58a6ff", "toolbar": "#010409", "border": "#30363d",
        "input": "#0d1117", "btn": "#21262d", "btn_hover": "#30363d",
        "star": "#e3b341", "ok": "#3fb950", "danger": "#f85149",
        "card_hover": "#1c2129", "hover_soft": "#1f2733", "sel_soft": "#16324a",
        "skeleton": "#1c2129", "skeleton_hi": "#262e39", "accent_soft": "#1f6feb",
        "shadow": "#000000",
    },
    "light": {
        "bg": "#f5f6f8", "card": "#ffffff", "text": "#1f2328", "text2": "#59636e",
        "accent": "#1f6feb", "toolbar": "#24292f", "border": "#e1e4e8",
        "input": "#ffffff", "btn": "#f0f1f3", "btn_hover": "#e1e4e8",
        "star": "#9a6700", "ok": "#2da44e", "danger": "#cf222e",
        "card_hover": "#f0f3f7", "hover_soft": "#eaeef3", "sel_soft": "#d6e4fa",
        "skeleton": "#eceff3", "skeleton_hi": "#dfe5ec", "accent_soft": "#1f6feb",
        "shadow": "#8b949e",
    },
}


def mix(c1: str, c2: str, t: float) -> str:
    """两个 #rrggbb 颜色按 t∈[0,1] 混合。"""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def app_qss(C: dict) -> str:
    """全局样式表（主窗口/侧栏/工具栏/滚动条/输入控件）。"""
    return f"""
QMainWindow, QDialog {{ background: {C['bg']}; }}
QWidget {{ color: {C['text']}; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }}
QToolTip {{ background: {C['card']}; color: {C['text']}; border: 1px solid {C['border']}; }}

#toolbar {{ background: {C['toolbar']}; border-bottom: 1px solid {C['border']}; }}
#toolbar QPushButton {{
    background: transparent; border: none; border-radius: 6px;
    padding: 6px 12px; color: {C['text']};
}}
#toolbar QPushButton:hover {{ background: {C['btn_hover']}; }}
#toolbar QPushButton:checked {{ background: {C['accent_soft']}; color: #ffffff; }}
#toolbar QPushButton:disabled {{ color: {C['text2']}; }}

#sidebar {{ background: {C['card']}; border-right: 1px solid {C['border']}; }}
#sidebarTitle {{ color: {C['text2']}; font-size: 11px; font-weight: 600;
    letter-spacing: 1px; padding: 8px 12px 2px 12px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 6px; padding: 5px 10px; margin: 1px 6px; }}
QListWidget::item:hover {{ background: {C['hover_soft']}; }}
QListWidget::item:selected {{ background: {C['sel_soft']}; color: {C['text']}; }}

QPushButton.flat {{
    background: {C['btn']}; border: none; border-radius: 6px;
    padding: 6px 12px; color: {C['text']};
}}
QPushButton.flat:hover {{ background: {C['btn_hover']}; }}
QPushButton.flat:disabled {{ color: {C['text2']}; }}
QPushButton.accent {{
    background: {C['accent_soft']}; border: none; border-radius: 6px;
    padding: 6px 14px; color: #ffffff; font-weight: 600;
}}
QPushButton.accent:hover {{ background: {C['accent']}; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {C['input']}; border: 1px solid {C['border']};
    border-radius: 6px; padding: 5px 8px; color: {C['text']};
    selection-background-color: {C['accent_soft']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {C['card']}; border: 1px solid {C['border']};
    selection-background-color: {C['sel_soft']}; color: {C['text']}; outline: none;
}}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {C['border']}; background: {C['input']}; }}
QCheckBox::indicator:checked {{ background: {C['accent_soft']};
    border-color: {C['accent_soft']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {C['btn_hover']}; border-radius: 5px;
    min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {C['text2']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {C['btn_hover']}; border-radius: 5px;
    min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

#statusbar {{ background: {C['card']}; border-top: 1px solid {C['border']};
    color: {C['text2']}; font-size: 12px; padding: 3px 10px; }}
#sectionLabel {{ color: {C['text2']}; font-size: 12px; }}
#linkLabel {{ color: {C['accent']}; }}
#emptyHint {{ color: {C['text2']}; font-size: 14px; padding: 64px 40px;
    line-height: 1.8; }}
"""
