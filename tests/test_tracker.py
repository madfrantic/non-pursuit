import sqlite3
from datetime import datetime, timedelta

import pytest

import tracker


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_tracker.db")


def _set_dates(db_path, request_id, date_sent=None, status_updated_at=None):
    """Backdate a row directly -- add_request always stamps today, so tests
    that need a specific past date (deadline math, retention cutoffs) have
    to reach past the public API."""
    conn = sqlite3.connect(db_path)
    if date_sent is not None:
        conn.execute("UPDATE requests SET date_sent = ? WHERE id = ?", (date_sent, request_id))
    if status_updated_at is not None:
        conn.execute("UPDATE requests SET status_updated_at = ? WHERE id = ?", (status_updated_at, request_id))
    conn.commit()
    conn.close()


def test_add_request_creates_row_with_sent_status(db_path):
    tracker.add_request(db_path, "Spokeo", "email", 30, notes="test note")
    requests = tracker.get_all_requests(db_path)
    assert len(requests) == 1
    assert requests[0]["broker_name"] == "Spokeo"
    assert requests[0]["status"] == "Sent"
    assert requests[0]["notes"] == "test note"
    assert requests[0]["date_sent"] == datetime.now().strftime("%Y-%m-%d")


def test_get_all_requests_computes_deadline_and_days_remaining(db_path):
    tracker.add_request(db_path, "MyLife", "email", 30)
    req = tracker.get_all_requests(db_path)[0]
    sent = datetime.strptime(req["date_sent"], "%Y-%m-%d").date()
    expected_deadline = sent + timedelta(days=30)
    assert req["deadline"] == expected_deadline.strftime("%Y-%m-%d")
    assert req["days_remaining"] == (expected_deadline - datetime.now().date()).days


def test_future_deadline_is_not_overdue(db_path):
    tracker.add_request(db_path, "MyLife", "email", 30)
    req = tracker.get_all_requests(db_path)[0]
    assert req["is_overdue"] is False


def test_past_deadline_is_overdue_when_not_complete(db_path):
    tracker.add_request(db_path, "MyLife", "email", 30)
    req_id = tracker.get_all_requests(db_path)[0]["id"]
    past_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    _set_dates(db_path, req_id, date_sent=past_date)
    req = tracker.get_all_requests(db_path)[0]
    assert req["days_remaining"] < 0
    assert req["is_overdue"] is True


def test_complete_status_never_shows_overdue(db_path):
    # A request can be well past its statutory window and still correctly
    # marked Complete -- overdue should only ever describe requests still
    # waiting on a response.
    tracker.add_request(db_path, "MyLife", "email", 30)
    req_id = tracker.get_all_requests(db_path)[0]["id"]
    past_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    _set_dates(db_path, req_id, date_sent=past_date)
    tracker.update_status(db_path, req_id, "Complete")
    req = tracker.get_all_requests(db_path)[0]
    assert req["days_remaining"] < 0
    assert req["status"] == "Complete"
    assert req["is_overdue"] is False


def test_update_status_updates_status_updated_at(db_path):
    tracker.add_request(db_path, "MyLife", "email", 30)
    req_id = tracker.get_all_requests(db_path)[0]["id"]
    _set_dates(db_path, req_id, status_updated_at="2000-01-01")
    tracker.update_status(db_path, req_id, "Awaiting Response")
    req = tracker.get_all_requests(db_path)[0]
    assert req["status"] == "Awaiting Response"
    assert req["status_updated_at"] == datetime.now().strftime("%Y-%m-%d")


def test_update_status_rejects_unknown_status(db_path):
    tracker.add_request(db_path, "MyLife", "email", 30)
    req_id = tracker.get_all_requests(db_path)[0]["id"]
    with pytest.raises(ValueError):
        tracker.update_status(db_path, req_id, "Not A Real Status")


def test_purge_expired_notes_clears_only_old_complete_rows(db_path):
    tracker.add_request(db_path, "Old Complete", "email", 30, notes="old sensitive note")
    tracker.add_request(db_path, "Recent Complete", "email", 30, notes="recent sensitive note")
    tracker.add_request(db_path, "Still Active", "email", 30, notes="active note")
    requests = tracker.get_all_requests(db_path)
    old_id = next(r["id"] for r in requests if r["broker_name"] == "Old Complete")
    recent_id = next(r["id"] for r in requests if r["broker_name"] == "Recent Complete")

    tracker.update_status(db_path, old_id, "Complete")
    tracker.update_status(db_path, recent_id, "Complete")
    old_cutoff_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    _set_dates(db_path, old_id, status_updated_at=old_cutoff_date)

    cleared = tracker.purge_expired_notes(db_path, retention_days=30)

    assert cleared == 1
    by_name = {r["broker_name"]: r for r in tracker.get_all_requests(db_path)}
    assert by_name["Old Complete"]["notes"] == ""
    assert by_name["Recent Complete"]["notes"] == "recent sensitive note"
    assert by_name["Still Active"]["notes"] == "active note"


def test_delete_request_removes_row(db_path):
    tracker.add_request(db_path, "MyLife", "email", 30)
    req_id = tracker.get_all_requests(db_path)[0]["id"]
    tracker.delete_request(db_path, req_id)
    assert tracker.get_all_requests(db_path) == []
