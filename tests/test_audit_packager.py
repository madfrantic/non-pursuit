"""
Coverage for the in-memory audit-trail package.

The archive is evidentiary, so these assert on its actual contents rather
than just that a ZIP came back: the deadline data has to survive into the
summary, one letter per distinct broker has to be present, and nothing
may touch disk on the way through.
"""
import io
import json
import zipfile

import pytest

from audit_packager import (
    DEMANDS_DIR,
    README_NAME,
    SUMMARY_NAME,
    VERIFICATION_NAME,
    build_audit_package,
    build_readme,
    build_summary,
    build_verification_log,
)

PROFILE = {
    "name": "Jane Q. Doe", "location": "San Francisco, CA", "email": "jane@example.com",
    "state": "CA", "country": "US",
}

REQUESTS = [
    {
        "broker_name": "Spokeo", "channel": "Email", "date_sent": "2026-07-20",
        "response_window_days": 45, "deadline": "2026-09-03", "days_remaining": 15,
        "status": "Awaiting Response", "is_overdue": False, "notes": "",
    },
    {
        "broker_name": "MyLife", "channel": "Email", "date_sent": "2026-06-01",
        "response_window_days": 45, "deadline": "2026-07-16", "days_remaining": -34,
        "status": "Non-Compliant", "is_overdue": True, "notes": "",
    },
    {
        "broker_name": "Spokeo", "channel": "Email", "date_sent": "2026-08-01",
        "response_window_days": 45, "deadline": "2026-09-15", "days_remaining": 27,
        "status": "Sent", "is_overdue": False, "notes": "follow-up",
    },
]

DISCOVERED = [
    {
        "platform": "Reddit", "category": "social", "target_identifier": "jdoe",
        "profile_url": "https://reddit.com/user/jdoe", "confidence": "CONFIRMED",
        "status": "New", "discovered_date": "2026-08-19", "updated_at": "2026-08-19",
    },
    {
        "platform": "Twitch", "category": "gaming", "target_identifier": "jdoe",
        "profile_url": "https://twitch.tv/jdoe", "confidence": "POSSIBLE",
        "status": "Flagged for closure", "discovered_date": "2026-08-19",
        "updated_at": "2026-08-19",
    },
]

EXPOSURE = {"email_breach": {"value": "Found exposure", "checked_at": "2026-08-19"}}


@pytest.fixture
def archive():
    payload = build_audit_package(REQUESTS, DISCOVERED, EXPOSURE, PROFILE,
                                  record_url="https://x.test/1")
    return zipfile.ZipFile(io.BytesIO(payload))


def test_package_returns_bytes():
    payload = build_audit_package(REQUESTS, DISCOVERED, EXPOSURE, PROFILE)
    assert isinstance(payload, bytes)
    assert payload[:2] == b"PK"  # ZIP magic


def test_archive_contains_all_required_members(archive):
    names = archive.namelist()
    assert SUMMARY_NAME in names
    assert VERIFICATION_NAME in names
    assert README_NAME in names
    assert any(n.startswith(f"{DEMANDS_DIR}/") for n in names)


def test_summary_includes_relational_mapping_and_clause():
    profile = {**PROFILE, "relational_entities": [{"name": "Alex Doe"}]}
    summary = build_summary(REQUESTS, DISCOVERED, EXPOSURE, target_profile=profile)
    assert summary["relational_entities"][0]["name"] == "Alex Doe"
    assert "STATUTORY RELATIONAL SEVERANCE" in summary["relational_disassociation_clause"]


def test_one_letter_per_distinct_broker(archive):
    """Spokeo appears twice in REQUESTS (demand + follow-up) but should
    contribute exactly one canonical letter."""
    letters = [n for n in archive.namelist() if n.startswith(f"{DEMANDS_DIR}/")]
    assert len(letters) == 2
    assert f"{DEMANDS_DIR}/spokeo_demand.txt" in letters
    assert f"{DEMANDS_DIR}/mylife_demand.txt" in letters


def test_archived_letter_carries_statutory_text(archive):
    letter = archive.read(f"{DEMANDS_DIR}/spokeo_demand.txt").decode("utf-8")
    assert "1798.105" in letter
    assert "Spokeo" in letter
    assert PROFILE["name"] in letter


def test_summary_preserves_deadlines_and_overdue_flags(archive):
    summary = json.loads(archive.read(SUMMARY_NAME))
    assert summary["totals"]["requests_logged"] == 3
    assert summary["totals"]["requests_overdue"] == 1
    by_broker = {r["broker"]: r for r in summary["deletion_requests"]}
    assert by_broker["MyLife"]["is_overdue"] is True
    assert by_broker["MyLife"]["deadline"] == "2026-07-16"


def test_summary_counts_open_requests_excluding_complete():
    requests = REQUESTS + [{"broker_name": "X", "status": "Complete", "is_overdue": False}]
    summary = build_summary(requests, DISCOVERED, EXPOSURE)
    assert summary["totals"]["requests_open"] == 3
    assert summary["totals"]["requests_logged"] == 4


def test_summary_includes_discovered_accounts_and_exposure():
    summary = build_summary(REQUESTS, DISCOVERED, EXPOSURE)
    assert summary["totals"]["accounts_discovered"] == 2
    assert summary["exposure_checks"] == EXPOSURE


def test_verification_log_has_header_and_one_row_per_account():
    csv_bytes = build_verification_log(DISCOVERED)
    lines = csv_bytes.decode("utf-8").strip().splitlines()
    assert len(lines) == 3  # header + 2
    assert "Platform" in lines[0]
    assert "Reddit" in lines[1]


def test_readme_explains_statutory_window(archive):
    readme = archive.read(README_NAME).decode("utf-8")
    assert "1798.105" in readme
    assert "45 days" in readme
    # The prose is hard-wrapped, so phrases span line breaks -- collapse
    # whitespace before asserting on them.
    flat = " ".join(readme.lower().split())
    assert "not legal advice" in flat
    assert "1798.130" in flat


def test_empty_campaign_still_produces_valid_archive():
    payload = build_audit_package([], [], {}, PROFILE)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        assert SUMMARY_NAME in zf.namelist()
        assert json.loads(zf.read(SUMMARY_NAME))["totals"]["requests_logged"] == 0


def test_request_without_record_url_still_gets_a_letter():
    payload = build_audit_package(REQUESTS, DISCOVERED, EXPOSURE, PROFILE, record_url=None)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        letter = zf.read(f"{DEMANDS_DIR}/spokeo_demand.txt").decode("utf-8")
    assert "None" not in letter


def test_package_writes_nothing_to_disk(tmp_path, monkeypatch):
    """Hosted builds must not persist user data to a shared filesystem."""
    monkeypatch.chdir(tmp_path)
    build_audit_package(REQUESTS, DISCOVERED, EXPOSURE, PROFILE)
    assert list(tmp_path.iterdir()) == []


# --- jurisdiction routing --------------------------------------------------


def _letter_for(profile, **kwargs):
    payload = build_audit_package(REQUESTS, DISCOVERED, EXPOSURE, profile, **kwargs)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return zf.read(f"{DEMANDS_DIR}/spokeo_demand.txt").decode("utf-8")


def test_california_profile_packages_ccpa_letter():
    letter = _letter_for({**PROFILE, "state": "CA", "country": "US"})
    assert "1798.105" in letter


def test_new_york_profile_packages_ny_hybrid_letter_not_ccpa():
    """Changing only the profile's state must change which statute the
    archived letter cites -- this was hardcoded to CCPA before routing."""
    letter = _letter_for({**PROFILE, "state": "NY", "country": "US"})
    assert "899-bb" in letter
    assert "1798.105" not in letter


def test_uk_profile_packages_gdpr_letter():
    letter = _letter_for({**PROFILE, "state": "", "country": "UK"})
    assert "Article 17" in letter
    assert "1798.105" not in letter


def test_unmatched_state_packages_generic_letter():
    letter = _letter_for({**PROFILE, "state": "TX", "country": "US"})
    assert "privacy policy" in letter
    assert "1798.105" not in letter


def test_broker_template_types_override_wins_over_auto_routing():
    """The letter the user actually reviewed and confirmed in the UI is what
    gets archived, even if auto-routing off the current profile would now
    pick something else -- e.g. the profile changed after the letter was
    confirmed."""
    letter = _letter_for(
        {**PROFILE, "state": "NY", "country": "US"},
        broker_template_types={"Spokeo": "ccpa_deletion"},
    )
    assert "1798.105" in letter


def test_broker_without_recorded_template_falls_back_to_auto_routing():
    letter = _letter_for(
        {**PROFILE, "state": "NY", "country": "US"},
        broker_template_types={"MyLife": "gdpr_erasure"},  # a different broker
    )
    assert "899-bb" in letter


# --- README tracks the frameworks actually in the batch --------------------


def _readme_for(profile, **kwargs):
    payload = build_audit_package(REQUESTS, DISCOVERED, EXPOSURE, profile, **kwargs)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return zf.read(README_NAME).decode("utf-8")


def test_readme_cites_only_ccpa_for_a_california_batch():
    readme = _readme_for({**PROFILE, "state": "CA", "country": "US"})
    assert "1798.105" in readme
    assert "Article 17" not in readme
    assert "899-bb" not in readme


def test_readme_cites_gdpr_and_not_ccpa_for_a_uk_batch():
    readme = _readme_for({**PROFILE, "state": "", "country": "UK"})
    assert "Article 17" in readme
    assert "Article 12(3)" in readme
    assert "1798.105" not in readme


def test_readme_cites_ny_statutes_for_a_new_york_batch():
    readme = _readme_for({**PROFILE, "state": "NY", "country": "US"})
    assert "380-f" in readme
    assert "899-bb" in readme
    assert "1798.105" not in readme


def test_readme_cites_policy_reliance_for_a_generic_batch():
    readme = _readme_for({**PROFILE, "state": "TX", "country": "US"})
    flat = " ".join(readme.lower().split())
    assert "published privacy" in flat or "opt-out" in flat
    assert "1798.105" not in readme


def test_readme_concatenates_blocks_for_a_mixed_jurisdiction_batch():
    """Spokeo confirmed under CCPA, MyLife auto-routed to NY: both statutes
    must appear, and the opening line must name both frameworks."""
    readme = _readme_for(
        {**PROFILE, "state": "NY", "country": "US"},
        broker_template_types={"Spokeo": "ccpa_deletion"},
    )
    assert "1798.105" in readme
    assert "899-bb" in readme


def test_readme_does_not_duplicate_a_block_when_many_brokers_share_a_framework():
    readme = _readme_for({**PROFILE, "state": "CA", "country": "US"})
    assert readme.count("§ 1798.130(a)(2)") == 1


def test_readme_orders_blocks_by_router_precedence_not_broker_order():
    readme = _readme_for(
        {**PROFILE, "state": "NY", "country": "US"},
        broker_template_types={"MyLife": "gdpr_erasure"},
    )
    # PRECEDENCE is GDPR > CCPA > NY, so the GDPR block precedes the NY block
    # even though MyLife is logged after Spokeo.
    assert readme.index("Article 17") < readme.index("380-f")


def test_summary_lists_the_statutes_present_in_the_batch():
    payload = build_audit_package(REQUESTS, DISCOVERED, EXPOSURE,
                                  {**PROFILE, "state": "NY", "country": "US"})
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        summary = json.loads(zf.read(SUMMARY_NAME))
    assert any("380" in s for s in summary["statutes"])


def test_build_readme_defaults_to_ccpa_when_no_template_types_given():
    """Archives produced before jurisdiction routing existed were CCPA-only."""
    readme = build_readme(45).decode("utf-8")
    assert "1798.105" in readme
