# -*- coding: utf-8 -*-
"""批次 2：仓库身份规范化与去重键。"""
import pytest

from app.domain.identity import (
    is_valid_repo_name,
    normalize_repo_name,
    repo_key,
    try_normalize_repo_name,
)


@pytest.mark.parametrize("raw,expected", [
    ("owner/repo", "owner/repo"),
    ("https://github.com/owner/repo", "owner/repo"),
    ("http://www.github.com/owner/repo/tree/main", "owner/repo"),
    ("github.com/owner/repo.git", "owner/repo"),
    ("  owner/repo/  ", "owner/repo"),
])
def test_normalize(raw, expected):
    assert normalize_repo_name(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "owner", "a/b/c", "https://gitlab.com/a/b", "features/actions",
    "owner/.git",
])
def test_invalid_or_deep(raw):
    assert try_normalize_repo_name(raw) is None


def test_repo_key_case_insensitive():
    assert repo_key("Owner/Repo") == repo_key("owner/repo") == "owner/repo"


def test_validation_rules():
    assert is_valid_repo_name("owner/repo")
    assert not is_valid_repo_name("owner")
    assert not is_valid_repo_name("owner/repo/extra")
