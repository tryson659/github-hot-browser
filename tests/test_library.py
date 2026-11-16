# -*- coding: utf-8 -*-
"""阶段 A：工具库分类、多分类收录、删除保留用户状态。"""
import pytest

from app.core.db import Database
from app.core.models import Repo
from app.domain.library import DEFAULT_CATEGORIES
from app.services import library, tracking
from app.storage.library import LibraryStore
from app.storage.store import Store

NOW = "2026-11-15T12:00:00Z"


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


@pytest.fixture()
def store(db):
    return LibraryStore(db)


def _repo(name="owner/repo", **kw):
    data = dict(name=name, url=f"https://github.com/{name}", stars=100,
                forks=10, language="C++", description="demo",
                pushed="2026-11-01", license="MIT", created="2024-01-01")
    data.update(kw)
    return Repo.from_dict(data)


class TestSchema:
    def test_library_tables_and_columns(self, db):
        tables = set(db.query(lambda c: [
            r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]))
        assert {"library_entries", "categories", "library_categories"} <= tables
        columns = {r[1] for r in db.query(lambda c: c.execute(
            "PRAGMA table_info(repositories)").fetchall())}
        assert {"stars", "forks", "pushed", "license", "created"} <= columns
        assert db.schema_version == 5


class TestCategories:
    def test_seed_idempotent_and_template_count(self, store):
        assert store.seed_defaults() == len(DEFAULT_CATEGORIES)
        assert store.seed_defaults() == 0
        names = [c["name"] for c in store.categories()]
        assert "PPT / 演示工具" in names and "清理工具" in names

    def test_seed_does_not_restore_deleted_or_renamed(self, store):
        store.seed_defaults()
        first = store.categories()[0]
        store.rename_category(first["id"], "我的演示")
        assert store.seed_defaults() == 0
        assert store.category(first["id"])["name"] == "我的演示"

        second = store.categories()[1]
        store.delete_category(second["id"])
        assert store.seed_defaults() == 0
        assert store.category(second["id"])["deleted"] == 1
        assert all(c["id"] != second["id"] for c in store.categories())

    def test_create_rename_and_duplicate_names(self, store):
        store.seed_defaults()
        cid = store.create_category("  写作   工具 ", description="写作")
        assert store.category(cid)["name"] == "写作 工具"
        with pytest.raises(ValueError):
            store.create_category("写作 工具")
        with pytest.raises(ValueError):
            store.create_category(" 写作   工具 ")
        with pytest.raises(ValueError):
            store.create_category("   ")

    def test_reorder_and_hide(self, store):
        store.seed_defaults()
        cats = store.categories()
        reversed_ids = [c["id"] for c in reversed(cats)]
        store.reorder_categories(reversed_ids)
        assert [c["id"] for c in store.categories()] == reversed_ids
        store.update_category(cats[0]["id"], hidden=True)
        assert all(c["id"] != cats[0]["id"]
                   for c in store.categories(include_hidden=False))


class TestEntry:
    def test_duplicate_entry_has_single_row(self, store, db):
        store.seed_defaults()
        ppt = store.category_by_name("PPT / 演示工具")["id"]
        docs = store.category_by_name("文档工具")["id"]
        store.add_repo(_repo(), categories=[ppt, docs])
        store.add_repo(_repo(stars=200), categories=[ppt])
        rows = store.entries()
        assert len(rows) == 1
        assert rows[0]["stars"] == 200          # 元数据刷新，不复制条目
        assert {c["id"] for c in rows[0]["categories"]} == {ppt, docs}

    def test_uncategorized_and_counts(self, store):
        store.seed_defaults()
        store.add_repo(_repo("a/one"))
        ppt = store.category_by_name("PPT / 演示工具")["id"]
        store.add_repo(_repo("b/two"), categories=[ppt])
        assert [r["name"] for r in store.entries(uncategorized=True)] == ["a/one"]
        counts = store.counts()
        assert counts["total"] == 2 and counts["uncategorized"] == 1
        assert counts["by_category"][ppt] == 1

    def test_search_offline(self, store):
        store.seed_defaults()
        store.add_repo(_repo("a/one", description="make slides"))
        store.add_repo(_repo("b/two", description="convert documents"))
        assert [r["name"] for r in store.entries(query="slides")] == ["a/one"]
        assert [r["name"] for r in store.entries(query="DOCUMENTS")] == ["b/two"]

    def test_remove_entry_keeps_favorite_and_tracking(self, store, db):
        store.seed_defaults()
        repo = _repo()
        store.add_repo(repo)
        db.add_favorite(repo)
        tracking.add_tracking(db, repo.name, store=Store(db))
        assert store.remove_entry(repo.name)
        assert not store.entries()
        assert db.is_favorite(repo.name)
        repo_row = store.repos.repository(repo.name)
        assert repo_row is not None
        assert Store(db).tracking(repo_row["id"]) is not None

    def test_delete_category_keeps_entry_and_moves(self, store, db):
        store.seed_defaults()
        ppt = store.category_by_name("PPT / 演示工具")["id"]
        docs = store.category_by_name("文档工具")["id"]
        store.add_repo(_repo(), categories=[ppt])
        stats = store.delete_category(ppt, move_to=docs)
        assert stats["removed"] == 1 and stats["moved"] == 1
        row = store.entries()[0]
        assert [c["id"] for c in row["categories"]] == [docs]
        assert store.category(ppt)["deleted"] == 1

    def test_delete_category_without_move_becomes_uncategorized(self, store):
        store.seed_defaults()
        ppt = store.category_by_name("PPT / 演示工具")["id"]
        store.add_repo(_repo(), categories=[ppt])
        store.delete_category(ppt)
        assert store.entries(uncategorized=True)
        assert store.entries()[0]["categories"] == []

    def test_set_entry_categories(self, store):
        store.seed_defaults()
        docs = store.category_by_name("文档工具")["id"]
        store.add_repo(_repo())
        store.set_entry_categories("owner/repo", [docs])
        assert [c["id"] for c in store.entry("owner/repo")["categories"]] == [docs]

    def test_invalid_category_rejected(self, store, db):
        with pytest.raises(ValueError):
            store.add_repo(_repo(), categories=["missing"])
        assert store.repos.repository("owner/repo") is None   # 未先写仓库


class TestGrowthSort:
    def test_growth_for_uses_same_valid_window(self, store, db):
        store.add_repo(_repo())
        repo_id = store.repos.repository("owner/repo")["id"]
        attrs = dict(source="test", metric="net_stars",
                     observation_key="obs-7", value=12,
                     window_start="2026-11-01", window_end="2026-11-07",
                     completeness="valid")
        store.repos.save_observation(repo_id, **attrs)
        assert library.growth_for(db, ["owner/repo"], 7) == {"owner/repo": 12}
        assert library.growth_for(db, ["owner/repo"], 30) == {}

    def test_partial_growth_ignored(self, store, db):
        store.add_repo(_repo())
        repo_id = store.repos.repository("owner/repo")["id"]
        store.repos.save_observation(
            repo_id, source="test", metric="net_stars",
            observation_key="obs-partial", value=99,
            window_start="2026-11-01", window_end="2026-11-07",
            completeness="partial")
        assert library.growth_for(db, ["owner/repo"], 7) == {}


class TestRestart:
    def test_library_survives_reopen(self, tmp_path):
        path = str(tmp_path / "t.db")
        db = Database(path)
        store = LibraryStore(db)
        store.seed_defaults()
        ppt = store.category_by_name("PPT / 演示工具")["id"]
        store.add_repo(_repo(), categories=[ppt])
        db.close()

        db2 = Database(path)
        try:
            store2 = LibraryStore(db2)
            rows = store2.entries()
            assert len(rows) == 1
            assert [c["id"] for c in rows[0]["categories"]] == [ppt]
            assert store2.seed_defaults() == 0
        finally:
            db2.close()
