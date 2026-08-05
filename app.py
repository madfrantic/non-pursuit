import sys
import os

import streamlit as st
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), "utils"))
from tracker import add_request, get_all_requests, update_status, delete_request, purge_expired_notes, STATUS_OPTIONS
from calendar_export import build_ics
import config

from components import letters as letters_component
from components import dashboard as dashboard_component
from components import results as results_component


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon=config.APP_ICON,
    layout=config.APP_LAYOUT,
    initial_sidebar_state="expanded",
)

# Theming lives in .streamlit/config.toml. The only custom CSS left here is
# for the "Broker Notes" info box and letter-preview box, which Streamlit's
# theme doesn't reach on its own — kept in sync with config.py's palette so
# changing one color in config.py and matching it in config.toml is the only
# thing needed to re-theme the app.
st.markdown(
    f"""
    <style>
        .np-info-box {{
            background-color: {config.SECONDARY_BACKGROUND_COLOR};
            color: {config.TEXT_COLOR};
            padding: 12px 16px;
            border-left: 4px solid {config.PRIMARY_COLOR};
            border-radius: 4px;
            margin-bottom: 12px;
        }}
        .np-overdue {{
            color: {config.ERROR_COLOR};
            font-weight: bold;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for key, default in {
    "user_name": "",
    "user_email": "",
    "user_location": "",
    "user_phone": "",
    "record_url": "",
    "listed_confirmed": {},
    "exposure_checklist": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
REQUIRED_BROKER_COLUMNS = {"broker_name", "compliance_email", "optout_url", "notes"}


@st.cache_data(ttl=3600)
def load_brokers():
    try:
        df = pd.read_csv(config.BROKERS_CSV_PATH)
    except FileNotFoundError:
        st.error(f"Broker data file not found at {config.BROKERS_CSV_PATH}")
        return pd.DataFrame(columns=list(REQUIRED_BROKER_COLUMNS))

    missing = REQUIRED_BROKER_COLUMNS - set(df.columns)
    if missing:
        st.error(
            f"{config.BROKERS_CSV_PATH} is missing required column(s): {', '.join(sorted(missing))}. "
            "Expected: broker_name, compliance_email, optout_url, notes."
        )
        return pd.DataFrame(columns=list(REQUIRED_BROKER_COLUMNS))

    df["compliance_email"] = df["compliance_email"].fillna("")
    df["notes"] = df["notes"].fillna("")
    if "search_url" not in df.columns:
        df["search_url"] = ""
    df["search_url"] = df["search_url"].fillna("")
    if "automated_search" not in df.columns:
        df["automated_search"] = ""
    df["automated_search"] = df["automated_search"].fillna("")
    return df


brokers_df = load_brokers()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
# A keyed widget's session_state value can't be changed after that widget has
# rendered in the same run — so a "quick action" button can't set nav_mode
# directly. It sets pending_nav instead, and we apply it here, before the
# radio widget below is created.
if "pending_nav" in st.session_state:
    st.session_state.nav_mode = st.session_state.pop("pending_nav")

st.sidebar.title(f"{config.APP_ICON} {config.APP_TITLE}")
st.sidebar.caption(config.APP_TAGLINE)
st.sidebar.markdown("---")

mode = st.sidebar.radio(
    "Select Tool",
    [
        ":material/dashboard: Dashboard",
        ":material/travel_explore: Results",
        ":material/mail: 1. Data Broker Deletion Letters",
        ":material/gavel: 2. NY Expungement Guidance",
        ":material/search_off: 3. Google De-Indexing",
        ":material/monitoring: 4. Campaign Tracker",
    ],
    key="nav_mode",
    label_visibility="visible",
)

st.sidebar.markdown("---")

if st.sidebar.button("📋 Load Demo Profile"):
    st.session_state.user_name = config.DEMO_PROFILE["name"]
    st.session_state.user_email = config.DEMO_PROFILE["email"]
    st.session_state.user_location = config.DEMO_PROFILE["location"]
    st.session_state.record_url = config.DEMO_PROFILE["record_url"]
    st.sidebar.success("Demo profile loaded!")

st.sidebar.markdown("---")
st.sidebar.markdown(
    f"""
    <div class="np-info-box" style="font-size: 0.85em;">
    <strong>California resident?</strong><br>
    The state's own deletion tool, <strong>DROP</strong>, reaches every
    <em>registered</em> data broker with one request, and brokers have been
    required to process DROP requests since Aug 1, 2026. Start there — use
    Non-Pursuit for brokers that aren't registered, for other states, or to
    escalate if a broker misses its window.
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.link_button("Open DROP (privacy.ca.gov)", config.CA_DROP_URL)

st.title(f"{config.APP_ICON} {config.APP_TITLE}")
st.caption(config.APP_TAGLINE)
st.markdown("---")


# ---------------------------------------------------------------------------
# MODE: Dashboard
# ---------------------------------------------------------------------------
if mode == ":material/dashboard: Dashboard":
    dashboard_component.render()

elif mode == ":material/travel_explore: Results":
    results_component.render(brokers_df)

elif mode == ":material/mail: 1. Data Broker Deletion Letters":
    letters_component.render(brokers_df)

elif mode == ":material/gavel: 2. NY Expungement Guidance":
    st.header(":material/gavel: New York Criminal Record Expungement Guidance")
    st.markdown("Navigate New York Criminal Procedure Law (CPL) pathways for record sealing and expungement.")
    st.markdown("---")

    st.subheader("Step 1: Case Outcome")
    case_outcome = st.selectbox(
        "What was the outcome of your criminal case?",
        ["Case was dismissed / acquitted", "Convicted of a crime", "Convicted of a violation / non-criminal offense"],
    )

    if case_outcome == "Case was dismissed / acquitted":
        st.markdown("---")
        st.success("### You may qualify under **NY CPL § 160.50**")
        st.markdown(
            """
            **CPL 160.50** applies to cases where:
            - The case was dismissed
            - You were acquitted (found not guilty)
            - The prosecution terminated the case

            **Next Steps:**
            1. Your records should be automatically sealed
            2. If not sealed, file a motion with the court
            3. Contact the court where your case was heard
            """
        )
        st.link_button("📚 Official NY Courts Guide", config.NY_COURT_EXPUNGEMENT_URL)

    elif case_outcome == "Convicted of a crime":
        st.markdown("---")
        st.subheader("Step 2: Waiting Period")
        time_since_conviction = st.selectbox(
            "How long has it been since your conviction?",
            ["Less than 10 years", "10+ years"],
        )

        if time_since_conviction == "10+ years":
            st.success("### You may qualify under **NY CPL § 160.59**")
            st.markdown(
                """
                **CPL 160.59** allows for sealing of certain convictions after 10 years if:
                - You have no more than 2 convictions
                - You have no pending criminal charges
                - You have satisfied all sentencing requirements
                - The conviction was not for a sex offense or violent felony

                **Next Steps:**
                1. File a certificate of disposition
                2. Submit motion to seal with the court
                3. Attend court hearing if required
                """
            )
            st.link_button("📚 Official NY Courts Guide", config.NY_COURT_EXPUNGEMENT_URL)
        else:
            st.warning("### You do not currently qualify for CPL 160.59")
            st.markdown(
                """
                You must wait 10 years from the date of conviction before applying for sealing under CPL 160.59.

                **Consider:**
                - CPL 160.55 for certain marijuana convictions
                - Certificate of Relief from Disabilities
                - Consult with a criminal defense attorney
                """
            )

    else:
        st.markdown("---")
        st.success("### Your record may already be sealed")
        st.markdown(
            """
            Violations and non-criminal offenses (e.g., disorderly conduct, traffic violations) are typically:
            - Automatically sealed after 1 year
            - Not visible in standard background checks
            - Not considered criminal convictions

            **Verification:**
            - Request your criminal history from the NY Division of Criminal Justice Services
            - Check with the court where your case was heard
            """
        )
        st.link_button("📚 Official NY Courts Guide", config.NY_COURT_EXPUNGEMENT_URL)

    st.markdown("---")
    st.info(
        "⚠️ **Disclaimer:** This tool provides general guidance only. For legal advice regarding "
        "your specific situation, consult with a qualified New York criminal defense attorney."
    )


# ---------------------------------------------------------------------------
# MODE 3: Google De-Indexing
# ---------------------------------------------------------------------------
elif mode == ":material/search_off: 3. Google De-Indexing":
    st.header(":material/search_off: Google PII Removal Request")
    st.markdown("Request removal of personally identifiable information from Google Search results.")
    st.markdown("---")

    st.subheader("Standardized PII Justification Statement")

    justification_text = """I am requesting the removal of personally identifiable information (PII) from Google Search results because:

1. The information contains my personal contact details (home address, phone number, email)
2. This information is being exposed without my consent
3. The information poses a risk to my personal safety and privacy
4. The information is not of public interest and does not serve a newsworthy purpose
5. I am the subject of this information and have not authorized its publication

This request is made under Google's PII removal policies for:
- Confidential government ID numbers
- Bank account or credit card numbers
- Images of handwritten signatures
- Personal medical records
- Private contact information

I have attached evidence of the search results containing this information and request prompt removal to protect my privacy and security."""

    st.code(justification_text, language=None)
    st.caption("Use the copy icon in the corner above to copy this text.")

    st.markdown("---")

    st.subheader("🚀 Submit to Google")
    st.link_button("🔗 Google PII Removal Portal", config.GOOGLE_PII_REMOVAL_URL)
    st.caption("Opens Google's official removal request form")

    st.markdown("---")

    st.subheader("📝 Before Submitting")
    st.markdown(
        """
        **Required Information:**
        - URLs of the pages containing your PII
        - Screenshots of the search results
        - Your contact information for verification
        - Specific type of PII (address, phone, SSN, etc.)

        **Processing Time:**
        - Google typically reviews requests within a few days
        - You will receive email confirmation of the decision
        - Approved removals take effect within 24-48 hours

        **Important Notes:**
        - This only removes content from Google Search, not the original website
        - Contact the website hosting the information directly for complete removal
        - Keep records of your submission for follow-up
        """
    )


# ---------------------------------------------------------------------------
# MODE 4: Campaign Tracker
# ---------------------------------------------------------------------------
elif mode == ":material/monitoring: 4. Campaign Tracker":
    st.header(":material/monitoring: Campaign Tracker")
    st.markdown("Every request logged from the other tools shows up here, with its response deadline tracked automatically.")
    st.markdown("---")

    with st.expander("➕ Log a request manually"):
        with st.form("manual_log_form"):
            m_broker = st.text_input("Broker / recipient name")
            m_channel = st.selectbox("Channel", ["Email", "Opt-out form", "Mail", "Other"])
            m_window = st.number_input("Response window (days)", min_value=1, value=config.CCPA_RESPONSE_WINDOW_DAYS)
            m_notes = st.text_input("Notes (optional)")
            submitted = st.form_submit_button("Log request")
            if submitted and m_broker:
                add_request(config.TRACKER_DB_PATH, m_broker, m_channel, int(m_window), m_notes)
                st.success(f"Logged {m_broker}.")

    cleared_count = purge_expired_notes(config.TRACKER_DB_PATH, config.PII_RETENTION_DAYS)
    if cleared_count:
        st.toast(f"🗑️ Cleared notes on {cleared_count} request(s) completed over {config.PII_RETENTION_DAYS} days ago.")

    requests_list = get_all_requests(config.TRACKER_DB_PATH)

    st.caption(
        f"🔒 Notes on completed requests are cleared automatically after {config.PII_RETENTION_DAYS} days — "
        "this app doesn't hold onto your data forever."
    )

    if not requests_list:
        st.info("Nothing logged yet. Generate a letter in Mode 1 and click \"Log this request\", or add one manually above.")
    else:
        overdue_count = sum(1 for r in requests_list if r["is_overdue"])
        c1, c2, c3 = st.columns(3)
        c1.metric("Total tracked", len(requests_list))
        c2.metric("Overdue", overdue_count)
        c3.metric("Complete", sum(1 for r in requests_list if r["status"] == "Complete"))

        open_requests = [r for r in requests_list if r["status"] != "Complete"]
        if open_requests:
            st.download_button(
                "📅 Export deadlines to calendar (.ics)",
                data=build_ics(requests_list),
                file_name="non_pursuit_deadlines.ics",
                mime="text/calendar",
            )

        st.markdown("---")

        for r in requests_list:
            cols = st.columns([3, 2, 2, 2, 2, 1])
            cols[0].markdown(f"**{r['broker_name']}**  \n{r['channel']}")
            cols[1].markdown(f"Sent: {r['date_sent']}")
            deadline_label = f"Due: {r['deadline']}"
            if r["is_overdue"]:
                cols[2].markdown(f'<span class="np-overdue">⚠️ Overdue ({deadline_label})</span>', unsafe_allow_html=True)
            else:
                cols[2].markdown(f"{deadline_label} ({r['days_remaining']}d left)")

            new_status = cols[3].selectbox(
                "Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(r["status"]),
                key=f"status_{r['id']}", label_visibility="collapsed",
            )
            if new_status != r["status"]:
                update_status(config.TRACKER_DB_PATH, r["id"], new_status)
                st.rerun()

            if r["notes"]:
                cols[4].caption(r["notes"])

            if cols[5].button("🗑️", key=f"delete_{r['id']}"):
                delete_request(config.TRACKER_DB_PATH, r["id"])
                st.rerun()


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.markdown(
    f"""
    <div style='text-align: center; color: #888; font-size: 12px;'>
        <p>{config.APP_TITLE} — {config.APP_TAGLINE}</p>
        <p>For educational purposes only. Not legal advice.</p>
    </div>
    """,
    unsafe_allow_html=True,
)
