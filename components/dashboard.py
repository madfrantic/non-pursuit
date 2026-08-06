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

_MODE_RESULTS = "🔍 Results"
_MODE_LETTERS = "✉️ 1. Data Broker Deletion Letters"
_MODE_TRACKER = "📈 4. Campaign Tracker"


def _switch_to(mode_value):
    # Can't set nav_mode directly here -- the sidebar radio (key="nav_mode")
    # has already rendered earlier in this same run. app.py applies this
    # before the radio is created on the next run.
    st.session_state.pending_nav = mode_value
    st.rerun()


def render():
    st.markdown(
        """
        <div class="np-hero">
            <div class="np-card-label">Workflow</div>
            <h3 style="margin: 0 0 0.4rem 0;">Take control of your digital footprint</h3>
            <p class="np-quiet" style="margin: 0;">
                Start with your profile, confirm what appears in search results, and then use the built-in tools to act on what you find.
            </p>
            <div style="margin-top: 0.7rem;">
                <span class="np-step-pill">1. Profile</span>
                <span class="np-step-pill">2. Review</span>
                <span class="np-step-pill">3. Act</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander(":material/emoji_flags: California resident? Check DROP first"):
        st.markdown(
            f"""
            <div class="np-info-box" style="font-size: 0.85em;">
            The state's own deletion tool, <strong>DROP</strong>, reaches every
            <em>registered</em> data broker with one request, and brokers have been
            required to process DROP requests since Aug 1, 2026. Start there — use
            Non-Pursuit for brokers that aren't registered, for other states, or to
            escalate if a broker misses its window.
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.link_button("Open DROP (privacy.ca.gov)", config.CA_DROP_URL)

    with st.container(border=True):
        info_header_col, info_demo_col = st.columns([4, 1.3])
        with info_header_col:
            st.subheader(":material/person: Your info")
            st.caption("Enter your details once so the results, letters, and tracker stay aligned.")
        with info_demo_col:
            st.write("")
            if st.button("🧪 Try a demo profile", width="stretch"):
                st.session_state.user_name = config.DEMO_PROFILE["name"]
                st.session_state.user_email = config.DEMO_PROFILE["email"]
                st.session_state.user_location = config.DEMO_PROFILE["location"]
                st.session_state.record_url = config.DEMO_PROFILE["record_url"]
                st.toast("Demo profile loaded!")

    workflow_cols = st.columns(3)
    with workflow_cols[0]:
        with st.container(border=True):
            st.markdown("### 1. Profile")
            st.caption("Add your name, location, and contact details so the app can tailor the search flow.")
    with workflow_cols[1]:
        with st.container(border=True):
            st.markdown("### 2. Review")
            st.caption("Check what appears in search results and confirm any broker listings you find.")
    with workflow_cols[2]:
        with st.container(border=True):
            st.markdown("### 3. Act")
            st.caption("Move from evidence to letters, tracker follow-up, and other removal steps.")
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
        st.caption("Enter your name above, then click through to see your results.")

    stale_count = sum(
        1 for entry in exposure_store.get_all_checks(config.EXPOSURE_DB_PATH).values()
        if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS)
    )
    if stale_count == 1:
        st.warning(f":material/schedule: 1 item on your Results page hasn't been rechecked in {config.RECHECK_STALE_DAYS}+ days.")
    elif stale_count > 1:
        st.warning(f":material/schedule: {stale_count} items on your Results page haven't been rechecked in {config.RECHECK_STALE_DAYS}+ days.")

    st.markdown("---")
    with st.container(border=True):
        st.subheader(":material/monitoring: Your campaign so far")
        st.caption("A simple snapshot of what is still active, overdue, or already complete.")

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
    with st.container(border=True):
        st.subheader(":material/bolt: Quick actions")
        st.caption("Jump straight to the next task once you have enough evidence to act.")
    qcol1, qcol2 = st.columns(2)
    if qcol1.button(":material/mail: Generate a letter", width="stretch"):
        _switch_to(_MODE_LETTERS)
    if qcol2.button(":material/monitoring: Open full tracker", width="stretch"):
        _switch_to(_MODE_TRACKER)
