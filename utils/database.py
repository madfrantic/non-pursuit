"""
Baseline identity/location profile -- the seed data (name, birth year,
contact info, current + historical addresses) that the rest of the app
personalizes the search flow and letters around.

Backed by the same local SQLite file the campaign tracker and exposure
checks use.

PII columns (name, email, phone, location) are encrypted at rest using
Fernet symmetric encryption. The key is stored in .env (or generated if
missing) and is never committed to git. Read/write are transparent to
callers -- this module handles encrypt/decrypt internally.
"""
import sqlite3
import json
import os
import sys
import base64
import logging
import argparse
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

_log = logging.getLogger("database")

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

# PII columns that are encrypted at rest.
PII_COLUMNS = {
    "first_name", "last_name", "middle_name",
    "email_address", "phone_number",
    "current_city", "current_state", "current_zip_code", "historical_zip_codes",
}


def _load_or_generate_key() -> bytes:
    """Load the Fernet encryption key from .env, or generate and save it."""
    load_dotenv()
    key = os.getenv("ENCRYPTION_KEY")
    if key:
        key_bytes = key.encode() if isinstance(key, str) else key
        try:
            decoded = base64.urlsafe_b64decode(key_bytes)
            if len(decoded) != 32:
                raise ValueError("Key must be 32 bytes")
            return key_bytes
        except Exception as e:
            _log.error("Invalid ENCRYPTION_KEY in .env. Must be a valid 32-byte urlsafe base64 Fernet key: %s", e)
            raise ValueError("Invalid ENCRYPTION_KEY format in .env.") from e

    _log.warning("No ENCRYPTION_KEY found in .env. Generating a new one. CRITICAL: Preserve .env to decrypt your local SQLite database!")
    key = Fernet.generate_key()
    env_path = Path(".env")
    env_path.touch(mode=0o600, exist_ok=True)
    with open(env_path, "a") as f:
        f.write(f"\nENCRYPTION_KEY={key.decode()}\n")
    return key



_CIPHER = None

def _cipher() -> Fernet:
    """Lazily initialize the Fernet cipher with the encryption key."""
    global _CIPHER
    if _CIPHER is None:
        _CIPHER = Fernet(_load_or_generate_key())
    return _CIPHER


def _encrypt(value) -> str | None:
    """Encrypt a string value, or return None if the value is None/empty."""
    if value is None or value == "":
        return value
    s = str(value)
    return _cipher().encrypt(s.encode()).decode() if s else ""


def _decrypt(value) -> str | None:
    """Decrypt a string value, handling None and already-plaintext gracefully."""
    if value is None or value == "":
        return value
    s = str(value)
    try:
        return _cipher().decrypt(s.encode()).decode()
    except Exception:
        return s


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=20.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
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
    """Create the target_profile table if it doesn't already exist.
    Triggers a one-time encryption migration on plaintext data."""
    with _connect(db_path):
        pass
    _migrate_encrypt_plaintext(db_path)


def get_latest_target_profile(db_path: str) -> dict | None:
    """Return the most recently saved profile as a dict, or None if no
    profile has been saved yet. PII columns are decrypted on read."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM target_profile ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        profile = dict(row)
        for col in PII_COLUMNS:
            if col in profile:
                profile[col] = _decrypt(profile[col])
        try:
            profile["relational_entities"] = json.loads(profile.get("relational_entities") or "[]")
        except (TypeError, json.JSONDecodeError):
            profile["relational_entities"] = []
        return profile


def insert_target_profile(db_path: str, data: dict) -> int:
    """Insert a new profile record. Only PROFILE_COLUMNS are read from
    `data` and every value is bound as a parameter, so nothing from the
    form is ever string-formatted into SQL. PII columns are encrypted
    before writing. Returns the new row's id.
    """
    values = []
    for col in PROFILE_COLUMNS:
        val = data.get(col)
        if col == "relational_entities":
            val = json.dumps(val or [])
        elif col in PII_COLUMNS:
            val = _encrypt(val)
        values.append(val)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"INSERT INTO target_profile ({', '.join(PROFILE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in PROFILE_COLUMNS)})",
            values,
        )
        conn.commit()
        return cursor.lastrowid


def _migrate_encrypt_plaintext(db_path: str) -> None:
    """One-time migration: detect plaintext PII and re-encrypt it.

    Called during init_db if plaintext data is detected. Does nothing if
    all PII is already encrypted (or empty).
    """
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM target_profile").fetchall()
        for (row_id,) in rows:
            row = conn.execute("SELECT * FROM target_profile WHERE id = ?", (row_id,)).fetchone()
            profile = dict(row)

            needs_update = False
            for col in PII_COLUMNS:
                val = profile.get(col)
                if val and not val.startswith("gAAAAAA"):
                    try:
                        _decrypt(val)
                    except Exception:
                        needs_update = True
                        profile[col] = _encrypt(val)

            if needs_update:
                updates = ", ".join(f"{col} = ?" for col in PII_COLUMNS)
                values = [profile[col] for col in PII_COLUMNS] + [row_id]
                conn.execute(
                    f"UPDATE target_profile SET {updates} WHERE id = ?",
                    values,
                )
        conn.commit()


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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Database Key Management")
    parser.add_argument("--export-key", action="store_true", help="Export the current Fernet key")
    parser.add_argument("--rotate-key", metavar="NEW_KEY", type=str, help="Rotate to a new Fernet key")
    args = parser.parse_args()

    if args.export_key:
        print(_load_or_generate_key().decode())
    elif args.rotate_key:
        new_key = args.rotate_key.encode()
        try:
            decoded = base64.urlsafe_b64decode(new_key)
            if len(decoded) != 32:
                raise ValueError("Key must be 32 bytes")
        except Exception as e:
            print(f"Error: Invalid new key format. Must be a 32-byte urlsafe base64 string. {e}", file=sys.stderr)
            sys.exit(1)
        
        env_path = Path(".env")
        if env_path.exists():
            content = env_path.read_text()
            lines = []
            for line in content.splitlines():
                if not line.startswith("ENCRYPTION_KEY="):
                    lines.append(line)
            lines.append(f"ENCRYPTION_KEY={new_key.decode()}")
            env_path.write_text("\n".join(lines) + "\n")
        else:
            env_path.touch(mode=0o600, exist_ok=True)
            env_path.write_text(f"ENCRYPTION_KEY={new_key.decode()}\n")
        print("Key successfully rotated in .env.")
