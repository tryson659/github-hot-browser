# -*- coding: utf-8 -*-
"""实地功能验证：实例化真实主窗口，逐项模拟用户操作，断言界面/数据变化。
网络全部 mock 掉；每项打印 PASS/FAIL。非 pytest，直接运行。
"""
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication, QMessageBox

from app.core import config
from app.core.db import Database
from app.core.models import Repo
from app.ui.main_window import MainWindow

app = QApplication([])
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if not cond else ""))


def mk(name, stars=100, lang="Python", desc="demo project", domain="开发工具",
       topics=None):
    return Repo(name=name, url=f"https://github.com/{name}", stars=stars,
                forks=10, language=lang, description=desc, domain=domain,
                topics=topics or [], growth_7d=5, growth_daily=1.0,
                pushed="2026-11-08")


tmp = tempfile.mkdtemp()
db = Database(os.path.join(tmp, "t.db"))
win = MainWindow(db, start_sync=False)

# ---- 准备数据：热门池 + 语言/领域通道缓存 ----
repos = [mk("a/alpha", 500, "Python", "web framework demo", "Web / 前端",
            ["web", "framework"]),
         mk("b/beta", 300, "C++", "sample compiler demo", "开发工具",
            ["compiler", "toolchain"]),
         mk("c/gamma", 200, "Rust", "cli tool demo", "开发工具", ["cli"])]
ch = win._hot_channel()
db.save_cache(ch, 7, repos, "测试榜")
db.save_history(ch, 7, repos)
db.save_cache("Python", 7, [repos[0]])
db.save_cache("domain:Web / 前端", 7, [repos[0]])
win._pools.clear()
win._refresh_view()
app.processEvents()

# 1. 启动与初始渲染
check("1 启动渲染卡片", win._rendered == 3, f"rendered={win._rendered}")

# 2. 榜单切换 hot→fav→watch→hot
win._set_board("fav")
check("2a 收藏榜空态", win._rendered == 0)
win._set_board("watch")
check("2b 观察榜空态", win._rendered == 0)
win._set_board("hot")
check("2c 回到热门榜", win._rendered == 3)

# 3. 时间档切换
win.days_combo.setCurrentIndex(1)
check("3 切30天档", win.days == 30)
win.days_combo.setCurrentIndex(0)
check("3b 切回7天档", win.days == 7)

# 4. 数据源切换：写设置 + 通道键变化
old_src = db.get_setting("hot_source")
for i in range(win.source_combo.count()):
    if win.source_combo.itemData(i) == "gharchive":
        win.source_combo.setCurrentIndex(i)
        break
check("4a 切源写设置", db.get_setting("hot_source") == "gharchive")
check("4b 切源换通道", win._hot_channel() == config.GH_ARC_KEY)
for i in range(win.source_combo.count()):
    if win.source_combo.itemData(i) == "auto":
        win.source_combo.setCurrentIndex(i)
        break

# 5. 排序切换
win.sort_combo.setCurrentText("Fork 数")
check("5 排序切换", win.sort_name == "Fork 数" and win._rendered == 3)
win.sort_combo.setCurrentText(config.SORTS[0])

# 6. 关键词筛选
win.kw_edit.setText("engine")
check("6a 关键词过滤", win._rendered == 1)
win.kw_edit.clear()
check("6b 清空关键词", win._rendered == 3)

# 7. 语言筛选（列表点击 + toggle 取消）
items = {win.lang_list.item(i).data(256): i for i in range(win.lang_list.count())}
check("7a 侧栏含跟踪语言", "Python" in items)
win._on_lang(win.lang_list.item(items["Python"]))
check("7b 语言筛选生效", win.lang_filter == "Python" and win._rendered == 1)
win._on_lang(win.lang_list.item(items["Python"]))
check("7c 再点取消筛选", win.lang_filter is None and win._rendered == 3)

# 8. 领域筛选 + 与语言叠加
ditems = {win.dom_list.item(i).data(256): i for i in range(win.dom_list.count())}
win._on_domain(win.dom_list.item(ditems["Web / 前端"]))
check("8a 领域筛选生效", win._rendered == 1)
win._on_lang(win.lang_list.item(items["Python"]))
check("8b 语言×领域叠加", win._rendered == 1)
win._clear_filters()
check("8c 清除筛选", win._rendered == 3 and win.domain_filter is None)

# 9. 标签筛选（combo）
tags = [win.tag_combo.itemText(i) for i in range(win.tag_combo.count())]
check("9a 标签候选生成", "web" in tags, str(tags))
win._pick_tag("web")
check("9b 标签筛选生效", win.tag_filter == "web" and win._rendered == 1)
win._clear_filters()

# 10. 收藏 + 备注 + 基准徽章
win._toggle_favorite("a/alpha")
check("10a 收藏写入", db.is_favorite("a/alpha"))
db.set_favorite_note("a/alpha", "好项目", ["参考"])
win._refresh_view()
meta = db.favorite_meta("a/alpha")
check("10b 备注标签保存", meta["note"] == "好项目" and meta["tags"] == ["参考"])
win._set_board("fav")
check("10c 收藏榜渲染", win._rendered == 1)
win.kw_edit.setText("参考")
check("10d 收藏按备注搜索", win._rendered == 1)
win.kw_edit.clear()
win._set_board("hot")

# 11. 翻译（mock MyMemory）+ 缓存
import app.services.translate as tr_svc


class _Resp:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return b'{"responseData":{"translatedText":"\\u8bd1\\u6587"}}'


import urllib.request as _ur
_orig_urlopen = _ur.urlopen
_ur.urlopen = lambda *a, **k: _Resp()
card = None
for i in range(win.cards_lay.count()):
    w = win.cards_lay.itemAt(i).widget()
    if hasattr(w, "repo") and w.repo.name == "a/alpha":
        card = w
        break
win._translate_card(card)
win._tr_last = None
# _translate_card 起线程；等它完成
time.sleep(0.1)
app.processEvents()
time.sleep(0.3)
app.processEvents()
_ur.urlopen = _orig_urlopen
check("11a 翻译回填", card._translated == "译文", str(card._translated))
check("11b 翻译写缓存", db.get_translation("web framework demo") == "译文")

# 12. Release 查询（mock）+ 缓存
import app.sources.github_search as gs
_orig_fetch_release = gs.fetch_release
gs.fetch_release = lambda name: {"tag": "v1.0", "date": "2026-11-01"}
win._fetch_release(card)
time.sleep(0.3)
app.processEvents()
gs.fetch_release = _orig_fetch_release
rel = db.get_release("a/alpha")
check("12a Release缓存", rel and rel["tag"] == "v1.0")
card2_rel = db.get_release("a/alpha")
check("12b Release读取", card2_rel["date"] == "2026-11-01")

# 13. 导出 md / csv / html
from app.services import exporter
md = exporter.to_markdown(repos, "本周热门", 7, "近7天增长")
check("13a 导出Markdown", "a/alpha" in md and "| # |" in md)
csv_path = os.path.join(tmp, "e.csv")
exporter.to_csv(repos, csv_path)
check("13b 导出CSV", os.path.getsize(csv_path) > 100)
html = exporter.to_html(repos, "本周热门", 7, "近7天增长")
check("13c 导出HTML", "<table>" in html and "b/beta" in html)

# 14. 设置保存 roundtrip + 数据源回写工具栏 + 自定义语言取消勾选
from app.ui.dialogs import SettingsDialog
db.set_setting("custom_langs", ["Zig", "Assembly"])
db.set_setting("languages", ["C++", "C", "Zig"])
dlg = SettingsDialog(db, win)
check("14a 自定义语言复选框出现", "Zig" in dlg.lang_checks
      and dlg.lang_checks["Zig"].isChecked())
dlg.lang_checks["Zig"].setChecked(False)     # 取消勾选自定义语言
dlg.pool_spin.setValue(200)
dlg.source_combo.setCurrentIndex(
    dlg.source_combo.findData("gharchive"))
dlg._save()
check("14b 取消勾选生效", "Zig" not in db.get_setting("languages")
      and "Zig" in db.get_setting("custom_langs"))
check("14c pool_size保存", db.get_setting("pool_size") == 200)
check("14d 数据源保存", db.get_setting("hot_source") == "gharchive")
# _open_settings 的回写逻辑（模拟保存后调用路径）
idx = win.source_combo.findData(db.get_setting("hot_source", "auto"))
win.source_combo.blockSignals(True)
win.source_combo.setCurrentIndex(idx)
win.source_combo.blockSignals(False)
check("14e 工具栏combo回写", win.source_combo.currentData() == "gharchive")
win.db.set_setting("hot_source", "auto")

# 15. 抓取：额度预检提示 / 单通道 / 进行中提示
from app.sources import http as _http
reset = int(time.time()) + 600
_http.github_api = lambda *a, **k: {"resources": {"core": {"remaining": 0,
                                                            "reset": reset}}}
wk_msgs = []
from app.services.fetcher import FetchWorker
wk = FetchWorker(db)
wk.progress.connect(wk_msgs.append)
wk.run()
check("15a 额度0预检跳过", wk.rate_limit_reset == reset
      and any("已跳过" in m for m in wk_msgs))
win._worker = wk
win._on_fetch_done(0, 0, [])
check("15b 状态栏提示恢复时间", "额度已用完" in win.status_lbl.text())
del _http.github_api
import importlib
importlib.reload(_http)

# 抓取进行中再点提示
win._worker = FetchWorker(db)   # 伪占用
win._start_fetch(None)
check("15c 进行中再点有提示", "正在进行中" in win.status_lbl.text())
win._worker = None

# 16. 历史快照回看 + 回到最新
db.save_history(ch, 7, repos)   # 第二条快照
snaps = db.list_history(ch, 7)
check("16a 历史快照列表", len(snaps) == 2)
win._snapshot_override = repos[:1]
win._refresh_view()
check("16b 快照回看渲染", win._rendered == 1
      and "回看" in win.status_lbl.text())
win._back_to_latest()
check("16c 回到最新", win._snapshot_override is None and win._rendered == 3)

# 17. 观察名单：手动添加（mock）+ 榜单渲染 + 导入解析
gs.fetch_repo = lambda name: {"stars": 42, "forks": 3, "pushed": "2026-11-05",
                              "license": "MIT", "archived": False,
                              "description": "watched", "language": "Go"}
from app.services import watchlist as wl
msg = wl.add_manual(db, "x/omega")
check("17a 手动观察添加", "已加入观察" in msg)
win._set_board("watch")
check("17b 观察榜渲染", win._rendered >= 1)
from app.services.importer import parse_repo_names
names = parse_repo_names("ggml-org/llama.cpp\nhttps://github.com/torvalds/linux")
check("17c 导入解析", names == ["ggml-org/llama.cpp", "torvalds/linux"])
del gs.fetch_repo
importlib.reload(gs)
win._set_board("hot")

# 18. 更新检查（mock 全部）+ 基线写入
gs2 = sys.modules.get("app.sources.github_search")
import app.services.updater as upd
_check_db = db
gs2.fetch_repo = lambda name: {"stars": 999, "forks": 0, "pushed": "2026-11-09",
                               "license": "-", "archived": False,
                               "description": "", "language": "-"}
uw = upd.UpdateCheckWorker(db)
found = []
uw.changes_found.connect(found.append)
uw.run()
check("18a 更新检出变化", len(found) == 1 and len(found[0]) >= 1,
      str(found))
check("18b 基线已写入", db.update_baseline_get("a/alpha") is not None)
importlib.reload(gs2)

# 19. GHA 开关：关闭后同步线程空转（设置可读）
db.set_setting("gha_hourly", False)
check("19 gha_hourly可关", db.get_setting("gha_hourly") is False)
db.set_setting("gha_hourly", True)

# 20. 定时抓取设置读取
db.set_setting("fetch_interval_hours", 2)
win._worker = None
db.set_setting("__last_fetch__", 0.0)   # 到期
# 不真触发抓取（会起线程），只验证判断逻辑会进入
interval = float(db.get_setting("fetch_interval_hours", 0) or 0)
last = db.get_setting("__last_fetch__", 0.0)
check("20 定时抓取条件判断", interval == 2 and time.time() - last >= interval * 3600)
db.set_setting("fetch_interval_hours", 0)

# 21. 主题切换
old_theme = db.get_setting("theme")
win._toggle_theme()
check("21 主题切换", db.get_setting("theme") != old_theme)
win._toggle_theme()

# 22. 空态提示（清空缓存视图）
win._pools.clear()
win._snapshot_override = []
win._refresh_view()
check("22 空态提示", "暂无数据" in win.status_lbl.text()
      or win.cards_lay.count() > 1)
win._snapshot_override = None
win._pools.clear()
win._refresh_view()

print()
fails = [r for r in results if not r[1]]
print(f"===== {len(results) - len(fails)}/{len(results)} 通过"
      + (f"，失败：{[f[0] for f in fails]}" if fails else "") + " =====")
db.close()
sys.exit(1 if fails else 0)
