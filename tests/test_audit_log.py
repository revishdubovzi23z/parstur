"""Tests for 8.18 — audit log append/list/mark_undone helpers.

The HTTP-level undo path is integration-tested implicitly via the
DAO + the `rebind` JSON shape it stores.
"""

from __future__ import annotations

import json


def test_append_and_list(tmp_db):
    aid = tmp_db.append_audit(
        action="rebind",
        item_id=42,
        field="kp_id",
        old_value=json.dumps({"kp_id": "111"}),
        new_value=json.dumps({"kp_id": "222"}),
    )
    assert aid > 0

    rows = tmp_db.list_audit()
    assert len(rows) == 1
    assert rows[0]["id"] == aid
    assert rows[0]["item_id"] == 42
    assert rows[0]["undone"] == 0


def test_list_filtered_by_item(tmp_db):
    a1 = tmp_db.append_audit(action="rebind", item_id=1)
    _a2 = tmp_db.append_audit(action="rebind", item_id=2)
    rows = tmp_db.list_audit(item_id=1)
    assert [r["id"] for r in rows] == [a1]


def test_list_orders_newest_first(tmp_db):
    a1 = tmp_db.append_audit(action="rebind", item_id=1)
    a2 = tmp_db.append_audit(action="rebind", item_id=1)
    rows = tmp_db.list_audit()
    assert [r["id"] for r in rows] == [a2, a1]


def test_mark_undone_idempotent(tmp_db):
    aid = tmp_db.append_audit(action="rebind", item_id=1)
    assert tmp_db.mark_audit_undone(aid) is True
    # Already undone — second call is a no-op (returns False).
    assert tmp_db.mark_audit_undone(aid) is False
    rows = tmp_db.list_audit()
    assert rows[0]["undone"] == 1
