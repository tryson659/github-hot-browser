# -*- coding: utf-8 -*-
"""R09：导出保留来源/指标/窗口/覆盖，未知值不伪装成 0。"""
import csv

from app.core.models import Repo
from app.services import exporter


def _repo(**kw):
    data = dict(name="o/r", url="https://github.com/o/r", stars=100,
                language="C++", domain="开发工具", source="GH Archive 自建")
    data.update(kw)
    return Repo.from_dict(data)


def test_markdown_includes_month_metric_and_coverage():
    r = _repo(growth_7d=None, growth_value=233, growth_window="近30天",
              coverage="720/720 小时（100%）")
    md = exporter.to_markdown([r], "本周热门", 30, "日均增长")
    assert "+233" in md
    assert "近30天" in md and "GH Archive 自建" in md
    assert "720/720" in md


def test_csv_keeps_raw_value_and_blank_unknown(tmp_path):
    month = _repo(name="m/1", growth_7d=None, growth_value=233,
                  growth_window="近30天")
    unknown = _repo(name="u/1", growth_7d=None)
    path = tmp_path / "out.csv"
    exporter.to_csv([month, unknown], str(path))

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["增长值"] == "233"
    assert rows[0]["近7天增长"] == ""
    assert rows[0]["增长指标"] == "窗口增量（近30天）"
    assert rows[1]["增长值"] == "" and rows[1]["近7天增长"] == ""


def test_csv_formula_injection_is_neutralized(tmp_path):
    r = _repo(description="=HYPERLINK(\"http://evil\")",
              language="+cmd", domain="@sum")
    path = tmp_path / "out.csv"
    exporter.to_csv([r], str(path))
    text = path.read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in text
    assert "'+cmd" in text and "'@sum" in text


def test_html_includes_source_and_value():
    r = _repo(growth_7d=None, growth_value=12, growth_window="近7天",
              coverage="168/169 小时（99%）")
    out = exporter.to_html([r], "本周热门", 7, "近7天增长")
    assert "+12" in out and "GH Archive 自建" in out and "168/169" in out
