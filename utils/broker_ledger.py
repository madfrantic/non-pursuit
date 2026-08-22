"""
The broker-agent ledger: profiles, brokers, scans, removals, evidence.

Ported from chino/GLM.py's DatabaseManager into this repo's existing
data/tracker.db, alongside the Phase 1 tables (`requests`, `target_profile`,
`broker_campaigns`, exposure checks) rather than in a database of its own.
Every table is created with CREATE TABLE IF NOT EXISTS and every added
column goes through a PRAGMA table_info check, so an existing tracker.db
gains the new tables without any of its rows being touched.

TWO DEVIATIONS FROM chino, BOTH DELIBERATE

1. NO HELD CONNECTION. chino's DatabaseManager opened one sqlite3
   connection in __init__ and kept it on the instance, then read it from a
   QThread and an APScheduler thread. sqlite3 refuses cross-thread use of a
   connection by default, and utils/review_queue.py already documents this
   as one of the two bugs it fixed when porting from the same lineage of
   code. This module opens per call and closes again, like tracker.py and
   review_queue.py -- which is what makes it safe for the background
   scheduler thread in app.py to touch.

2. NO manual_queue TABLE. chino had one; this app already has
   utils/review_queue.py, with a closed reason vocabulary, id-addressed
   rows, and its own database. Creating chino's table too would give the
   app two competing answers to "what does a human still owe?", which is
   exactly the failure review_queue.py was written to end. Instead the two
   columns chino's table had that review_queue lacked -- current_url and
   screenshot_path -- are added to review_queue by migration, and queue
   writes from this module go there. See queue_for_human() below.

PII: pii_fields.encrypted_value holds ciphertext produced by
database._encrypt (the master-password vault when unlocked, the .env key
otherwise). Nothing in this module writes a plaintext PII value to SQLite,
and nothing logs one -- log lines carry ids and broker names only.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import database
import review_queue
from applog import get_logger
from broker_agent_models import (
    Broker,
    BrokerDifficulty,
    Evidence,
    PIIField,
    Profile,
    Removal,
    RemovalStatus,
    ScanStatus,
    coerce_difficulty,
)

_log = get_logger("broker_ledger")

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_TABLES = """
CREATE TABLE IF NOT EXISTS agent_schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pii_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    field_type TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_brokers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT,
    opt_out_url TEXT,
    search_url TEXT,
    difficulty TEXT DEFAULT 'standard',
    removal_method TEXT DEFAULT 'automated',
    supports_gdpr INTEGER DEFAULT 0,
    supports_ccpa INTEGER DEFAULT 1,
    notes TEXT,
    handler_module TEXT,
    compliance_email TEXT,
    is_active INTEGER DEFAULT 1,
    last_updated TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    broker_id INTEGER NOT NULL,
    scan_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    found INTEGER DEFAULT 0,
    search_url_used TEXT,
    search_params TEXT,
    result_url TEXT,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES profiles(id),
    FOREIGN KEY (broker_id) REFERENCES agent_brokers(id)
);

CREATE TABLE IF NOT EXISTS removals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
    broker_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    submission_method TEXT,
    opt_out_url TEXT,
    request_id TEXT,
    submitted_at TEXT,
    confirmed_at TEXT,
    failed_at TEXT,
    failure_reason TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scan_id) REFERENCES scans(id),
    FOREIGN KEY (profile_id) REFERENCES profiles(id),
    FOREIGN KEY (broker_id) REFERENCES agent_brokers(id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    removal_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    file_path TEXT,
    file_hash TEXT,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    FOREIGN KEY (removal_id) REFERENCES removals(id)
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    profile_id INTEGER,
    broker_id INTEGER,
    schedule_cron TEXT,
    interval_days INTEGER,
    last_run TEXT,
    next_run TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pii_profile ON pii_fields(profile_id);
CREATE INDEX IF NOT EXISTS idx_scans_profile ON scans(profile_id);
CREATE INDEX IF NOT EXISTS idx_scans_broker ON scans(broker_id);
CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);
CREATE INDEX IF NOT EXISTS idx_removals_status ON removals(status);
CREATE INDEX IF NOT EXISTS idx_evidence_removal ON evidence(removal_id);
"""

# Columns added after a table first shipped. Each is applied only when
# PRAGMA table_info says it is missing, so re-running is a no-op and an
# existing tracker.db keeps every row it had.
_ADDED_COLUMNS = {
    "agent_brokers": [("compliance_email", "TEXT")],
    "scheduled_tasks": [("interval_days", "INTEGER")],
    "removals": [("notes", "TEXT")],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table not created yet; _TABLES defines it with the column
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                _log.info("Migrated %s: added column %s", table, name)


@contextmanager
def _connect(db_path: str):
    """Open, create, migrate, and always close. One connection per call.

    Per-call rather than held on an object: the scheduler thread in app.py
    and the Streamlit script thread both reach this module, and a shared
    sqlite3 connection across threads is refused by the driver.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_TABLES)
    _migrate(conn)
    conn.execute(
        "INSERT OR IGNORE INTO agent_schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_ledger(db_path: str) -> None:
    """Create/migrate the agent tables. Safe to call repeatedly."""
    with _connect(db_path):
        pass


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def log_activity(db_path: str, action: str, entity_type: str = "",
                 entity_id: Optional[int] = None, details: str = "") -> int:
    """Record one mutation.

    `details` is written verbatim, so callers must keep PII out of it --
    ids and broker names only. Nothing in this module puts a decrypted
    field value here.
    """
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO activity_log (action, entity_type, entity_id, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (action, entity_type, entity_id, details, _now()),
        )
        return cur.lastrowid


def get_activity_log(db_path: str, limit: int = 100) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Profiles + PII
# ---------------------------------------------------------------------------

def create_profile(db_path: str, name: str) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO profiles (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, _now(), _now()),
        )
        profile_id = cur.lastrowid
    log_activity(db_path, "create_profile", "profile", profile_id)
    return profile_id


def get_profiles(db_path: str, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM profiles"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY created_at DESC"
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(query).fetchall()]


def get_profile(db_path: str, profile_id: int) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return dict(row) if row else None


def update_profile(db_path: str, profile_id: int, name: str, is_active: bool = True) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE profiles SET name = ?, is_active = ?, updated_at = ? WHERE id = ?",
            (name, 1 if is_active else 0, _now(), profile_id),
        )
    log_activity(db_path, "update_profile", "profile", profile_id)


def delete_profile(db_path: str, profile_id: int) -> None:
    """Hard-delete a profile and, by cascade, its PII fields."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM pii_fields WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    log_activity(db_path, "delete_profile", "profile", profile_id)


def save_pii_field(db_path: str, profile_id: int, field_type: str, value: str) -> int:
    """Encrypt and store one PII value, replacing any prior value of that type.

    Encryption goes through database._encrypt, so this follows whichever key
    is active -- the master-password vault when unlocked, the .env key when
    not. chino stored a separate `iv` column; Fernet embeds its own IV in
    the token, so that column would always have been empty and is dropped.
    """
    encrypted = database._encrypt(value) or ""
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM pii_fields WHERE profile_id = ? AND field_type = ?",
            (profile_id, field_type),
        )
        cur = conn.execute(
            "INSERT INTO pii_fields (profile_id, field_type, encrypted_value, created_at) "
            "VALUES (?, ?, ?, ?)",
            (profile_id, field_type, encrypted, _now()),
        )
        field_id = cur.lastrowid
        conn.execute("UPDATE profiles SET updated_at = ? WHERE id = ?", (_now(), profile_id))
    # field_type only -- never the value.
    log_activity(db_path, "save_pii_field", "profile", profile_id, field_type)
    return field_id


def get_pii_fields(db_path: str, profile_id: int) -> list[PIIField]:
    """Decrypted PII for a profile."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT field_type, encrypted_value FROM pii_fields WHERE profile_id = ? ORDER BY id",
            (profile_id,),
        ).fetchall()
    return [
        PIIField(field_type=r["field_type"], value=database._decrypt(r["encrypted_value"]) or "")
        for r in rows
    ]


def load_profile(db_path: str, profile_id: int) -> Optional[Profile]:
    """A Profile with its PII fields decrypted and attached."""
    row = get_profile(db_path, profile_id)
    if not row:
        return None
    return Profile(
        id=row["id"],
        name=row["name"],
        fields=get_pii_fields(db_path, profile_id),
        is_active=bool(row["is_active"]),
    )


# ---------------------------------------------------------------------------
# Brokers
# ---------------------------------------------------------------------------

def add_broker(db_path: str, broker: Broker) -> int:
    """Insert a broker, or update the existing row with that name."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO agent_brokers
                (name, url, opt_out_url, search_url, difficulty, removal_method,
                 supports_gdpr, supports_ccpa, notes, handler_module,
                 compliance_email, is_active, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                url = excluded.url,
                opt_out_url = excluded.opt_out_url,
                search_url = excluded.search_url,
                difficulty = excluded.difficulty,
                removal_method = excluded.removal_method,
                supports_gdpr = excluded.supports_gdpr,
                supports_ccpa = excluded.supports_ccpa,
                notes = excluded.notes,
                handler_module = excluded.handler_module,
                compliance_email = excluded.compliance_email,
                last_updated = excluded.last_updated
            """,
            (
                broker.name, broker.url, broker.opt_out_url, broker.search_url,
                coerce_difficulty(broker.difficulty).value, broker.removal_method,
                1 if broker.supports_gdpr else 0, 1 if broker.supports_ccpa else 0,
                broker.notes, broker.handler_module, broker.compliance_email,
                1 if broker.is_active else 0, _now(),
            ),
        )
        row = conn.execute(
            "SELECT id FROM agent_brokers WHERE name = ?", (broker.name,)
        ).fetchone()
    return row["id"]


def get_brokers(db_path: str, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM agent_brokers"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name"
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(query).fetchall()]


def get_broker(db_path: str, broker_id: int) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM agent_brokers WHERE id = ?", (broker_id,)).fetchone()
    return dict(row) if row else None


def get_broker_by_name(db_path: str, name: str) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM agent_brokers WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def update_broker_difficulty(db_path: str, broker_id: int, difficulty: str) -> None:
    value = coerce_difficulty(difficulty).value
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE agent_brokers SET difficulty = ?, last_updated = ? WHERE id = ?",
            (value, _now(), broker_id),
        )
    log_activity(db_path, "update_broker_difficulty", "broker", broker_id, value)


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------

def create_scan(db_path: str, profile_id: int, broker_id: int,
                scan_type: str = "initial") -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO scans (profile_id, broker_id, scan_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (profile_id, broker_id, scan_type, ScanStatus.PENDING.value, _now()),
        )
        return cur.lastrowid


_SCAN_COLUMNS = {
    "status", "found", "search_url_used", "search_params", "result_url",
    "error_message", "started_at", "completed_at",
}


def update_scan(db_path: str, scan_id: int, **kwargs) -> None:
    """Update named scan columns. Unknown column names are rejected.

    chino interpolated caller-supplied keys straight into the SQL. Checking
    them against a known set keeps that from being a way to write arbitrary
    SQL through a keyword argument.
    """
    unknown = set(kwargs) - _SCAN_COLUMNS
    if unknown:
        raise ValueError(f"Unknown scan column(s): {sorted(unknown)}")
    if not kwargs:
        return
    if isinstance(kwargs.get("status"), ScanStatus):
        kwargs["status"] = kwargs["status"].value
    if "found" in kwargs:
        kwargs["found"] = 1 if kwargs["found"] else 0
    assignments = ", ".join(f"{k} = ?" for k in kwargs)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE scans SET {assignments} WHERE id = ?",
            [*kwargs.values(), scan_id],
        )


def get_scan(db_path: str, scan_id: int) -> Optional[dict]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT s.*, b.name AS broker_name FROM scans s "
            "LEFT JOIN agent_brokers b ON b.id = s.broker_id WHERE s.id = ?",
            (scan_id,),
        ).fetchone()
    return dict(row) if row else None


def get_scans(db_path: str, profile_id: Optional[int] = None,
              status: Optional[str] = None, found_only: bool = False) -> list[dict]:
    query = (
        "SELECT s.*, b.name AS broker_name FROM scans s "
        "LEFT JOIN agent_brokers b ON b.id = s.broker_id WHERE 1=1"
    )
    params: list[Any] = []
    if profile_id is not None:
        query += " AND s.profile_id = ?"
        params.append(profile_id)
    if status:
        query += " AND s.status = ?"
        params.append(status)
    if found_only:
        query += " AND s.found = 1"
    query += " ORDER BY s.id DESC"
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


# ---------------------------------------------------------------------------
# Removals
# ---------------------------------------------------------------------------

def create_removal(db_path: str, scan_id: int, profile_id: int, broker_id: int,
                   opt_out_url: str = "") -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO removals (scan_id, profile_id, broker_id, status, opt_out_url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, profile_id, broker_id, RemovalStatus.PENDING.value, opt_out_url, _now()),
        )
        removal_id = cur.lastrowid
    log_activity(db_path, "create_removal", "removal", removal_id)
    return removal_id


_REMOVAL_COLUMNS = {
    "status", "submission_method", "opt_out_url", "request_id", "submitted_at",
    "confirmed_at", "failed_at", "failure_reason", "retry_count", "max_retries", "notes",
}


def update_removal(db_path: str, removal_id: int, **kwargs) -> None:
    """Update named removal columns. Unknown column names are rejected."""
    unknown = set(kwargs) - _REMOVAL_COLUMNS
    if unknown:
        raise ValueError(f"Unknown removal column(s): {sorted(unknown)}")
    if not kwargs:
        return
    if isinstance(kwargs.get("status"), RemovalStatus):
        kwargs["status"] = kwargs["status"].value
    assignments = ", ".join(f"{k} = ?" for k in kwargs)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE removals SET {assignments} WHERE id = ?",
            [*kwargs.values(), removal_id],
        )
    log_activity(db_path, "update_removal", "removal", removal_id, str(kwargs.get("status", "")))


def get_removal(db_path: str, removal_id: int) -> Optional[dict]:
    """One removal joined to its broker name.

    chino's VerificationEngine called self.db.get_removal() and its own
    comment noted the method did not exist -- verification could never have
    run. This is that missing method.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT r.*, b.name AS broker_name, b.search_url, b.opt_out_url AS broker_opt_out_url "
            "FROM removals r LEFT JOIN agent_brokers b ON b.id = r.broker_id WHERE r.id = ?",
            (removal_id,),
        ).fetchone()
    return dict(row) if row else None


def get_removals(db_path: str, profile_id: Optional[int] = None,
                 status: Optional[str] = None) -> list[dict]:
    query = (
        "SELECT r.*, b.name AS broker_name FROM removals r "
        "LEFT JOIN agent_brokers b ON b.id = r.broker_id WHERE 1=1"
    )
    params: list[Any] = []
    if profile_id is not None:
        query += " AND r.profile_id = ?"
        params.append(profile_id)
    if status:
        query += " AND r.status = ?"
        params.append(status)
    query += " ORDER BY r.id DESC"
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_pending_removals(db_path: str) -> list[dict]:
    return get_removals(db_path, status=RemovalStatus.PENDING.value)


def get_removals_for_retry(db_path: str) -> list[dict]:
    """Failed removals that have not yet burned through max_retries."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT r.*, b.name AS broker_name FROM removals r "
            "LEFT JOIN agent_brokers b ON b.id = r.broker_id "
            "WHERE r.status = ? AND r.retry_count < r.max_retries ORDER BY r.id",
            (RemovalStatus.FAILED.value,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def add_evidence(db_path: str, removal_id: int, evidence_type: str,
                 file_path: str = "", file_hash: str = "", notes: str = "") -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO evidence (removal_id, evidence_type, file_path, file_hash, captured_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (removal_id, evidence_type, file_path, file_hash, _now(), notes),
        )
        evidence_id = cur.lastrowid
    log_activity(db_path, "add_evidence", "evidence", evidence_id, evidence_type)
    return evidence_id


def get_evidence(db_path: str, removal_id: Optional[int] = None) -> list[dict]:
    query = (
        "SELECT e.*, b.name AS broker_name FROM evidence e "
        "LEFT JOIN removals r ON r.id = e.removal_id "
        "LEFT JOIN agent_brokers b ON b.id = r.broker_id"
    )
    params: list[Any] = []
    if removal_id is not None:
        query += " WHERE e.removal_id = ?"
        params.append(removal_id)
    query += " ORDER BY e.id DESC"
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


# ---------------------------------------------------------------------------
# Human queue -- routed to utils/review_queue.py, not a second table
# ---------------------------------------------------------------------------

def queue_for_human(review_db_path: str, target: str, task_type: str, reason: str,
                    note: str = "", current_url: str = "",
                    screenshot_path: str = "") -> int:
    """File a piece of work automation stopped short of.

    chino wrote these to its own manual_queue table. They go to
    review_queue instead -- one queue, one vocabulary. `reason` must be a
    review_queue reason; translate a chino QueueReason with
    broker_agent_models.map_queue_reason() first.
    """
    return review_queue.add_item(
        review_db_path, target=target, task_type=task_type, reason=reason,
        note=note, current_url=current_url, screenshot_path=screenshot_path,
    )


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------

def record_scheduled_task(db_path: str, task_type: str, profile_id: Optional[int] = None,
                          interval_days: Optional[int] = None,
                          next_run: str = "") -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO scheduled_tasks (task_type, profile_id, interval_days, next_run, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_type, profile_id, interval_days, next_run, _now()),
        )
        return cur.lastrowid


def mark_task_run(db_path: str, task_id: int, next_run: str = "") -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET last_run = ?, next_run = ? WHERE id = ?",
            (_now(), next_run, task_id),
        )


def get_scheduled_tasks(db_path: str, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM scheduled_tasks"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY id DESC"
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(query).fetchall()]


def deactivate_task(db_path: str, task_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute("UPDATE scheduled_tasks SET is_active = 0 WHERE id = ?", (task_id,))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def get_dashboard_stats(db_path: str) -> dict:
    """Counts for the agent dashboard."""
    with _connect(db_path) as conn:
        def one(sql: str, params: tuple = ()) -> int:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        return {
            "profiles": one("SELECT COUNT(*) FROM profiles WHERE is_active = 1"),
            "brokers": one("SELECT COUNT(*) FROM agent_brokers WHERE is_active = 1"),
            "scans_total": one("SELECT COUNT(*) FROM scans"),
            "scans_found": one("SELECT COUNT(*) FROM scans WHERE found = 1"),
            "removals_pending": one(
                "SELECT COUNT(*) FROM removals WHERE status = ?", (RemovalStatus.PENDING.value,)),
            "removals_submitted": one(
                "SELECT COUNT(*) FROM removals WHERE status = ?", (RemovalStatus.SUBMITTED.value,)),
            "removals_confirmed": one(
                "SELECT COUNT(*) FROM removals WHERE status = ?", (RemovalStatus.CONFIRMED.value,)),
            "removals_failed": one(
                "SELECT COUNT(*) FROM removals WHERE status = ?", (RemovalStatus.FAILED.value,)),
            "evidence_captured": one("SELECT COUNT(*) FROM evidence"),
        }
