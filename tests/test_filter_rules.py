"""Tests for 8.5 — filter_rules CRUD and get_feed integration."""

from __future__ import annotations

import pytest


def _add_item(d, *, title: str, description: str = "", year: int = 2020) -> int:
    from app_core import normalize_title

    with d._conn() as c:
        cur = c.execute(
            "INSERT INTO items (category_id, title, description, year, title_norm) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, title, description, year, normalize_title(title)),
        )
        return int(cur.lastrowid)


def test_create_validates_field_and_action(tmp_db):
    with pytest.raises(ValueError):
        tmp_db.create_filter_rule(name="bad", field="not_a_field", pattern=".*", action="hide")
    with pytest.raises(ValueError):
        tmp_db.create_filter_rule(name="bad2", field="title", pattern=".*", action="explode")


def test_create_validates_regex(tmp_db):
    with pytest.raises(ValueError):
        tmp_db.create_filter_rule(name="bad", field="title", pattern="[unclosed", action="hide")


def test_list_default_returns_all_only_enabled_filters(tmp_db):
    a = tmp_db.create_filter_rule(name="a", field="title", pattern="x", action="hide", enabled=True)
    b = tmp_db.create_filter_rule(
        name="b", field="title", pattern="y", action="hide", enabled=False
    )
    all_rules = tmp_db.list_filter_rules()
    assert {r["id"] for r in all_rules} == {a, b}
    enabled_only = tmp_db.list_filter_rules(only_enabled=True)
    assert {r["id"] for r in enabled_only} == {a}


def test_get_feed_drops_items_matching_hide_rule(tmp_db):
    keep_id = _add_item(tmp_db, title="Inception")
    drop_id = _add_item(tmp_db, title="Bad Movie Vol. 2")
    tmp_db.create_filter_rule(name="no-bad", field="title", pattern=r"\bBad\b", action="hide")

    out = tmp_db.get_feed(category_id=1)
    ids = {i["id"] for i in out["items"]}
    assert keep_id in ids
    assert drop_id not in ids


def test_get_feed_keeps_dropped_item_when_rule_disabled(tmp_db):
    drop_id = _add_item(tmp_db, title="Trash 1")
    rid = tmp_db.create_filter_rule(
        name="trash", field="title", pattern="Trash", action="hide", enabled=False
    )

    ids = {i["id"] for i in tmp_db.get_feed(category_id=1)["items"]}
    assert drop_id in ids
    tmp_db.update_filter_rule(rid, enabled=True)
    ids = {i["id"] for i in tmp_db.get_feed(category_id=1)["items"]}
    assert drop_id not in ids


def test_highlight_rule_decorates_matched_rules(tmp_db):
    item_id = _add_item(tmp_db, title="Sci-Fi epic")
    tmp_db.create_filter_rule(name="scifi-tag", field="title", pattern="Sci", action="highlight")

    out = tmp_db.get_feed(category_id=1)
    matched = next(i for i in out["items"] if i["id"] == item_id)
    assert matched.get("matched_rules") == ["scifi-tag"]


def test_invalid_regex_in_db_doesnt_500_get_feed(tmp_db):
    """A rule that bypasses validation (e.g. raw INSERT or schema drift)
    must not break the whole feed — the REGEXP function returns 0 on
    re.error so the row simply doesn't match.
    """
    _add_item(tmp_db, title="some movie")
    # Bypass validation by writing directly.
    with tmp_db._conn() as c:
        c.execute(
            "INSERT INTO filter_rules (name, field, pattern, action, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            ("broken", "title", "[invalid", "hide"),
        )

    # Must not raise.
    out = tmp_db.get_feed(category_id=1)
    assert out["totalPages"] >= 1


def test_update_and_delete(tmp_db):
    rid = tmp_db.create_filter_rule(name="orig", field="title", pattern=".", action="hide")
    assert tmp_db.update_filter_rule(rid, name="renamed") is True
    assert tmp_db.list_filter_rules()[0]["name"] == "renamed"
    assert tmp_db.delete_filter_rule(rid) is True
    assert tmp_db.list_filter_rules() == []
    assert tmp_db.delete_filter_rule(rid) is False
