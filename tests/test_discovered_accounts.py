"""
Coverage for the discovered_accounts store, against a real SQLite file in
tmp_path -- same approach as test_exposure_store, since the schema and its
ON CONFLICT behavior are exactly what's worth testing.
"""
from datetime import datetime, timedelta

import pytest

import discovered_accounts as store


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_discovered.db")


def make_row(platform="Reddit", identifier="alice", confidence="CONFIRMED"):
    return {
        "platform": platform,
        "category": "social",
        "target_identifier": identifier,
        "profile_url": f"https://example.com/{identifier}",
        "confidence": confidence,
    }


def _set_updated_at(db_path, row_id, days_ago):
    """Backdate a row so retention logic can be tested without waiting."""
    import sqlite3
    stamp = (datetime.now().date() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE discovered_accounts SET updated_at = ? WHERE id = ?", (stamp, row_id))
    conn.commit()
    conn.close()


def test_empty_store_returns_empty_list(db_path):
    assert store.get_all(db_path) == []


def test_save_and_read_back(db_path):
    store.save_discoveries(db_path, [make_row()])
    rows = store.get_all(db_path)
    assert len(rows) == 1
    assert rows[0]["platform"] == "Reddit"
    assert rows[0]["status"] == store.STATUS_NEW
    assert rows[0]["discovered_date"] == datetime.now().strftime("%Y-%m-%d")


def test_save_empty_list_is_a_noop(db_path):
    assert store.save_discoveries(db_path, []) == 0
    assert store.get_all(db_path) == []


def test_same_handle_on_two_platforms_are_separate_rows(db_path):
    store.save_discoveries(db_path, [
        make_row(platform="Reddit"),
        make_row(platform="Twitch"),
    ])
    assert len(store.get_all(db_path)) == 2


def test_rescan_updates_confidence_without_duplicating(db_path):
    store.save_discoveries(db_path, [make_row(confidence="POSSIBLE")])
    store.save_discoveries(db_path, [make_row(confidence="CONFIRMED")])
    rows = store.get_all(db_path)
    assert len(rows) == 1
    assert rows[0]["confidence"] == "CONFIRMED"


def test_rescan_preserves_user_triage(db_path):
    """The whole reason ON CONFLICT is selective: a rescan must not reset
    a status the user set by hand."""
    store.save_discoveries(db_path, [make_row()])
    row_id = store.get_all(db_path)[0]["id"]
    store.update_status(db_path, row_id, store.STATUS_FLAGGED)

    store.save_discoveries(db_path, [make_row()])
    assert store.get_all(db_path)[0]["status"] == store.STATUS_FLAGGED


def test_update_status_rejects_unknown_value(db_path):
    store.save_discoveries(db_path, [make_row()])
    row_id = store.get_all(db_path)[0]["id"]
    with pytest.raises(ValueError):
        store.update_status(db_path, row_id, "Definitely Not A Status")


def test_delete_account_removes_row(db_path):
    store.save_discoveries(db_path, [make_row()])
    store.delete_account(db_path, store.get_all(db_path)[0]["id"])
    assert store.get_all(db_path) == []


def test_purge_removes_old_closed_rows(db_path):
    store.save_discoveries(db_path, [make_row()])
    row_id = store.get_all(db_path)[0]["id"]
    store.update_status(db_path, row_id, store.STATUS_CLOSED)
    _set_updated_at(db_path, row_id, days_ago=60)

    assert store.purge_finished(db_path, retention_days=30) == 1
    assert store.get_all(db_path) == []


def test_purge_keeps_recent_closed_rows(db_path):
    store.save_discoveries(db_path, [make_row()])
    row_id = store.get_all(db_path)[0]["id"]
    store.update_status(db_path, row_id, store.STATUS_CLOSED)

    assert store.purge_finished(db_path, retention_days=30) == 0
    assert len(store.get_all(db_path)) == 1


def test_purge_keeps_flagged_rows_regardless_of_age(db_path):
    """Flagged-for-closure is open work, not a terminal state -- ageing
    out someone's to-do list would be data loss."""
    store.save_discoveries(db_path, [make_row()])
    row_id = store.get_all(db_path)[0]["id"]
    store.update_status(db_path, row_id, store.STATUS_FLAGGED)
    _set_updated_at(db_path, row_id, days_ago=500)

    assert store.purge_finished(db_path, retention_days=30) == 0
    assert len(store.get_all(db_path)) == 1
