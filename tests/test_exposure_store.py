from datetime import datetime, timedelta

import pytest

import exposure_store


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_exposure.db")


def test_no_checks_returns_empty_dict(db_path):
    assert exposure_store.get_all_checks(db_path) == {}


def test_record_check_stores_value_and_today(db_path):
    exposure_store.record_check(db_path, "email_breach", "Checked — clear")
    checks = exposure_store.get_all_checks(db_path)
    assert checks["email_breach"]["value"] == "Checked — clear"
    assert checks["email_breach"]["checked_at"] == datetime.now().strftime("%Y-%m-%d")


def test_record_check_overwrites_existing_category(db_path):
    exposure_store.record_check(db_path, "broker:Spokeo", "false")
    exposure_store.record_check(db_path, "broker:Spokeo", "true")
    checks = exposure_store.get_all_checks(db_path)
    assert len(checks) == 1
    assert checks["broker:Spokeo"]["value"] == "true"


def test_days_since_checked(db_path):
    ten_days_ago = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    assert exposure_store.days_since_checked(ten_days_ago) == 10


def test_is_stale_true_past_threshold(db_path):
    old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    assert exposure_store.is_stale(old_date, stale_days=30) is True


def test_is_stale_false_within_threshold(db_path):
    recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    assert exposure_store.is_stale(recent_date, stale_days=30) is False


def test_is_stale_boundary_is_inclusive(db_path):
    exactly_threshold = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    assert exposure_store.is_stale(exactly_threshold, stale_days=30) is True
