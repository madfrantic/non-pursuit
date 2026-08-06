"""
Dashboard: enter your info once, then head to Results to see what
searching for yourself actually found. Also shows your tracked campaign
progress. Kept separate from Results on purpose — stacking the input
form, the full broker-by-broker results, and campaign metrics on one long
page made it too easy to miss that results were there at all.
"""
import streamlit as st

import config
import exposure_store
from tracker import get_all_requests

_MODE_RESULTS = ":material/travel_explore: Results"
_MODE_LETTERS = ":material/mail: 1. Data Broker Deletion Letters"
_MODE_TRACKER = ":material/monitoring: 4. Campaign Tracker"


def _switch_to(mode_value):
    # Can't set nav_mode directly here -- the sidebar radio (key="nav_mode")
    # has already rendered earlier in this same run. app.py applies this
    # before the radio is created on the next run.
    st.session_state.pending_nav = mode_value
    st.rerun()


def render():
    st.header(":material/dashboard: Dashboard")
    st.markdown("Enter your info once — it's reused everywhere else in the app (results, letters, tracker).")
    st.markdown("---")

    st.subheader("Your info")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        name = st.text_input("Full Name", value=st.session_state.user_name, placeholder="Enter your full legal name")
    with col2:
        location = st.text_input("Location", value=st.session_state.user_location, placeholder="City, State")
    with col3:
        email = st.text_input("Email", value=st.session_state.user_email, placeholder="your.email@example.com")
    with col4:
        phone = st.text_input("Phone (optional)", value=st.session_state.user_phone, placeholder="555-123-4567")
    st.session_state.user_name = name
    st.session_state.user_location = location
    st.session_state.user_email = email
    st.session_state.user_phone = phone

    if name:
        if st.button(":material/travel_explore: See my results", type="primary", width="stretch"):
            _switch_to(_MODE_RESULTS)
    else:
        st.info("Enter your name above, then click through to see your results.")

    stale_count = sum(
        1 for entry in exposure_store.get_all_checks(config.EXPOSURE_DB_PATH).values()
        if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS)
    )
    if stale_count == 1:
        st.warning(f":material/schedule: 1 item on your Results page hasn't been rechecked in {config.RECHECK_STALE_DAYS}+ days.")
    elif stale_count > 1:
        st.warning(f":material/schedule: {stale_count} items on your Results page haven't been rechecked in {config.RECHECK_STALE_DAYS}+ days.")

    st.markdown("---")
    st.subheader("Your campaign so far")

    requests = get_all_requests(config.TRACKER_DB_PATH)
    total = len(requests)
    overdue = sum(1 for r in requests if r["is_overdue"])
    complete = sum(1 for r in requests if r["status"] == "Complete")
    active = total - complete

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Overdue", overdue)
    col2.metric("Active", active)
    col3.metric("Complete", complete)
    col4.metric("Total tracked", total)

    if overdue:
        st.error(
            f"🚨 {overdue} request(s) are past their statutory deadline — "
            "check the Campaign Tracker to follow up."
        )

    active_requests = [r for r in requests if r["status"] != "Complete"]
    if not active_requests:
        st.info("Nothing active yet — check your results above and generate a letter to start your first request.")
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

    st.markdown("---")
    st.subheader("Quick actions")
    qcol1, qcol2 = st.columns(2)
    if qcol1.button(":material/mail: Generate a letter", width="stretch"):
        _switch_to(_MODE_LETTERS)
    if qcol2.button(":material/monitoring: Open full tracker", width="stretch"):
        _switch_to(_MODE_TRACKER)
