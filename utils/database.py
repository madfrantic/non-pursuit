"""
Baseline identity/location profile -- the seed data (name, birth year,
contact info, current + historical addresses) that the rest of the app
personalizes the search flow and letters around.

Backed by the same local SQLite file the campaign tracker and exposure
checks use.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PROFILE_COLUMNS = [
    "first_name",
    "last_name",
    "middle_name",
    "birth_year",
    "email_address",
    "phone_number",
    "current_city",
    "current_state",
    "current_zip_code",
    "historical_zip_codes",
]


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS target_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            middle_name TEXT,
            birth_year INTEGER,
            email_address TEXT,
            phone_number TEXT,
            current_city TEXT,
            current_state TEXT,
            current_zip_code TEXT,
            historical_zip_codes TEXT
        )
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """Create the target_profile table if it doesn't already exist."""
    with _connect(db_path):
        pass


def get_latest_target_profile(db_path: str) -> dict | None:
    """Return the most recently saved profile as a dict, or None if no
    profile has been saved yet."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM target_profile ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def insert_target_profile(db_path: str, data: dict) -> int:
    """Insert a new profile record. Only PROFILE_COLUMNS are read from
    `data` and every value is bound as a parameter, so nothing from the
    form is ever string-formatted into SQL. Returns the new row's id.
    """
    values = [data.get(col) for col in PROFILE_COLUMNS]
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"INSERT INTO target_profile ({', '.join(PROFILE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in PROFILE_COLUMNS)})",
            values,
        )
        conn.commit()
        return cursor.lastrowid
