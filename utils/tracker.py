"""
Campaign tracker.

A real deletion campaign runs across dozens of brokers over weeks, each with
its own statutory response clock. This module is the piece that turns
Non-Pursuit from a one-shot letter generator into something worth keeping
open: it logs every request sent, and computes each one's deadline and
overdue status so a user can see at a glance what still needs a follow-up.

Backed by a local SQLite file — no server, no account, nothing leaves the
user's machine.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

STATUS_OPTIONS = ["Sent", "Awaiting Response", "Complete", "Non-Compliant"]


@contextmanager
def _connect(db_path: str):
    """One place that opens, migrates, and always closes the connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_name TEXT NOT NULL,
            channel TEXT NOT NULL,
            date_sent TEXT NOT NULL,
            response_window_days INTEGER NOT NULL,
            status TEXT NOT NULL,
            notes TEXT
        )
        """
    )
    # Migration: older databases predate this column. Needed to know when a
    # request actually became Complete, so retention can count from that
    # date rather than date_sent (those aren't the same event).
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(requests)")}
    if "status_updated_at" not in existing_columns:
        conn.execute("ALTER TABLE requests ADD COLUMN status_updated_at TEXT")
        conn.execute(
            "UPDATE requests SET status_updated_at = date_sent WHERE status_updated_at IS NULL"
        )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def add_request(db_path: str, broker_name: str, channel: str,
                 response_window_days: int, notes: str = "") -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO requests "
            "(broker_name, channel, date_sent, response_window_days, status, notes, status_updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                broker_name,
                channel,
                today,
                response_window_days,
                "Sent",
                notes,
                today,
            ),
        )
        conn.commit()


def update_status(db_path: str, request_id: int, status: str) -> None:
    if status not in STATUS_OPTIONS:
        raise ValueError(f"Unknown status: {status}")
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE requests SET status = ?, status_updated_at = ? WHERE id = ?",
            (status, datetime.now().strftime("%Y-%m-%d"), request_id),
        )
        conn.commit()


def purge_expired_notes(db_path: str, retention_days: int) -> int:
    """Clear `notes` on requests that have been Complete for longer than
    retention_days. Everything else (dates, status, broker, deadline) is
    left alone — this only touches the one free-text field that could hold
    anything identifying. Returns how many rows were cleared.
    """
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE requests SET notes = '' "
            "WHERE status = 'Complete' AND status_updated_at < ? "
            "AND notes IS NOT NULL AND notes != ''",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount


def delete_request(db_path: str, request_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM requests WHERE id = ?", (request_id,))
        conn.commit()


def get_all_requests(db_path: str) -> list[dict]:
    """All tracked requests, each with a computed deadline and days-remaining."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM requests ORDER BY date_sent DESC").fetchall()

    today = datetime.now().date()
    results = []
    for row in rows:
        record = dict(row)
        sent_date = datetime.strptime(record["date_sent"], "%Y-%m-%d").date()
        deadline = sent_date + timedelta(days=record["response_window_days"])
        days_remaining = (deadline - today).days
        record["deadline"] = deadline.strftime("%Y-%m-%d")
        record["days_remaining"] = days_remaining
        record["is_overdue"] = days_remaining < 0 and record["status"] != "Complete"
        results.append(record)
    return results
