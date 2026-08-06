import base64
import sys
import os

import streamlit as st
import pandas as pd

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
UTILS_DIR = os.path.join(ROOT_DIR, "utils")
if UTILS_DIR not in sys.path:
    sys.path.append(UTILS_DIR)

from tracker import add_request, get_all_requests, update_status, delete_request, purge_expired_notes, STATUS_OPTIONS
from calendar_export import build_ics
from data_export import build_json_export
import exposure_store
import config

from components import letters as letters_component
from components import dashboard as dashboard_component
from components import results as results_component


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon=config.APP_LOGO_PATH,
    layout=config.APP_LAYOUT,
    # "expanded" pinned the sidebar open even on narrow viewports, leaving
    # the main content squeezed into a sliver on mobile. "auto" keeps
    # desktop's default-open behavior but lets Streamlit's native
    # responsive breakpoint collapse it into a slide-out drawer on phones.
    initial_sidebar_state="auto",
)


@st.cache_data
def _image_data_uri(path):
    """Raw <img> tags inside a custom-HTML block can't reference a local
    file path directly -- base64-embedding it is the standard way to get a
    local image into markdown(unsafe_allow_html=True)."""
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

# Theming lives in .streamlit/config.toml. The only custom CSS left here is
# for the "Broker Notes" info box and letter-preview box, which Streamlit's
# theme doesn't reach on its own — kept in sync with config.py's palette so
# changing one color in config.py and matching it in config.toml is the only
# thing needed to re-theme the app.
st.markdown(
    f"""
    <style>
        .stApp {{
            padding-top: 0.5rem;
        }}
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
        .np-hero {{
            background: linear-gradient(135deg, rgba(37, 99, 235, 0.16), rgba(96, 165, 250, 0.06));
            border: 1px solid rgba(96, 165, 250, 0.24);
            border-radius: 18px;
            padding: 1.15rem 1.25rem;
            margin-bottom: 1rem;
        }}
        .np-card-label {{
            display: inline-block;
            padding: 0.2rem 0.55rem;
            border-radius: 999px;
            background: rgba(37, 99, 235, 0.16);
            color: #bfdbfe;
            font-size: 0.78rem;
            font-weight: 600;
            margin-bottom: 0.45rem;
        }}
        .np-step-pill {{
            display: inline-block;
            padding: 0.35rem 0.7rem;
            border-radius: 999px;
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid rgba(148, 163, 184, 0.22);
            margin-right: 0.45rem;
            margin-top: 0.35rem;
            font-size: 0.82rem;
        }}
        .np-quiet {{
            color: #94a3b8;
        }}
        /* Real emoji render as plain characters (unlike Material Symbols,
        which Streamlit wraps in their own styled span) -- they scale with
        the label's own font-size, so bumping that is what makes them pop. */
        [data-testid="stSidebar"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p {{
            font-size: 1.2rem;
            line-height: 1.8;
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
    "pending_nav": None,
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
if st.session_state.get("pending_nav") is not None:
    st.session_state.nav_mode = st.session_state.pop("pending_nav")

st.sidebar.markdown(
    f"""
    <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem;">
        <img src="{_image_data_uri(config.APP_LOGO_PATH)}" style="height: 40px;" alt="">
        <img src="{_image_data_uri(config.APP_WORDMARK_PATH)}" style="height: 26px;" alt="{config.APP_TITLE}">
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.caption(config.APP_TAGLINE)
st.sidebar.markdown("---")

mode = st.sidebar.radio(
    "Select Tool",
    [
        "📊 Dashboard",
        "🔍 Results",
        "✉️ Data Broker Deletion Letters",
        "⚖️ NY Expungement Guidance",
        "🚫 Google De-Indexing",
        "📈 Campaign Tracker",
    ],
    key="nav_mode",
    label_visibility="visible",
)

st.markdown(
    f"""
    <div style="width: 100%; display: flex; align-items: center; justify-content: center;
                gap: 0; padding: 0.5rem 0 0.5rem 0;">
        <img src="{_image_data_uri(config.APP_LOGO_PATH)}" style="height: 170px;" alt="">
        <img src="{_image_data_uri(config.APP_WORDMARK_PATH)}" style="height: 195px; margin-left: -0.3rem;" alt="{config.APP_TITLE}">
    </div>
    <p style="width: 100%; text-align: center; font-size: 1.5rem; font-weight: 700;
              color: {config.TEXT_COLOR}; margin: 0 0 1rem 0;">
        {config.APP_TAGLINE}
    </p>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# MODE: Dashboard
# ---------------------------------------------------------------------------
if mode == "📊 Dashboard":
    dashboard_component.render()

elif mode == "🔍 Results":
    results_component.render(brokers_df)

elif mode == "✉️ Data Broker Deletion Letters":
    letters_component.render(brokers_df)

elif mode == "⚖️ NY Expungement Guidance":
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
elif mode == "🚫 Google De-Indexing":
    st.header(":material/search_off: Google PII Removal Request")
    st.markdown("Request removal of personally identifiable information from Google Search results.")
    st.markdown("---")

    g_name = st.session_state.user_name
    g_email = st.session_state.user_email

    if not g_name or not g_email:
        st.warning("Enter your name and email on the **Dashboard** first — this request is personalized and needs a real contact for verification.")
    else:
        st.subheader("URLs to request removal for")
        st.caption("Paste each Google search result URL containing your PII, one per line.")
        pii_urls_raw = st.text_area(
            "URLs",
            placeholder="https://broker-site.com/your-name/record\nhttps://example.com/another-page",
            height=100,
            label_visibility="collapsed",
        )
        pii_urls = [u.strip() for u in pii_urls_raw.splitlines() if u.strip()]

        st.markdown("---")
        st.subheader("Standardized PII Justification Statement")

        if pii_urls:
            url_block = "\n".join(f"- {u}" for u in pii_urls)
        else:
            url_block = "- [Add the specific URLs above before submitting — Google requires them]"

        justification_text = f"""I am {g_name}, and I am requesting the removal of personally identifiable information (PII) from Google Search results because:

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

The specific URLs containing this information are:
{url_block}

I have attached evidence of the search results containing this information and request prompt removal to protect my privacy and security. I can be reached at {g_email} to verify this request."""

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
elif mode == "📈 Campaign Tracker":
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

        export_cols = st.columns(2)
        open_requests = [r for r in requests_list if r["status"] != "Complete"]
        if open_requests:
            export_cols[0].download_button(
                "📅 Export deadlines to calendar (.ics)",
                data=build_ics(requests_list),
                file_name="non_pursuit_deadlines.ics",
                mime="text/calendar",
                width="stretch",
            )
        export_cols[1].download_button(
            ":material/download: Export all my data (.json)",
            data=build_json_export(requests_list, exposure_store.get_all_checks(config.EXPOSURE_DB_PATH)),
            file_name="non_pursuit_data_export.json",
            mime="application/json",
            width="stretch",
            help="Everything tracked in this app -- campaign requests and self-search history -- as one portable file you control.",
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
