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
import hashlib
import logging
import argparse
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
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



# ---------------------------------------------------------------------------
# Master-password vault (ported from chino/GLM.py VaultManager)
# ---------------------------------------------------------------------------
# chino derived its Fernet key from a master password with PBKDF2-HMAC-SHA256
# at 480k iterations. That part is kept. What is NOT kept is its salt:
#
#     salt = b'data_broker_agent_v1'   # chino/GLM.py:272
#
# A salt hardcoded into the source is the same salt on every install, which
# is what a salt exists to prevent -- it lets one precomputed table attack
# every user of the program at once. chino's own comment conceded the point
# ("In production, use random salt stored separately"). So this generates 16
# random bytes per install and stores them beside the database.
#
# The salt is not a secret and does not need protecting; it needs to be
# unique and durable. Losing it means the password no longer derives the
# same key, so it lives in data/ with the database it belongs to.
#
# WHY THE VAULT IS AN OVERLAY AND NOT A REPLACEMENT
#
# There is already real data in data/tracker.db encrypted under the .env
# ENCRYPTION_KEY. Switching the cipher outright would render every one of
# those rows unreadable -- silently, because _decrypt() returns the input
# unchanged when it cannot decrypt. So:
#
#   * reads try the vault cipher first, then fall back to the legacy .env
#     key, so pre-vault rows keep opening;
#   * writes use the vault when one is unlocked, and the legacy key when
#     none is, so the headless surfaces (api/, main.py, pytest) that never
#     prompt for a password behave exactly as they did before.
#
# The Streamlit app requires the password at launch (pages/ gate). That is a
# UI-layer decision and deliberately not enforced here: making import-time
# password entry mandatory in this module would break the FastAPI service,
# which has no console to prompt at.

SALT_FILENAME = "vault.salt"
KDF_ITERATIONS = 480_000
SALT_LENGTH = 16


def _salt_path(db_path: str | None = None) -> Path:
    base = Path(db_path).parent if db_path else Path("data")
    return base / SALT_FILENAME


def load_or_create_salt(db_path: str | None = None) -> bytes:
    """The per-install PBKDF2 salt, generated on first use."""
    path = _salt_path(db_path)
    if path.exists():
        salt = path.read_bytes()
        if len(salt) == SALT_LENGTH:
            return salt
        _log.warning("Vault salt at %s is malformed; regenerating.", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_LENGTH)
    path.write_bytes(salt)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return salt


def derive_vault_key(master_password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 -> a urlsafe-base64 Fernet key."""
    if not master_password:
        raise ValueError("Master password must not be empty.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(master_password.encode()))


class VaultManager:
    """Encrypts and decrypts PII under a key derived from a master password."""

    def __init__(self, master_password: str, db_path: str | None = None):
        self._salt = load_or_create_salt(db_path)
        self._fernet = Fernet(derive_vault_key(master_password, self._salt))

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(str(plaintext).encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        return self._fernet.decrypt(
            ciphertext.encode() if isinstance(ciphertext, str) else ciphertext
        ).decode()

    @staticmethod
    def hash_for_verification(value: str) -> str:
        """One-way hash, for checking a value matches without storing it."""
        return hashlib.sha256(value.encode()).hexdigest()


# The unlocked vault for this process, or None. Set by unlock_vault().
_VAULT: "VaultManager | None" = None

# A password check that does not store the password. Written on first
# unlock; every later unlock must produce the same digest or it is the
# wrong password and we refuse rather than silently writing rows the
# original password can never read back.
_VERIFIER_FILENAME = "vault.verifier"


def _verifier_path(db_path: str | None = None) -> Path:
    base = Path(db_path).parent if db_path else Path("data")
    return base / _VERIFIER_FILENAME


def unlock_vault(master_password: str, db_path: str | None = None) -> bool:
    """Derive and install the vault cipher for this process.

    Returns True on success. Raises ValueError when the password does not
    match the one this vault was created with.
    """
    global _VAULT
    salt = load_or_create_salt(db_path)
    digest = hashlib.sha256(
        derive_vault_key(master_password, salt)
    ).hexdigest()

    path = _verifier_path(db_path)
    if path.exists():
        if path.read_text().strip() != digest:
            raise ValueError("Incorrect master password.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    _VAULT = VaultManager(master_password, db_path)
    _log.info("Vault unlocked.")
    return True


def lock_vault() -> None:
    """Drop the in-memory vault cipher."""
    global _VAULT
    _VAULT = None


def vault_is_unlocked() -> bool:
    return _VAULT is not None


def vault_exists(db_path: str | None = None) -> bool:
    """True once a master password has been set on this install."""
    return _verifier_path(db_path).exists()


_CIPHER = None

def _cipher() -> Fernet:
    """Lazily initialize the Fernet cipher with the encryption key."""
    global _CIPHER
    if _CIPHER is None:
        _CIPHER = Fernet(_load_or_generate_key())
    return _CIPHER


def _encrypt(value) -> str | None:
    """Encrypt a string value, or return None if the value is None/empty.

    Uses the unlocked master-password vault when there is one, and the
    legacy .env key otherwise -- see the vault notes above.
    """
    if value is None or value == "":
        return value
    s = str(value)
    if not s:
        return ""
    if _VAULT is not None:
        return _VAULT.encrypt(s)
    return _cipher().encrypt(s.encode()).decode()


def _decrypt(value) -> str | None:
    """Decrypt a string value, handling None and already-plaintext gracefully.

    Tries the unlocked vault first, then the legacy .env key. The fallback
    is what keeps rows written before the vault existed readable after it
    is introduced; without it those rows would come back as ciphertext
    strings and look, to every caller, like corrupted names.
    """
    if value is None or value == "":
        return value
    s = str(value)
    if _VAULT is not None:
        try:
            return _VAULT.decrypt(s)
        except Exception:
            pass
    try:
        return _cipher().decrypt(s.encode()).decode()
    except Exception:
        return s


# WAL is the right journal mode for this app -- the agent scheduler writes
# on its own thread while the Streamlit script thread reads, and WAL is
# what keeps those from blocking each other. It is not universally
# available, though: WAL has to create <db>-wal and <db>-shm beside the
# database file and mmap the -shm segment, which NFS/SMB mounts and some
# container volume drivers refuse. SQLite surfaces that as an
# OperationalError from the PRAGMA itself, which took the whole app down
# at import time because init_db() runs before anything renders.
#
# DELETE journalling needs no sidecar files, so it is the honest fallback:
# more lock contention under concurrency, but it opens.
_JOURNAL_FALLBACK_WARNED: set[str] = set()


def apply_journal_mode(conn: sqlite3.Connection, db_path: str) -> str:
    """Enable WAL, degrading to DELETE where WAL is unavailable.

    Returns the journal mode actually in force ("wal" or "delete"). If
    DELETE fails too the error propagates -- that means the database is
    genuinely not writable, and every statement after this one would fail
    anyway, so masking it here would only move the crash somewhere less
    obvious.
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        return "wal"
    except sqlite3.OperationalError as exc:
        # Connections are opened per call, so warning unconditionally would
        # log once per read for the life of the process. Once per path
        # explains the degraded mode without flooding the log.
        if db_path not in _JOURNAL_FALLBACK_WARNED:
            _JOURNAL_FALLBACK_WARNED.add(db_path)
            _log.warning(
                "WAL journalling unavailable for %s (%s); falling back to DELETE. "
                "Expect more lock contention while the scheduler writes.",
                db_path, exc,
            )
        conn.execute("PRAGMA journal_mode=DELETE;")
        return "delete"


@contextmanager
def _connect(db_path: str):
    # Path(...).parent rather than os.path.dirname(...): dirname("t.db") is
    # "" and os.makedirs("") raises FileNotFoundError, which would break
    # every caller that passes a bare relative filename (the test suite
    # does). Path(".").mkdir(exist_ok=True) is a no-op.
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=20.0)
    journal_mode = apply_journal_mode(conn, db_path)
    # synchronous=NORMAL is only durable under WAL. With DELETE journalling
    # it can lose a committed transaction on power loss, so the fallback
    # path pays for FULL rather than silently weakening durability on the
    # one file holding the user's case record.
    conn.execute(
        "PRAGMA synchronous=NORMAL;" if journal_mode == "wal"
        else "PRAGMA synchronous=FULL;"
    )
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
