"""
Persists self-search results (per broker + per category) with a timestamp.

Previously these answers lived only in st.session_state, which resets every
time the browser tab closes -- there was no way to know how long ago
something was actually checked, so "recheck due" staleness couldn't exist.
Backed by the same local SQLite file the campaign tracker uses. Only
meaningful answers are stored (a category that's never been checked simply
has no row) -- "not checked yet" and "checked, over a month ago" are
different things and this is what lets the UI tell them apart.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exposure_checks (
            category TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            checked_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def get_all_checks(db_path: str) -> dict:
    """category -> {"value": str, "checked_at": "YYYY-MM-DD"}"""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM exposure_checks").fetchall()
    return {row["category"]: {"value": row["value"], "checked_at": row["checked_at"]} for row in rows}


def record_check(db_path: str, category: str, value: str) -> None:
    """Store/update a category's answer, stamped with today's date."""
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO exposure_checks (category, value, checked_at) VALUES (?, ?, ?) "
            "ON CONFLICT(category) DO UPDATE SET value = excluded.value, checked_at = excluded.checked_at",
            (category, value, today),
        )
        conn.commit()


def days_since_checked(checked_at: str) -> int:
    checked_date = datetime.strptime(checked_at, "%Y-%m-%d").date()
    return (datetime.now().date() - checked_date).days


def is_stale(checked_at: str, stale_days: int) -> bool:
    return days_since_checked(checked_at) >= stale_days
