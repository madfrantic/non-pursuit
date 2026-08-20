import json
import sqlite3

import pytest

import database


@pytest.fixture
def db_path(tmp_path):
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

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM target_profile WHERE id = ?", (row_id,)).fetchone()
    conn.close()

    assert row["first_name"] == "Jane"
    assert row["last_name"] == "Doe"
    assert row["middle_name"] == "Q"
    assert row["birth_year"] == 1990
    assert row["email_address"] == "jane@example.com"
    assert row["phone_number"] == "555-123-4567"
    assert row["current_city"] == "Austin"
    assert row["current_state"] == "TX"
    assert row["current_zip_code"] == "78701"
    assert row["historical_zip_codes"] == "94105, 10001"
    assert json.loads(row["relational_entities"])[0]["name"] == "Alex Doe"

    profile = database.get_latest_target_profile(db_path)
    assert profile["relational_entities"][0]["shared_phone_numbers"] == ["555-1111"]


def test_insert_target_profile_allows_missing_optional_fields(db_path):
    database.init_db(db_path)
    row_id = database.insert_target_profile(db_path, {"first_name": "Jane", "last_name": "Doe"})

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM target_profile WHERE id = ?", (row_id,)).fetchone()
    conn.close()

    assert row["first_name"] == "Jane"
    assert row["last_name"] == "Doe"
    assert row["birth_year"] is None
    assert row["email_address"] is None


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
    row = conn.execute("SELECT first_name FROM target_profile").fetchone()
    conn.close()

    assert "target_profile" in tables
    assert row[0] == malicious


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
