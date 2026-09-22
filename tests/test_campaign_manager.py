"""
Campaign lifecycle tests.

Deadline math is a Human Validation Zone in this repo (see docs/SAFETY_BOUNDARIES.md), so
the 45-day window is exercised at its boundaries -- day 0, the deadline
itself, and the day after -- rather than only in the comfortable middle.
"""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

import campaign_manager
import config


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_campaigns.db")


@pytest.fixture
def campaign(db_path):
    """One DISCOVERED campaign, returned as (db_path, campaign_id)."""
    campaign_id = campaign_manager.create_or_update_campaign(
        db_path, "Spokeo", "spokeo.com", "https://spokeo.com/p/123"
    )
    return campaign_id


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime(campaign_manager.DATE_FORMAT)


# --- creation ---------------------------------------------------------


def test_create_campaign_starts_as_discovered(db_path, campaign):
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["broker_name"] == "Spokeo"
    assert record["domain"] == "spokeo.com"
    assert record["status"] == campaign_manager.STATUS_DISCOVERED
    assert record["effective_status"] == campaign_manager.STATUS_DISCOVERED
    assert record["date_discovered"] == date.today().strftime(campaign_manager.DATE_FORMAT)
    assert record["date_dispatched"] is None
    assert record["statutory_deadline"] is None
    assert record["days_remaining"] is None


def test_create_defaults_to_ccpa_statute(db_path, campaign):
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["statute_invoked"] == campaign_manager.DEFAULT_STATUTE


def test_create_is_idempotent_on_broker_and_domain(db_path, campaign):
    again = campaign_manager.create_or_update_campaign(db_path, "Spokeo", "spokeo.com")
    assert again == campaign
    assert len(campaign_manager.get_all_campaigns(db_path)) == 1


def test_rediscovery_does_not_rewind_lifecycle_state(db_path, campaign):
    """Finding the same profile again must not undo a demand already sent."""
    campaign_manager.mark_dispatched(db_path, campaign)
    campaign_manager.create_or_update_campaign(db_path, "Spokeo", "spokeo.com")
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["status"] == campaign_manager.STATUS_DISPATCHED
    assert record["statutory_deadline"] is not None


def test_rediscovery_without_url_does_not_blank_existing_url(db_path, campaign):
    campaign_manager.create_or_update_campaign(db_path, "Spokeo", "spokeo.com")
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["profile_url"] == "https://spokeo.com/p/123"


def test_same_broker_different_domain_is_a_separate_campaign(db_path, campaign):
    other = campaign_manager.create_or_update_campaign(db_path, "Spokeo", "spokeo.co.uk")
    assert other != campaign
    assert len(campaign_manager.get_all_campaigns(db_path)) == 2


def test_create_requires_broker_and_domain(db_path):
    with pytest.raises(ValueError):
        campaign_manager.create_or_update_campaign(db_path, "", "spokeo.com")
    with pytest.raises(ValueError):
        campaign_manager.create_or_update_campaign(db_path, "Spokeo", "")


# --- 45-day statutory deadline ---------------------------------------


def test_deadline_is_dispatch_plus_configured_window(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date="2026-08-10")
    record = campaign_manager.get_campaign(db_path, campaign)
    expected = (date(2026, 8, 10) + timedelta(days=config.CCPA_RESPONSE_WINDOW_DAYS))
    assert record["statutory_deadline"] == expected.strftime(campaign_manager.DATE_FORMAT)


def test_deadline_uses_45_days(db_path, campaign):
    """Pin the statutory number itself, not just 'whatever config says'."""
    assert config.CCPA_RESPONSE_WINDOW_DAYS == 45
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date="2026-08-10")
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["statutory_deadline"] == "2026-09-24"


def test_calculate_deadline_is_pure_and_handles_missing_input():
    assert campaign_manager.calculate_deadline("2026-01-01") == "2026-02-15"
    assert campaign_manager.calculate_deadline(None) is None
    assert campaign_manager.calculate_deadline("not-a-date") is None


def test_calculate_days_remaining_boundaries():
    today = date(2026, 9, 24)
    assert campaign_manager.calculate_days_remaining("2026-09-25", today=today) == 1
    assert campaign_manager.calculate_days_remaining("2026-09-24", today=today) == 0
    assert campaign_manager.calculate_days_remaining("2026-09-23", today=today) == -1
    assert campaign_manager.calculate_days_remaining(None, today=today) is None


def test_mark_dispatched_rejects_malformed_date(db_path, campaign):
    with pytest.raises(ValueError):
        campaign_manager.mark_dispatched(db_path, campaign, dispatch_date="08/10/2026")


def test_dispatched_deadline_is_frozen_against_later_config_change(db_path, campaign, monkeypatch):
    """The deadline a user was shown on day 1 must survive a config edit."""
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date="2026-08-10")
    monkeypatch.setattr(config, "CCPA_RESPONSE_WINDOW_DAYS", 10)
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["statutory_deadline"] == "2026-09-24"


# --- state transitions ------------------------------------------------


def test_mark_dispatched_moves_to_dispatched_on_day_zero(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign)
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["status"] == campaign_manager.STATUS_DISPATCHED
    assert record["effective_status"] == campaign_manager.STATUS_DISPATCHED
    assert record["date_dispatched"] == date.today().strftime(campaign_manager.DATE_FORMAT)


def test_clock_active_once_a_day_has_passed(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(1))
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["effective_status"] == campaign_manager.STATUS_CLOCK_ACTIVE
    assert record["is_overdue"] is False


def test_clock_active_mid_window_reports_days_remaining(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(11))
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["effective_status"] == campaign_manager.STATUS_CLOCK_ACTIVE
    assert record["days_remaining"] == config.CCPA_RESPONSE_WINDOW_DAYS - 11


def test_record_delisting_closes_the_case(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(12))
    campaign_manager.record_delisting(db_path, campaign, evidence="HTTP 404")
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["status"] == campaign_manager.STATUS_DELISTED
    assert record["effective_status"] == campaign_manager.STATUS_DELISTED
    assert record["date_verified_removed"] == date.today().strftime(campaign_manager.DATE_FORMAT)
    assert record["last_recon_check"] == record["date_verified_removed"]
    assert "HTTP 404" in record["evidence_log"]


def test_delisted_after_deadline_is_not_reported_overdue(db_path, campaign):
    """A removal that arrived late is still a removal, not a violation."""
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(90))
    campaign_manager.record_delisting(db_path, campaign)
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["effective_status"] == campaign_manager.STATUS_DELISTED
    assert record["is_overdue"] is False


def test_recon_check_still_listed_stamps_date_without_closing(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(5))
    campaign_manager.record_recon_check(db_path, campaign, still_listed=True, evidence="HTTP 200")
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["status"] == campaign_manager.STATUS_DISPATCHED
    assert record["last_recon_check"] == date.today().strftime(campaign_manager.DATE_FORMAT)
    assert record["date_verified_removed"] is None


def test_recon_check_not_listed_delists(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(5))
    campaign_manager.record_recon_check(db_path, campaign, still_listed=False)
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["effective_status"] == campaign_manager.STATUS_DELISTED


def test_evidence_log_appends_rather_than_overwrites(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(5))
    campaign_manager.record_recon_check(db_path, campaign, still_listed=True, evidence="check one")
    campaign_manager.record_recon_check(db_path, campaign, still_listed=True, evidence="check two")
    log = campaign_manager.get_campaign(db_path, campaign)["evidence_log"]
    assert "check one" in log and "check two" in log


# --- overdue / NON_COMPLIANT derivation ------------------------------


def test_on_the_deadline_is_not_yet_overdue(db_path, campaign):
    """Day 45 of a 45-day window: the broker still has the day."""
    campaign_manager.mark_dispatched(
        db_path, campaign, dispatch_date=_days_ago(config.CCPA_RESPONSE_WINDOW_DAYS)
    )
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["days_remaining"] == 0
    assert record["is_overdue"] is False
    assert record["effective_status"] == campaign_manager.STATUS_CLOCK_ACTIVE


def test_day_after_deadline_is_non_compliant(db_path, campaign):
    campaign_manager.mark_dispatched(
        db_path, campaign, dispatch_date=_days_ago(config.CCPA_RESPONSE_WINDOW_DAYS + 1)
    )
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["days_remaining"] == -1
    assert record["is_overdue"] is True
    assert record["effective_status"] == campaign_manager.STATUS_NON_COMPLIANT


def test_non_compliant_is_derived_without_any_background_job(db_path, campaign):
    """Nothing wrote NON_COMPLIANT -- the calendar alone produced it."""
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(60))
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["status"] == campaign_manager.STATUS_DISPATCHED
    assert record["effective_status"] == campaign_manager.STATUS_NON_COMPLIANT


def test_discovered_campaign_never_reads_as_overdue(db_path, campaign):
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["is_overdue"] is False
    assert record["effective_status"] == campaign_manager.STATUS_DISCOVERED


def test_mark_non_compliant_persists_through_a_redispatch(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(60))
    campaign_manager.mark_non_compliant(db_path, campaign, evidence="AG complaint filed")
    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["status"] == campaign_manager.STATUS_NON_COMPLIANT
    assert "AG complaint filed" in record["evidence_log"]


def test_derive_status_accepts_an_explicit_today():
    record = {
        "status": campaign_manager.STATUS_DISPATCHED,
        "date_dispatched": "2026-08-10",
        "statutory_deadline": "2026-09-24",
    }
    assert campaign_manager.derive_status(record, today=date(2026, 8, 10)) == \
        campaign_manager.STATUS_DISPATCHED
    assert campaign_manager.derive_status(record, today=date(2026, 8, 11)) == \
        campaign_manager.STATUS_CLOCK_ACTIVE
    assert campaign_manager.derive_status(record, today=date(2026, 9, 24)) == \
        campaign_manager.STATUS_CLOCK_ACTIVE
    assert campaign_manager.derive_status(record, today=date(2026, 9, 25)) == \
        campaign_manager.STATUS_NON_COMPLIANT


def test_malformed_stored_date_degrades_instead_of_raising(db_path, campaign):
    """A hand-edited row must not take down the timeline render."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE broker_campaigns SET status = ?, date_dispatched = 'garbage', "
        "statutory_deadline = 'garbage' WHERE id = ?",
        (campaign_manager.STATUS_DISPATCHED, campaign),
    )
    conn.commit()
    conn.close()

    record = campaign_manager.get_campaign(db_path, campaign)
    assert record["days_remaining"] is None
    assert record["is_overdue"] is False
    assert record["effective_status"] == campaign_manager.STATUS_DISPATCHED


# --- queries ----------------------------------------------------------


def test_get_active_campaigns_excludes_delisted(db_path, campaign):
    other = campaign_manager.create_or_update_campaign(db_path, "MyLife", "mylife.com")
    campaign_manager.record_delisting(db_path, other)
    active = campaign_manager.get_active_campaigns(db_path)
    assert [r["id"] for r in active] == [campaign]


def test_get_active_campaigns_keeps_non_compliant_rows(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date=_days_ago(60))
    active = campaign_manager.get_active_campaigns(db_path)
    assert len(active) == 1
    assert active[0]["effective_status"] == campaign_manager.STATUS_NON_COMPLIANT


def test_active_campaigns_sort_most_urgent_first(db_path):
    overdue = campaign_manager.create_or_update_campaign(db_path, "BeenVerified", "beenverified.com")
    campaign_manager.mark_dispatched(db_path, overdue, dispatch_date=_days_ago(60))
    soon = campaign_manager.create_or_update_campaign(db_path, "Spokeo", "spokeo.com")
    campaign_manager.mark_dispatched(db_path, soon, dispatch_date=_days_ago(40))
    undispatched = campaign_manager.create_or_update_campaign(db_path, "Nuwber", "nuwber.com")

    order = [r["id"] for r in campaign_manager.get_active_campaigns(db_path)]
    assert order == [overdue, soon, undispatched]


def test_get_campaign_returns_none_for_unknown_id(db_path):
    assert campaign_manager.get_campaign(db_path, 999) is None


def test_delete_campaign_removes_the_row(db_path, campaign):
    campaign_manager.delete_campaign(db_path, campaign)
    assert campaign_manager.get_all_campaigns(db_path) == []


def test_summarize_counts_by_effective_status(db_path):
    delisted = campaign_manager.create_or_update_campaign(db_path, "TruePeople", "truepeoplesearch.com")
    campaign_manager.record_delisting(db_path, delisted)
    overdue = campaign_manager.create_or_update_campaign(db_path, "BeenVerified", "beenverified.com")
    campaign_manager.mark_dispatched(db_path, overdue, dispatch_date=_days_ago(60))
    campaign_manager.create_or_update_campaign(db_path, "Nuwber", "nuwber.com")

    counts = campaign_manager.summarize(db_path)
    assert counts["TOTAL"] == 3
    assert counts[campaign_manager.STATUS_DELISTED] == 1
    assert counts[campaign_manager.STATUS_NON_COMPLIANT] == 1
    assert counts[campaign_manager.STATUS_DISCOVERED] == 1


# --- persistence / coexistence ---------------------------------------


def test_ledger_coexists_with_the_tracker_requests_table(db_path, campaign):
    """Both tables share one file -- neither may disturb the other."""
    import tracker

    tracker.add_request(db_path, "Spokeo", "email", config.CCPA_RESPONSE_WINDOW_DAYS)
    campaign_manager.create_or_update_campaign(db_path, "MyLife", "mylife.com")

    assert len(tracker.get_all_requests(db_path)) == 1
    assert len(campaign_manager.get_all_campaigns(db_path)) == 2


def test_init_db_is_safe_to_call_repeatedly(db_path, campaign):
    campaign_manager.init_db(db_path)
    campaign_manager.init_db(db_path)
    assert len(campaign_manager.get_all_campaigns(db_path)) == 1


def test_state_survives_reopening_the_database(db_path, campaign):
    campaign_manager.mark_dispatched(db_path, campaign, dispatch_date="2026-08-10")
    reread = campaign_manager.get_campaign(db_path, campaign)
    assert reread["statutory_deadline"] == "2026-09-24"
    assert reread["date_dispatched"] == "2026-08-10"
