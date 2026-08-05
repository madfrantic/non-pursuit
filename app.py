import sys
import os
import hashlib
from datetime import datetime

import streamlit as st
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), "utils"))
from tracker import add_request, get_all_requests, update_status, delete_request, purge_expired_notes, STATUS_OPTIONS
from calendar_export import build_ics
import config

from components import self_search as self_search_component
from components import letters as letters_component
from components import dashboard as dashboard_component
from components import wizard as wizard_component


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
    "record_url": "",
    "listed_confirmed": {},
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


def generate_privacy_report(results):
    """Generate a detailed privacy report for download."""
    report = []
    report.append("=" * 60)
    report.append("NON-PURSUIT PRIVACY EXPOSURE REPORT")
    report.append("Generated: " + datetime.now().strftime("%B %d, %Y at %I:%M %p"))
    report.append("=" * 60)
    report.append("")

    score = results.get("privacy_score", {})
    report.append(f"PRIVACY SCORE: {score.get('score', 'N/A')}%")
    report.append(f"RATING: {score.get('level', 'Unknown')}")
    report.append("")
    report.append("-" * 60)

    for category, data in results.items():
        if category == "privacy_score":
            continue

        report.append(f"{category.upper().replace('_', ' ')}:")
        report.append(f"  Found: {'YES' if data.get('found', False) else 'NO'}")
        report.append(f"  Risk Level: {data.get('risk_level', 'Unknown').upper()}")
        report.append(f"  Details: {data.get('description', 'N/A')}")
        if data.get('recommendation'):
            report.append(f"  Recommendation: {data.get('recommendation')}")
        report.append("")

    report.append("-" * 60)
    report.append("NEXT STEPS:")
    report.append("1. Use Non-Pursuit's Data Broker Deletion Letters to start removal")
    report.append("2. Submit Google PII removal requests")
    report.append("3. Set all social media accounts to private")
    report.append("4. Change passwords for breached accounts")
    report.append("5. Consider using a password manager")
    report.append("")
    report.append("=" * 60)

    return "\n".join(report)


def check_search_frequency(query: str) -> dict:
    """
    Simulate checking how many times a query appears in search results.
    In production, you'd use a search API.
    """
    query_hash = hashlib.md5(query.encode()).hexdigest()
    base_count = int(query_hash[:4], 16) % 100

    return {
        "google": base_count,
        "bing": int(base_count * 0.8),
        "duckduckgo": int(base_count * 0.6),
        "yahoo": int(base_count * 0.7),
    }


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
        ":material/rocket_launch: Guided Wizard",
        ":material/security: Privacy Dashboard",
        ":material/person_search: Should I Worry? (Self-Search)",
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


# ---------------------------------------------------------------------------
# MODE: Guided Wizard
# ---------------------------------------------------------------------------
elif mode == ":material/rocket_launch: Guided Wizard":
    wizard_component.render(brokers_df)


# ---------------------------------------------------------------------------
# MODE: Privacy Dashboard
# ---------------------------------------------------------------------------
elif mode == ":material/security: Privacy Dashboard":
    st.header(":material/security: Privacy Exposure Dashboard")
    st.markdown("See where your personal information is exposed online and what to do about it.")
    st.warning(
        "⚠️ **The scores and counts below are simulated for demonstration** — generated from a "
        "hash of your info, not real lookups against breach databases or broker sites. For an "
        "actual check, use **Should I Worry? (Self-Search)** to search real broker sites yourself, "
        "or the **Check Email Breaches** button below, which links to the real HaveIBeenPwned."
    )
    st.markdown("---")

    has_info = any([
        st.session_state.user_name,
        st.session_state.user_email,
        st.session_state.user_location,
    ])

    if not has_info:
        st.info("👤 Enter your personal information above to get a privacy score and see where your data is exposed.")
        st.markdown("""
        ### What we check:
        - **Social Security Number** - Critical identity theft risk
        - **Email Address** - Breach exposure and spam risk
        - **Phone Number** - Telemarketing and SIM swapping risk
        - **Physical Address** - Doxxing and mail fraud risk
        - **Social Media** - Oversharing and stalking risk
        - **Data Brokers** - Who's selling your information
        """)
        st.stop()

    from utils.privacy_scanner import PrivacyScanner

    user_data = {
        "name": st.session_state.user_name,
        "email": st.session_state.user_email,
        "location": st.session_state.user_location,
        "address": st.session_state.user_location,
        "phone": st.session_state.get("user_phone", ""),
        "ssn": st.session_state.get("user_ssn", ""),
        "social_media": st.session_state.get("social_media_accounts", {}),
        "has_taken_action": st.session_state.get("has_taken_action", False),
    }

    with st.expander("🔐 Enter additional information to scan (optional)", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            user_phone = st.text_input("Phone Number", placeholder="(555) 555-5555")
            if user_phone:
                st.session_state.user_phone = user_phone

        with col2:
            user_ssn = st.text_input("Social Security Number (last 4 only)",
                                    placeholder="XXX-XX-1234", type="password")
            if user_ssn and len(user_ssn) > 4:
                st.session_state.user_ssn = user_ssn

    with st.expander("📱 Social Media Accounts to check", expanded=False):
        st.caption("Enter your usernames to check if your social media profiles are publicly visible")
        social_media = {}
        col1, col2 = st.columns(2)
        with col1:
            instagram = st.text_input("Instagram username", placeholder="@username")
            if instagram:
                social_media["instagram"] = instagram
            twitter = st.text_input("Twitter/X username", placeholder="@username")
            if twitter:
                social_media["twitter"] = twitter
        with col2:
            facebook = st.text_input("Facebook username", placeholder="username")
            if facebook:
                social_media["facebook"] = facebook
            linkedin = st.text_input("LinkedIn username", placeholder="username")
            if linkedin:
                social_media["linkedin"] = linkedin

        if social_media:
            st.session_state.social_media_accounts = social_media

    scanner = PrivacyScanner(user_data)
    results = scanner.scan_all()

    score_data = results.get("privacy_score", {"score": 0, "level": "Unknown"})
    score = score_data["score"]
    level = score_data["level"]

    if score >= 80:
        color = "#22c55e"
        emoji = "🌟"
    elif score >= 60:
        color = "#eab308"
        emoji = "👍"
    elif score >= 40:
        color = "#f97316"
        emoji = "⚠️"
    else:
        color = "#ef4444"
        emoji = "🚨"

    st.markdown(f"""
    <div style='text-align: center; padding: 20px; background: {config.SECONDARY_BACKGROUND_COLOR}; border-radius: 10px; margin: 20px 0;'>
        <div style='font-size: 48px;'>{emoji}</div>
        <h1 style='color: {color}; font-size: 72px; margin: 0;'>{score}%</h1>
        <p style='font-size: 24px; color: {config.TEXT_COLOR};'>Privacy Score: <strong>{level}</strong></p>
        <p style='color: #888;'>{score_data.get('description', 'Your privacy score based on information exposure')}</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    st.subheader("📊 Exposure Breakdown")

    cols = st.columns(3)
    category_metrics = {
        "social_security": "🔑 SSN",
        "email": "📧 Email",
        "phone": "📱 Phone",
        "address": "🏠 Address",
        "social_media": "👤 Social Media",
        "data_brokers": "🏢 Data Brokers",
        "breaches": "💻 Data Breaches",
    }

    for idx, (key, label) in enumerate(category_metrics.items()):
        if key in results:
            result = results[key]
            col = cols[idx % 3]
            found = result.get("found", False)
            count = result.get("exposure_count", 0)
            risk = result.get("risk_level", "low")

            risk_colors = {
                "low": "#22c55e",
                "medium": "#eab308",
                "high": "#f97316",
                "critical": "#ef4444",
            }
            color = risk_colors.get(risk, "#888")

            status_emoji = "🟢" if risk == "low" else "🟡" if risk == "medium" else "🔴"

            col.markdown(f"""
            <div style='background: {config.SECONDARY_BACKGROUND_COLOR}; padding: 12px; border-radius: 8px; margin-bottom: 10px;'>
                <div style='display: flex; justify-content: space-between;'>
                    <span style='font-weight: bold;'>{label}</span>
                    <span style='color: {color};'>{status_emoji} {risk.title()}</span>
                </div>
                <div style='font-size: 14px; color: #888;'>
                    {result.get('description', 'Not scanned')}
                </div>
                {f"<div style='font-size: 12px; color: #666; margin-top: 4px;'>Found: {count} locations</div>" if count > 0 else ""}
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")

    st.subheader("🎯 Action Items")

    for category, result in results.items():
        if category == "privacy_score":
            continue

        if result.get("found", False) and result.get("risk_level") in ["high", "critical"]:
            with st.container():
                st.warning(f"**{category.replace('_', ' ').title()}**: {result.get('recommendation', 'Take action to protect your privacy.')}")

    if "data_brokers" in results and results["data_brokers"].get("found", False):
        brokers = results["data_brokers"].get("brokers", [])
        if brokers:
            st.info(f"📋 You're listed on {len(brokers)} data broker sites: {', '.join(brokers[:5])}")
            st.markdown("Use the **Data Broker Deletion Letters** tool to start removing your information.")

    if "breaches" in results and results["breaches"].get("found", False):
        breach_count = results["breaches"].get("exposure_count", 0)
        st.error(f"🚨 Simulated result: {breach_count} known data breaches (not a real check — see warning above).")
        st.markdown("""
        **Recommended actions:**
        1. Change passwords for all accounts using this email
        2. Enable 2-factor authentication everywhere
        3. Consider using a password manager
        4. Monitor your credit reports for suspicious activity
        """)

    st.markdown("---")
    st.subheader("🔍 Check Search Engine Exposure")

    search_col1, search_col2 = st.columns([2, 1])
    with search_col1:
        search_query = st.text_input(
            "Search for your name or information",
            value=st.session_state.user_name,
            placeholder="John Doe"
        )

    if search_query:
        from utils.check_exposure import generate_search_links

        st.caption("🔗 Manually check these search engines for your information:")
        search_links = generate_search_links(search_query)

        cols = st.columns(len(search_links))
        for idx, (engine, url) in enumerate(search_links.items()):
            cols[idx].link_button(f"Search {engine.title()}", url)

    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📧 Check Email Breaches", use_container_width=True):
            email = st.session_state.user_email
            if email:
                st.link_button("Check on HaveIBeenPwned", f"https://haveibeenpwned.com/account/{email}")
            else:
                st.warning("Enter your email above first")

    with col2:
        if st.button("🛡️ Get Data Removal Checklist", use_container_width=True):
            st.session_state.show_checklist = True

    with col3:
        if st.button("📊 Download Privacy Report", use_container_width=True):
            report = generate_privacy_report(results)
            st.download_button(
                "📥 Download Report",
                data=report,
                file_name=f"privacy_report_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
            )


# ---------------------------------------------------------------------------
# MODE 0: Should I Worry? (Self-Search)
# ---------------------------------------------------------------------------
elif mode == ":material/person_search: Should I Worry? (Self-Search)":
    self_search_component.render(brokers_df)

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
