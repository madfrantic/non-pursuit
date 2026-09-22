from datetime import datetime, timedelta

from broker_freshness import days_since_verified, is_broker_stale


def _months_ago_str(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m")


def test_days_since_verified_parses_year_month():
    assert days_since_verified("2026-08") is not None


def test_days_since_verified_none_for_empty():
    assert days_since_verified("") is None


def test_days_since_verified_none_for_malformed():
    assert days_since_verified("not-a-date") is None


def test_is_broker_stale_true_past_threshold():
    old = _months_ago_str(200)
    assert is_broker_stale(old, stale_days=180) is True


def test_is_broker_stale_false_within_threshold():
    recent = _months_ago_str(10)
    assert is_broker_stale(recent, stale_days=180) is False


def test_is_broker_stale_true_when_missing():
    assert is_broker_stale("", stale_days=180) is True


def test_is_broker_stale_true_when_malformed():
    assert is_broker_stale("garbage", stale_days=180) is True
