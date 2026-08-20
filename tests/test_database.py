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
