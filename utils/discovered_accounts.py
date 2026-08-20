"""
Persists accounts the footprint scanner turned up, so a scan becomes a
worklist instead of a result that vanishes when the tab closes.

Same local SQLite file as the campaign tracker, exposure checks and
baseline profile. No user_id column: this app is single-profile
everywhere else (target_profile is latest-row-wins), so a foreign key to
a user that can't exist yet would be dead weight pretending to be
structure.

Identity is (platform, target_identifier) -- one row per handle per
platform. Rescanning is expected to be routine, so a re-discovery
refreshes the machine-derived fields (confidence, URL, timestamp) but
deliberately leaves `status` and `discovered_date` alone. Triage is the
user's own work; a rescan that reset "Flagged for closure" back to "New"
would quietly destroy it.

Rows carrying PII (a handle, a profile URL) don't live here forever. Once
an account is Closed or Ignored -- the two terminal states, where the
user is done with it -- the row is purged after PII_RETENTION_DAYS, the
same convention the tracker uses for notes on completed requests.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

STATUS_NEW = "New"
STATUS_FLAGGED = "Flagged for closure"
STATUS_CLOSED = "Closed"
STATUS_IGNORED = "Ignored"

STATUS_OPTIONS = [STATUS_NEW, STATUS_FLAGGED, STATUS_CLOSED, STATUS_IGNORED]

# States meaning "the user is finished with this row", which is what makes
# it eligible for retention cleanup. Flagged-for-closure is deliberately
# excluded -- that's still open work.
TERMINAL_STATUSES = (STATUS_CLOSED, STATUS_IGNORED)


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovered_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            category TEXT,
            target_identifier TEXT NOT NULL,
            profile_url TEXT,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL,
            discovered_date TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(platform, target_identifier)
        )
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    with _connect(db_path):
        pass


def save_discoveries(db_path: str, rows: list) -> int:
    """Upsert scan results. Returns how many rows were written.

    ON CONFLICT refreshes only what the scanner owns. status and
    discovered_date are left untouched so re-running a scan never
    overwrites the user's triage or backdates when something was first
    seen.
    """
    if not rows:
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    payload = [
        (
            row["platform"],
            row.get("category", ""),
            row["target_identifier"],
            row.get("profile_url", ""),
            row["confidence"],
            STATUS_NEW,
            today,
            today,
        )
        for row in rows
    ]

    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO discovered_accounts
                (platform, category, target_identifier, profile_url,
                 confidence, status, discovered_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, target_identifier) DO UPDATE SET
                confidence = excluded.confidence,
                profile_url = excluded.profile_url,
                category = excluded.category,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        conn.commit()
    return len(payload)


def get_all(db_path: str) -> list:
    """Every stored account, newest discovery first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM discovered_accounts ORDER BY discovered_date DESC, platform ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def update_status(db_path: str, row_id: int, status: str) -> None:
    if status not in STATUS_OPTIONS:
        raise ValueError(f"Unknown status: {status}")
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE discovered_accounts SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now().strftime("%Y-%m-%d"), row_id),
        )
        conn.commit()


def delete_account(db_path: str, row_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM discovered_accounts WHERE id = ?", (row_id,))
        conn.commit()


def purge_finished(db_path: str, retention_days: int) -> int:
    """Delete Closed/Ignored rows untouched for longer than
    retention_days. Returns how many were removed, so the UI can say so
    rather than silently deleting the user's data."""
    cutoff = (datetime.now().date() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    placeholders = ", ".join("?" for _ in TERMINAL_STATUSES)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"DELETE FROM discovered_accounts "
            f"WHERE status IN ({placeholders}) AND updated_at < ?",
            (*TERMINAL_STATUSES, cutoff),
        )
        conn.commit()
        return cursor.rowcount
