"""Profile state is the single identity contract for downstream views."""

import profile_state
from audit_packager import build_summary


def test_sync_profile_updates_canonical_profile_and_legacy_consumers():
    state = {}
    profile = profile_state.sync_profile(state, {
        "full_name": "Jane Q. Doe",
        "email": "jane@example.com",
        "handle": "janedoe",
        "domain": "janedoe.example",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78701",
        "phone": "555-0100",
    })

    assert state["profile"] == profile
    assert profile["full_name"] == "Jane Q. Doe"
    assert state["user_name"] == "Jane Q. Doe"
    assert state["user_email"] == "jane@example.com"
    assert state["user_location"] == "Austin, TX"
    assert state["footprint_handle"] == "janedoe"
    assert state["footprint_email"] == "jane@example.com"
    assert state["osint_handle"] == "janedoe"
    assert state["osint_domain"] == "janedoe.example"
    assert profile["country"] == "US"  # default when not supplied


def test_sync_profile_can_set_country():
    state = {}
    profile = profile_state.sync_profile(state, {"country": "DE"})
    assert profile["country"] == "DE"


def test_saved_profile_hydrates_canonical_identity():
    profile = profile_state.profile_from_saved({
        "first_name": "Jane",
        "middle_name": "Q.",
        "last_name": "Doe",
        "email_address": "jane@example.com",
        "current_city": "Austin",
        "current_state": "TX",
        "current_zip_code": "78701",
        "phone_number": "555-0100",
    })
    assert profile == {
        "full_name": "Jane Q. Doe",
        "email": "jane@example.com",
        "handle": "",
        "domain": "",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78701",
        "phone": "555-0100",
        "country": "US",
    }


def test_canonical_profile_projects_into_audit_summary():
    state = {}
    profile = profile_state.sync_profile(state, {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "city": "Austin",
        "state": "TX",
    })
    summary = build_summary([], [], {}, target_profile={
        "name": profile["full_name"],
        "location": ", ".join((profile["city"], profile["state"])),
        "email": profile["email"],
    })
    assert summary["relational_entities"] == []
    assert summary["relational_disassociation_clause"] == ""
