"""Google search-result escalation for delisted broker campaigns.

The load-bearing rule is that escalation is gated on DELISTED. Refresh
Outdated Content only actions a URL that is already gone, so offering the
button on a live campaign would send the user to a tool that rejects them.
"""
import config
import remediation
from campaign_manager import (STATUS_CLOCK_ACTIVE, STATUS_DELISTED,
                              STATUS_DISCOVERED, STATUS_NON_COMPLIANT)

PROFILE = {"name": "Jane Roe", "email": "jane@example.com"}


def campaign(**overrides):
    value = {
        "broker_name": "TruePeopleSearch",
        "domain": "truepeoplesearch.com",
        "profile_url": "https://truepeoplesearch.com/find/person/abc123",
        "effective_status": STATUS_DELISTED,
        "date_verified_removed": "2026-08-01",
    }
    value.update(overrides)
    return value


# --- the index probe is the one genuinely prefillable link ----------------

def test_index_probe_builds_a_real_site_query():
    url = remediation.google_index_probe_url("example.com", "Jane Roe")
    assert url.startswith("https://www.google.com/search?q=")
    assert "site%3Aexample.com" in url
    assert "Jane+Roe" in url


def test_index_probe_survives_a_missing_name():
    url = remediation.google_index_probe_url("example.com")
    assert "site%3Aexample.com" in url
    assert url.count("q=") == 1


def test_index_probe_with_nothing_to_search_falls_back_to_plain_search():
    assert remediation.google_index_probe_url("", "") == remediation.GOOGLE_SEARCH_URL


# --- gating ---------------------------------------------------------------

def test_a_delisted_campaign_is_escalatable():
    payload = remediation.plan_google_escalation(campaign(), PROFILE)
    assert payload["eligible"]
    assert payload["blocked_reason"] == ""
    assert len(payload["actions"]) == 2


def test_only_delisted_campaigns_are_escalatable():
    for status in (STATUS_DISCOVERED, STATUS_CLOCK_ACTIVE, STATUS_NON_COMPLIANT):
        payload = remediation.plan_google_escalation(
            campaign(effective_status=status), PROFILE)
        assert not payload["eligible"], status
        assert "not DELISTED" in payload["blocked_reason"]
        assert payload["actions"] == []


def test_a_delisted_campaign_with_no_record_url_is_blocked():
    payload = remediation.plan_google_escalation(campaign(profile_url=""), PROFILE)
    assert not payload["eligible"]
    assert "no record URL" in payload["blocked_reason"]


def test_status_falls_back_to_the_stored_column():
    """get_all_campaigns enriches with effective_status; a raw row may not."""
    raw = campaign()
    raw.pop("effective_status")
    raw["status"] = STATUS_DELISTED
    assert remediation.plan_google_escalation(raw, PROFILE)["eligible"]


# --- action shape ---------------------------------------------------------

def test_refresh_outdated_is_the_primary_action_for_a_dead_url():
    actions = remediation.plan_google_escalation(campaign(), PROFILE)["actions"]
    primary = [a for a in actions if a["primary"]]
    assert len(primary) == 1
    assert primary[0]["kind"] == remediation.ESCALATION_REFRESH
    assert primary[0]["url"] == remediation.GOOGLE_REFRESH_OUTDATED_URL


def test_pii_removal_is_offered_as_the_secondary_route():
    actions = remediation.plan_google_escalation(campaign(), PROFILE)["actions"]
    secondary = [a for a in actions if not a["primary"]]
    assert secondary[0]["kind"] == remediation.ESCALATION_PII
    assert secondary[0]["url"] == config.GOOGLE_PII_REMOVAL_URL


def test_every_action_carries_the_url_to_paste_rather_than_a_fake_prefill():
    """Neither Google form accepts a URL parameter, so we must not invent one."""
    record_url = "https://truepeoplesearch.com/find/person/abc123"
    for action in remediation.plan_google_escalation(campaign(), PROFILE)["actions"]:
        assert action["paste_value"] == record_url
        assert record_url not in action["url"]
        assert "?" not in action["url"]
        assert action["why"] and action["how"] and action["note"]


# --- collection -----------------------------------------------------------

def test_escalations_are_sorted_newest_delisting_first():
    payloads = remediation.plan_google_escalations([
        campaign(broker_name="Older", date_verified_removed="2026-01-01"),
        campaign(broker_name="Newer", date_verified_removed="2026-08-01"),
    ], PROFILE)
    assert [p["broker_name"] for p in payloads] == ["Newer", "Older"]


def test_eligible_only_filters_by_default_and_can_be_turned_off():
    rows = [campaign(), campaign(broker_name="Live", effective_status=STATUS_CLOCK_ACTIVE)]
    assert len(remediation.plan_google_escalations(rows, PROFILE)) == 1
    assert len(remediation.plan_google_escalations(rows, PROFILE, eligible_only=False)) == 2


def test_summary_counts_eligible_and_blocked():
    rows = [campaign(), campaign(broker_name="Live", effective_status=STATUS_CLOCK_ACTIVE)]
    payloads = remediation.plan_google_escalations(rows, PROFILE, eligible_only=False)
    assert remediation.google_escalation_summary(payloads) == {
        "total": 2, "eligible": 1, "blocked": 1}


def test_no_campaigns_yields_no_payloads():
    assert remediation.plan_google_escalations([], PROFILE) == []
    assert remediation.plan_google_escalations(None, PROFILE) == []
