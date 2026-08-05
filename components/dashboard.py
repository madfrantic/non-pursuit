"""
Single-pane dashboard: an at-a-glance overview of everything already
tracked, plus quick links into the modes that do the actual work. Reuses
tracker.py's get_all_requests — no new data model, just a different view
over the same data already backing the Campaign Tracker.
"""
import streamlit as st

import config
from tracker import get_all_requests

_MODE_SELF_SEARCH = ":material/person_search: Should I Worry? (Self-Search)"
_MODE_LETTERS = ":material/mail: 1. Data Broker Deletion Letters"
_MODE_TRACKER = ":material/monitoring: 4. Campaign Tracker"
_MODE_WIZARD = ":material/rocket_launch: Guided Wizard"


def _switch_to(mode_value):
    # Can't set nav_mode directly here -- the sidebar radio (key="nav_mode")
    # has already rendered earlier in this same run. app.py applies this
    # before the radio is created on the next run.
    st.session_state.pending_nav = mode_value
    st.rerun()


def render():
    st.header(":material/dashboard: Dashboard")
    st.markdown("Everything you've tracked, at a glance.")
    st.markdown("---")

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

    st.markdown("---")
    st.subheader("Deadlines")

    active_requests = [r for r in requests if r["status"] != "Complete"]
    if not active_requests:
        st.info("Nothing active right now. Use the Guided Wizard below to start your first request.")
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
    qcol1, qcol2, qcol3, qcol4 = st.columns(4)
    if qcol1.button(":material/rocket_launch: Start guided wizard", width="stretch"):
        _switch_to(_MODE_WIZARD)
    if qcol2.button(":material/person_search: Search for myself", width="stretch"):
        _switch_to(_MODE_SELF_SEARCH)
    if qcol3.button(":material/mail: Generate a letter", width="stretch"):
        _switch_to(_MODE_LETTERS)
    if qcol4.button(":material/monitoring: Open full tracker", width="stretch"):
        _switch_to(_MODE_TRACKER)
