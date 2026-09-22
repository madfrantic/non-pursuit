import json
import sqlite3
from unittest.mock import patch

import pytest

import database
from cryptography.fernet import Fernet


@pytest.fixture
def encryption_key():
    """Provide a fixed test encryption key."""
    return Fernet.generate_key()


@pytest.fixture
def db_path(tmp_path, encryption_key, monkeypatch):
    # Mock environment and cipher so tests don't create .env files
    monkeypatch.setenv("ENCRYPTION_KEY", encryption_key.decode())
    monkeypatch.setattr("database._CIPHER", None)
    return str(tmp_path / "test_profile.db")


def test_init_db_creates_target_profile_table(db_path):
    database.init_db(db_path)
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "target_profile" in tables


def test_insert_target_profile_stores_all_fields(db_path):
    database.init_db(db_path)
    row_id = database.insert_target_profile(
        db_path,
        {
            "first_name": "Jane",
            "last_name": "Doe",
            "middle_name": "Q",
            "birth_year": 1990,
            "email_address": "jane@example.com",
            "phone_number": "555-123-4567",
            "current_city": "Austin",
            "current_state": "TX",
            "current_zip_code": "78701",
            "historical_zip_codes": "94105, 10001",
            "relational_entities": [{
                "name": "Alex Doe",
                "shared_historical_addresses": ["1 Main St, Austin, TX 78701"],
                "shared_phone_numbers": ["555-1111"],
            }],
        },
    )
    assert row_id == 1

    # PII is encrypted at rest, so raw SQLite reads see ciphertext.
    # Verify through the high-level API which decrypts on return.
    profile = database.get_latest_target_profile(db_path)
    assert profile["first_name"] == "Jane"
    assert profile["last_name"] == "Doe"
    assert profile["middle_name"] == "Q"
    assert profile["birth_year"] == 1990
    assert profile["email_address"] == "jane@example.com"
    assert profile["phone_number"] == "555-123-4567"
    assert profile["current_city"] == "Austin"
    assert profile["current_state"] == "TX"
    assert profile["current_zip_code"] == "78701"
    assert profile["historical_zip_codes"] == "94105, 10001"
    assert profile["relational_entities"][0]["shared_phone_numbers"] == ["555-1111"]


def test_insert_target_profile_allows_missing_optional_fields(db_path):
    database.init_db(db_path)
    row_id = database.insert_target_profile(db_path, {"first_name": "Jane", "last_name": "Doe"})

    profile = database.get_latest_target_profile(db_path)
    assert profile["first_name"] == "Jane"
    assert profile["last_name"] == "Doe"
    assert profile["birth_year"] is None
    assert profile["email_address"] is None


def test_get_latest_target_profile_returns_none_when_empty(db_path):
    database.init_db(db_path)
    assert database.get_latest_target_profile(db_path) is None


def test_get_latest_target_profile_returns_most_recent(db_path):
    database.init_db(db_path)
    database.insert_target_profile(db_path, {"first_name": "Jane", "last_name": "Doe"})
    database.insert_target_profile(db_path, {"first_name": "John", "last_name": "Smith"})

    latest = database.get_latest_target_profile(db_path)

    assert latest["first_name"] == "John"
    assert latest["last_name"] == "Smith"


def test_insert_target_profile_is_parameterized_against_injection(db_path):
    database.init_db(db_path)
    malicious = "Robert'); DROP TABLE target_profile; --"
    database.insert_target_profile(db_path, {"first_name": malicious, "last_name": "Doe"})

    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    profile = database.get_latest_target_profile(db_path)
    assert "target_profile" in tables
    assert profile["first_name"] == malicious


def test_parse_historical_zips_handles_comma_separated():
    assert database.parse_historical_zips("94105, 10001") == ["94105", "10001"]


def test_parse_historical_zips_handles_newline_separated():
    assert database.parse_historical_zips("94105\n10001") == ["94105", "10001"]


def test_parse_historical_zips_dedupes_preserving_order():
    assert database.parse_historical_zips("94105, 10001, 94105") == ["94105", "10001"]


def test_parse_historical_zips_strips_whitespace_and_blanks():
    assert database.parse_historical_zips(" 94105 ,, 10001 \n\n") == ["94105", "10001"]


def test_parse_historical_zips_returns_empty_list_for_none_or_blank():
    assert database.parse_historical_zips(None) == []
    assert database.parse_historical_zips("") == []
    assert database.parse_historical_zips("   ") == []


def test_database_cli_rotation_and_export(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    env_path = tmp_path / ".env"
    
    import subprocess
    import sys
    import os
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_root

    result = subprocess.run(
        [sys.executable, "-m", "utils.database", "--rotate-key", "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="],
        capture_output=True, text=True, cwd=str(tmp_path), env=env
    )
    assert result.returncode == 0
    assert "ENCRYPTION_KEY=YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=" in env_path.read_text()


# ---------------------------------------------------------------------------
# Journal mode fallback
# ---------------------------------------------------------------------------
# WAL has to create <db>-wal and <db>-shm beside the database and mmap the
# -shm segment. NFS/SMB mounts and some container volume drivers refuse
# that, and SQLite raises OperationalError straight out of the PRAGMA --
# which crashed the app at import, because init_db() runs before anything
# renders. These pin the degraded path.


class _WalRefusingConnection:
    """Stands in for a connection on a filesystem that has no WAL support.

    sqlite3.Connection is an immutable C type, so the refusal can't be
    monkeypatched onto a real one.
    """

    def __init__(self):
        self.statements = []

    def execute(self, sql, *args):
        self.statements.append(sql)
        if "journal_mode=WAL" in sql:
            raise sqlite3.OperationalError("disk I/O error")
        return self


def test_apply_journal_mode_uses_wal_when_available(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    try:
        assert database.apply_journal_mode(conn, str(tmp_path / "t.db")) == "wal"
        assert conn.execute("PRAGMA journal_mode;").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_apply_journal_mode_falls_back_to_delete(tmp_path):
    conn = _WalRefusingConnection()
    assert database.apply_journal_mode(conn, str(tmp_path / "nfs.db")) == "delete"
    assert conn.statements == ["PRAGMA journal_mode=WAL;", "PRAGMA journal_mode=DELETE;"]


def test_journal_fallback_warns_once_per_path(tmp_path, caplog):
    """_connect opens a connection per call, so an unguarded warning would
    fire on every single read for the life of the process."""
    path = str(tmp_path / "nfs.db")
    database._JOURNAL_FALLBACK_WARNED.discard(path)

    with caplog.at_level("WARNING", logger="database"):
        database.apply_journal_mode(_WalRefusingConnection(), path)
        database.apply_journal_mode(_WalRefusingConnection(), path)

    warnings = [r for r in caplog.records if "WAL journalling unavailable" in r.getMessage()]
    assert len(warnings) == 1


def test_init_db_creates_a_missing_parent_directory(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "tracker.db"
    database.init_db(str(nested))
    assert nested.exists()


def test_init_db_accepts_a_bare_relative_filename(tmp_path, monkeypatch):
    """os.path.dirname("t.db") is "", and os.makedirs("") raises
    FileNotFoundError -- so the parent-directory guard has to be the
    Path(...).parent form, which no-ops instead."""
    monkeypatch.chdir(tmp_path)
    database.init_db("bare.db")
    assert (tmp_path / "bare.db").exists()
