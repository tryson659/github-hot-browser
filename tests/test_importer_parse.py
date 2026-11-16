# -*- coding: utf-8 -*-
"""R07：名单解析不再从三级路径截取虚假仓库。"""
import pytest

from app.services.importer import parse_repo_names


@pytest.mark.parametrize("text,expected", [
    ("a/b/c", []),                       # 裸三级路径 → 不截取
    ("a/b/c d/e", ["d/e"]),
    ("github.com/a/b/c", ["a/b"]),       # GitHub 链接深层路径 → 归一到仓库
    ("https://github.com/a/b/tree/main", ["a/b"]),
    ("https://github.com/a/b/issues/12", ["a/b"]),
    ("https://gitlab.com/a/b", []),      # 其他域名
    ("https://mygithub.com/a/b", []),
    ("https://gist.github.com/a/b", []),
    ("a/b.git, c/d.", ["a/b", "c/d"]),
    ("[a/b](https://github.com/a/b)", ["a/b"]),
    ("A/B a/b", ["A/B"]),                # 大小写不敏感去重
    ("github.com/features/actions", []), # 保留路径不是仓库
])
def test_parse_cases(text, expected):
    assert parse_repo_names(text) == expected


def test_preserves_text_order():
    text = ("https://github.com/first/repo\n"
            "second/repo\n"
            "看这个 github.com/third/repo 不错")
    assert parse_repo_names(text) == ["first/repo", "second/repo", "third/repo"]


def test_duplicates_case_insensitive():
    assert parse_repo_names("Owner/Repo owner/repo OWNER/REPO") == ["Owner/Repo"]
