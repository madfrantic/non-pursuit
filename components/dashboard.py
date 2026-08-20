"""
Dashboard: enter your info once, then head to Results to see what
searching for yourself actually found. Also shows your tracked campaign
progress. Kept separate from Results on purpose -- stacking the input
form, the full broker-by-broker results, and campaign metrics on one long
page made it too easy to miss that results were there at all.

There used to be two overlapping ways to enter your name/email/location
here -- a set of "quick" fields that saved instantly to session only, and
a fuller form below it that saved to SQLite. They could disagree with
each other, and either way you were typing the same details twice. This
is now the one form; everything else in the app reads from it.
"""
import streamlit as st

import config
import runtime_mode
import database
import exposure_store
from tracker import get_all_requests
from validators import is_valid_email

_MODE_RESULTS = "🔍 Results"
_MODE_LETTERS = "✉️ Data Broker Deletion Letters"
_MODE_TRACKER = "📈 Campaign Tracker"

# Widget key -> target_profile column, for the fields backed by SQLite.
_PROFILE_FIELDS = {
    "pf_first_name": "first_name",
    "pf_middle_name": "middle_name",
    "pf_last_name": "last_name",
    "pf_email": "email_address",
    "pf_phone": "phone_number",
    "pf_city": "current_city",
    "pf_state": "current_state",
    "pf_zip": "current_zip_code",
    "pf_historical_zips": "historical_zip_codes",
}


def _switch_to(mode_value):
    # Can't set nav_mode directly here -- the sidebar radio (key="nav_mode")
    # has already rendered earlier in this same run. app.py applies this
    # before the radio is created on the next run.
    st.session_state.pending_nav = mode_value
    st.rerun()


def _seed_profile_fields():
    """Populate the form's widget keys from the last saved profile, once
    per session -- so reopening the Dashboard shows what's on file instead
    of a blank form that looks like nothing was ever saved."""
    if st.session_state.get("_profile_fields_seeded"):
        return
    st.session_state._profile_fields_seeded = True
    profile = database.get_latest_target_profile(runtime_mode.db_path()) or {}
    for key, column in _PROFILE_FIELDS.items():
        st.session_state.setdefault(key, profile.get(column) or "")
    st.session_state.setdefault("pf_birth_year", profile.get("birth_year"))


def _load_demo_profile():
    city, _, state = config.DEMO_PROFILE["location"].partition(", ")
    st.session_state.pf_first_name, _, st.session_state.pf_last_name = config.DEMO_PROFILE["name"].partition(" ")
    st.session_state.pf_middle_name = ""
    st.session_state.pf_email = config.DEMO_PROFILE["email"]
    st.session_state.pf_phone = ""
    st.session_state.pf_city = city
    st.session_state.pf_state = state
    st.session_state.pf_zip = ""
    st.session_state.pf_birth_year = None
    st.session_state.pf_historical_zips = ""
    st.session_state.record_url = config.DEMO_PROFILE["record_url"]


def _save_profile():
    database.insert_target_profile(
        runtime_mode.db_path(),
        {
            "first_name": st.session_state.pf_first_name,
            "last_name": st.session_state.pf_last_name,
            "middle_name": st.session_state.pf_middle_name,
            "birth_year": int(st.session_state.pf_birth_year) if st.session_state.pf_birth_year else None,
            "email_address": st.session_state.pf_email,
            "phone_number": st.session_state.pf_phone,
            "current_city": st.session_state.pf_city,
            "current_state": st.session_state.pf_state,
            "current_zip_code": st.session_state.pf_zip,
            "historical_zip_codes": st.session_state.pf_historical_zips,
        },
    )
    # The rest of the app (Results/Letters/Tracker) reads these plain
    # session keys rather than re-deriving them from the DB profile.
    st.session_state.user_name = " ".join(
        p for p in [st.session_state.pf_first_name, st.session_state.pf_middle_name, st.session_state.pf_last_name] if p
    )
    st.session_state.user_email = st.session_state.pf_email
    st.session_state.user_phone = st.session_state.pf_phone
    st.session_state.user_location = ", ".join(p for p in [st.session_state.pf_city, st.session_state.pf_state] if p)


def render():
    _seed_profile_fields()

    with st.container(border=True):
        with st.form("profile_form"):
            name_cols = st.columns(2)
            name_cols[0].text_input("👤 First", key="pf_first_name", placeholder="Jane")
            name_cols[1].text_input("👤 Last", key="pf_last_name", placeholder="Doe")

            contact_cols = st.columns(2)
            contact_cols[0].text_input("✉️ Email", key="pf_email", placeholder="you@example.com")
            contact_cols[1].text_input("📱 Phone", key="pf_phone", placeholder="Optional")

            location_cols = st.columns(2)
            location_cols[0].text_input("🏙️ City", key="pf_city", placeholder="Austin")
            location_cols[1].text_input("📍 State", key="pf_state", placeholder="TX")

            with st.expander("🕰️ Previous names & addresses"):
                extra_cols = st.columns(2)
                extra_cols[0].text_input("👤 Middle", key="pf_middle_name", placeholder="Optional")
                extra_cols[1].number_input(
                    "🎂 Born", min_value=1900, max_value=2026, step=1, format="%d", key="pf_birth_year", placeholder="YYYY"
                )
                st.text_input("📮 ZIP", key="pf_zip", placeholder="Optional")
                st.text_area("📮 Prior ZIPs", key="pf_historical_zips", placeholder="94105, 10001", height=80)

            submitted = st.form_submit_button("💾 Save", type="primary", width="stretch")
            if submitted:
                if not st.session_state.pf_first_name or not st.session_state.pf_last_name:
                    st.error("First and last name are required.")
                elif st.session_state.pf_email and not is_valid_email(st.session_state.pf_email):
                    st.error("That email address doesn't look valid.")
                else:
                    _save_profile()
                    st.toast("Profile saved!")
                    st.rerun()

    action_cols = st.columns([2, 1])
    if action_cols[0].button(
        "🌐 See my results", type="primary", width="stretch",
        disabled=not st.session_state.user_name,
    ):
        _switch_to(_MODE_RESULTS)
    if action_cols[1].button("🧪 Try a demo profile", width="stretch"):
        _load_demo_profile()
        st.toast("Demo profile loaded -- click Save to use it.")
        st.rerun()

    stale_count = sum(
        1 for entry in exposure_store.get_all_checks(runtime_mode.db_path()).values()
        if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS)
    )
    if stale_count:
        item_word = "item" if stale_count == 1 else "items"
        st.warning(f"⏰ {stale_count} {item_word} on your Results page haven't been rechecked in {config.RECHECK_STALE_DAYS}+ days.")

    requests = get_all_requests(runtime_mode.db_path())
    total = len(requests)
    overdue = sum(1 for r in requests if r["is_overdue"])
    complete = sum(1 for r in requests if r["status"] == "Complete")
    active = total - complete

    with st.container(border=True):
        st.markdown("##### 📈 Your campaign so far")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Overdue", overdue)
        col2.metric("Active", active)
        col3.metric("Complete", complete)
        col4.metric("Total tracked", total)

        if overdue:
            st.error(f"🚨 {overdue} request(s) are past their statutory deadline -- check the Campaign Tracker to follow up.")

        active_requests = [r for r in requests if r["status"] != "Complete"]
        if not active_requests:
            st.caption("Nothing active yet -- generate a letter below to start your first request.")
        else:
            for r in sorted(active_requests, key=lambda r: r["deadline"]):
                window = max(r["response_window_days"], 1)
                elapsed = window - r["days_remaining"]
                progress = min(max(elapsed / window, 0.0), 1.0)
                label = f"{r['broker_name']} — due {r['deadline']}"
                if r["is_overdue"]:
                    label += f" (overdue by {abs(r['days_remaining'])}d)"
                else:
                    label += f" ({r['days_remaining']}d left)"
                st.progress(progress, text=label)

        action_cols = st.columns(2)
        if action_cols[0].button("✉️ Generate a letter", width="stretch"):
            _switch_to(_MODE_LETTERS)
        if action_cols[1].button("📈 Open full tracker", width="stretch"):
            _switch_to(_MODE_TRACKER)
