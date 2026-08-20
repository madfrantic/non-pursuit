"""
Baseline identity/location profile -- the seed data (name, birth year,
contact info, current + historical addresses) that the rest of the app
personalizes the search flow and letters around.

Backed by the same local SQLite file the campaign tracker and exposure
checks use.
"""
import sqlite3
import json
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
    "relational_entities",
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
            historical_zip_codes TEXT,
            relational_entities TEXT
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(target_profile)")}
    if "relational_entities" not in columns:
        conn.execute("ALTER TABLE target_profile ADD COLUMN relational_entities TEXT")
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
        if not row:
            return None
        profile = dict(row)
        try:
            profile["relational_entities"] = json.loads(profile.get("relational_entities") or "[]")
        except (TypeError, json.JSONDecodeError):
            profile["relational_entities"] = []
        return profile


def insert_target_profile(db_path: str, data: dict) -> int:
    """Insert a new profile record. Only PROFILE_COLUMNS are read from
    `data` and every value is bound as a parameter, so nothing from the
    form is ever string-formatted into SQL. Returns the new row's id.
    """
    values = [
        json.dumps(data.get(col) or []) if col == "relational_entities" else data.get(col)
        for col in PROFILE_COLUMNS
    ]
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"INSERT INTO target_profile ({', '.join(PROFILE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in PROFILE_COLUMNS)})",
            values,
        )
        conn.commit()
        return cursor.lastrowid


def parse_historical_zips(raw: str | None) -> list[str]:
    """Split the free-form historical_zip_codes field (the Dashboard form
    accepts "one per line or comma-separated") into a clean, ordered,
    deduplicated list of ZIP strings -- so a broker search can be re-run
    once per former address without also re-parsing this everywhere it's
    needed."""
    if not raw:
        return []
    zips = []
    for chunk in raw.replace(",", "\n").splitlines():
        zip_code = chunk.strip()
        if zip_code and zip_code not in zips:
            zips.append(zip_code)
    return zips
