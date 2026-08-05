import io
import re
import sys
import os
import zipfile
import hashlib
from datetime import datetime

import streamlit as st
import pandas as pd
from jinja2 import Environment, FileSystemLoader

sys.path.append(os.path.join(os.path.dirname(__file__), "utils"))
from mailto_builder import build_mailto_link, mailto_length, is_mailto_safe
from tracker import add_request, get_all_requests, update_status, delete_request, purge_expired_notes, STATUS_OPTIONS
from calendar_export import build_ics
import spokeo_automation
import config


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
# Validation helpers
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip())) if value else False


def is_valid_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://")) if value else False


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


def render_letter(env, broker_name, user_name, user_location, user_email, record_url):
    template = env.get_template("ccpa_deletion_demand.j2")
    return template.render(
        broker_name=broker_name,
        user_name=user_name,
        user_location=user_location,
        user_email=user_email,
        record_url=record_url,
        current_date=datetime.now().strftime("%B %d, %Y"),
    )


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
st.sidebar.title(f"{config.APP_ICON} {config.APP_TITLE}")
st.sidebar.caption(config.APP_TAGLINE)
st.sidebar.markdown("---")

mode = st.sidebar.radio(
    "Select Tool",
    [
        "🔍 Privacy Dashboard",
        "🔍 Should I Worry? (Self-Search)",
        "1. Data Broker Deletion Letters",
        "2. NY Expungement Guidance",
        "3. Google De-Indexing",
        "4. Campaign Tracker",
    ],
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
# MODE: Privacy Dashboard
# ---------------------------------------------------------------------------
if mode == "🔍 Privacy Dashboard":
    st.header("🛡️ Privacy Exposure Dashboard")
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
elif mode == "🔍 Should I Worry? (Self-Search)":
    st.header("🔍 Should I worry?")
    st.markdown(
        "Search each data broker's site for your own name **before** generating a deletion "
        "letter. There's no point demanding a broker delete a record you haven't confirmed exists."
    )
    st.markdown("---")

    st.subheader("Your search terms")
    st.caption("A name alone is rarely enough to find yourself on these sites — most also need a city/state to narrow it down.")
    ss_col1, ss_col2 = st.columns(2)
    with ss_col1:
        self_search_name = st.text_input(
            "Full Name", value=st.session_state.user_name, placeholder="Enter your full legal name"
        )
    with ss_col2:
        self_search_location = st.text_input(
            "Location", value=st.session_state.user_location, placeholder="City, State"
        )
    st.session_state.user_name = self_search_name
    st.session_state.user_location = self_search_location

    if self_search_name or self_search_location:
        st.info(
            f"Type this into each broker's search box below: "
            f"**{self_search_name or '(your name)'}**, **{self_search_location or '(your city/state)'}**"
        )

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
    else:
        metric_placeholder = st.empty()
        st.markdown("---")

        for _, broker in brokers_df.iterrows():
            broker_name = broker["broker_name"]
            is_automated = bool(broker["automated_search"])

            row_cols = st.columns([3, 2, 3])
            row_cols[0].markdown(f"**{broker_name}**")
            if broker["notes"]:
                row_cols[0].caption(broker["notes"])

            if is_automated:
                if not self_search_name:
                    row_cols[1].caption("Enter your name above first")
                else:
                    if row_cols[1].button("🤖 Auto-search", key=f"autosearch_{broker_name}"):
                        with st.spinner(
                            f"Chrome is open and searching {broker_name} — review the results there, "
                            "then close that window to continue."
                        ):
                            outcome = spokeo_automation.run_search_and_wait(
                                self_search_name, self_search_location
                            )
                        if outcome == "timed_out":
                            wait_minutes = spokeo_automation.MAX_WAIT_SECONDS // 60
                            st.warning(f"Closed the {broker_name} window automatically after {wait_minutes} minutes of inactivity.")
            elif broker["search_url"]:
                row_cols[1].link_button("Search", broker["search_url"], key=f"selfsearch_link_{broker_name}")
            else:
                row_cols[1].caption("No search link on file")

            checked = row_cols[2].checkbox(
                "Found myself listed here",
                key=f"selfsearch_check_{broker_name}",
            )
            st.session_state.listed_confirmed[broker_name] = checked

        found_count = sum(1 for v in st.session_state.listed_confirmed.values() if v)
        metric_placeholder.metric("Brokers confirmed listed", f"{found_count} / {len(brokers_df)} checked")

        if self_search_name:
            st.markdown("---")
            st.subheader("🌐 Online Search Presence")

            with st.spinner("Checking search engine presence..."):
                from utils.check_exposure import check_online_exposure

                search_terms = {
                    "name": self_search_name,
                    "name_location": f"{self_search_name} {self_search_location}" if self_search_location else self_search_name,
                    "email": st.session_state.user_email,
                }

                exposure_results = check_online_exposure(search_terms)

                exp_cols = st.columns(3)
                for idx, (term, result) in enumerate(exposure_results.items()):
                    if result["found"]:
                        exp_cols[idx % 3].metric(
                            label=term.replace("_", " ").title(),
                            value=f"{result['count']} matches",
                            delta="Found" if result['count'] > 0 else "Not found",
                            delta_color="inverse" if result['count'] > 5 else "normal",
                        )

            st.caption("🔗 Check these search engines manually:")
            link_cols = st.columns(4)
            search_engines = {
                "Google": f"https://www.google.com/search?q={self_search_name.replace(' ', '+')}",
                "Bing": f"https://www.bing.com/search?q={self_search_name.replace(' ', '+')}",
                "DuckDuckGo": f"https://duckduckgo.com/?q={self_search_name.replace(' ', '+')}",
                "Yahoo": f"https://search.yahoo.com/search?p={self_search_name.replace(' ', '+')}",
            }
            for idx, (engine_name, url) in enumerate(search_engines.items()):
                link_cols[idx].link_button(f"🔍 {engine_name}", url)

        st.markdown("---")
        if found_count > 0:
            st.success(
                f"You're listed on {found_count} broker(s). Head to **Data Broker Deletion Letters** "
                "in the sidebar — it already knows which brokers you confirmed here."
            )
        else:
            st.info("Check off any broker above where you found your own information.")


# ---------------------------------------------------------------------------
# MODE 1: Data Broker Deletion Letters
# ---------------------------------------------------------------------------
elif mode == "1. Data Broker Deletion Letters":
    st.header("📜 CCPA Data Deletion Demand Letters")
    st.markdown(
        "Generate formal deletion demand letters for data brokers under "
        "California Civil Code § 1798.105."
    )

    col1, col2 = st.columns(2)
    with col1:
        user_name = st.text_input("Full Name", value=st.session_state.user_name, placeholder="Enter your full legal name")
        user_email = st.text_input("Email Address", value=st.session_state.user_email, placeholder="your.email@example.com")
    with col2:
        user_location = st.text_input("Location", value=st.session_state.user_location, placeholder="City, State")
        record_url = st.text_input("Record URL", value=st.session_state.record_url, placeholder="https://broker.com/record/...")

    st.session_state.user_name = user_name
    st.session_state.user_email = user_email
    st.session_state.user_location = user_location
    st.session_state.record_url = record_url

    errors = []
    if user_name and len(user_name.strip()) < 2:
        errors.append("Enter your full name.")
    if user_email and not is_valid_email(user_email):
        errors.append("That email address doesn't look valid.")
    if record_url and not is_valid_url(record_url):
        errors.append("Record URL should start with http:// or https://")

    for e in errors:
        st.warning(e)

    ready = bool(user_name and user_email and user_location and record_url) and not errors

    st.markdown("---")

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
    else:
        batch_mode = st.toggle("Batch mode (select multiple brokers)", value=False)
        env = Environment(loader=FileSystemLoader("templates"))

        if not ready:
            st.info("Fill in your name, email, location, and record URL above to generate letters.")

        elif not batch_mode:
            broker_options = brokers_df["broker_name"].tolist()
            selected_broker = st.selectbox("Select Target Data Broker", broker_options)
            broker_info = brokers_df[brokers_df["broker_name"] == selected_broker].iloc[0]
            broker_email = broker_info["compliance_email"]
            optout_url = broker_info["optout_url"]
            broker_notes = broker_info["notes"]

            if broker_notes:
                st.markdown(f'<div class="np-info-box">📝 {broker_notes}</div>', unsafe_allow_html=True)

            search_url = broker_info["search_url"]
            st.markdown("---")
            st.subheader("🔍 Step 1: Confirm you're actually listed")

            if st.session_state.listed_confirmed.get(selected_broker, False):
                st.success(f"✅ Already confirmed via Should I Worry? that you're listed on {selected_broker}.")
                confirmed_listed = True
            else:
                st.caption(
                    "Search the broker's site for your own name before sending a deletion demand — "
                    "don't ask them to delete a record you haven't confirmed exists."
                )
                if search_url:
                    st.link_button(f"Search {selected_broker}", search_url)
                else:
                    st.caption(f"No search link on file for {selected_broker} yet — search their site manually.")
                confirmed_listed = st.checkbox(
                    f"I searched {selected_broker} and found a listing with my information",
                    key=f"confirmed_{selected_broker}",
                )
                if confirmed_listed:
                    st.session_state.listed_confirmed[selected_broker] = True

            if not confirmed_listed:
                st.info("Check the box above once you've confirmed you're listed to generate the letter.")
            else:
                rendered_letter = render_letter(env, selected_broker, user_name, user_location, user_email, record_url)

                st.markdown("---")
                st.subheader("📄 Generated Demand Letter")
                st.code(rendered_letter, language=None)
                st.caption("Use the copy icon in the corner above, or download below.")

                st.markdown("---")
                st.subheader("🚀 Actions")

                col_a, col_b, col_c = st.columns(3)

                with col_a:
                    if broker_email:
                        subject = f"CCPA Data Deletion Demand - {user_name}"
                        safe = is_mailto_safe(broker_email, subject, rendered_letter, config.MAILTO_SAFE_LENGTH)
                        mailto_link = build_mailto_link(broker_email, subject, rendered_letter)
                        st.link_button("📧 Open Email Client", mailto_link, disabled=not safe)
                        st.caption(f"To: {broker_email}")
                        if not safe:
                            st.warning(
                                f"This letter is long enough ({mailto_length(broker_email, subject, rendered_letter)} "
                                "encoded characters) that some email clients will silently truncate it as a mailto "
                                "link. Download it instead and paste it into a new email."
                            )
                    else:
                        st.caption("No compliance email on file for this broker — use the opt-out form instead.")

                with col_b:
                    st.download_button(
                        label="📥 Download as TXT",
                        data=rendered_letter,
                        file_name=f"ccpa_demand_{selected_broker.replace(' ', '_').lower()}_{datetime.now().strftime('%Y%m%d')}.txt",
                        mime="text/plain",
                    )

                with col_c:
                    if optout_url:
                        st.link_button("🔗 Official Opt-Out Form", optout_url)
                        st.caption("Opens in new tab")

                st.markdown("---")
                if st.button("➕ Log this request in the Campaign Tracker"):
                    add_request(
                        config.TRACKER_DB_PATH,
                        broker_name=selected_broker,
                        channel="Email" if broker_email else "Opt-out form",
                        response_window_days=config.CCPA_RESPONSE_WINDOW_DAYS,
                    )
                    st.success(f"Logged {selected_broker} in the tracker.")

        else:
            broker_options = brokers_df["broker_name"].tolist()
            selected_brokers = st.multiselect("Select target brokers", broker_options, default=broker_options[:3])

            if selected_brokers:
                st.markdown("---")
                st.subheader("🔍 Step 1: Confirm you're actually listed on each broker")
                st.caption(
                    "Search each broker's site for your own name before including it in the batch — "
                    "don't ask a broker to delete a record you haven't confirmed exists."
                )
                confirmed_brokers = []
                for broker_name in selected_brokers:
                    broker_info = brokers_df[brokers_df["broker_name"] == broker_name].iloc[0]
                    search_url = broker_info["search_url"]
                    row_cols = st.columns([3, 2, 3])
                    row_cols[0].markdown(f"**{broker_name}**")

                    if st.session_state.listed_confirmed.get(broker_name, False):
                        row_cols[1].caption("—")
                        row_cols[2].caption("✅ Confirmed via Should I Worry?")
                        confirmed_brokers.append(broker_name)
                        continue

                    if search_url:
                        row_cols[1].link_button("Search", search_url, key=f"batch_search_{broker_name}")
                    else:
                        row_cols[1].caption("No search link on file")
                    if row_cols[2].checkbox("Confirmed listed", key=f"batch_confirmed_{broker_name}"):
                        confirmed_brokers.append(broker_name)
                        st.session_state.listed_confirmed[broker_name] = True

                st.markdown("---")

                if not confirmed_brokers:
                    st.info("Check off at least one broker above once you've confirmed you're listed there.")
                else:
                    letters = {}
                    for broker_name in confirmed_brokers:
                        letters[broker_name] = render_letter(env, broker_name, user_name, user_location, user_email, record_url)

                    with st.expander(f"Preview ({len(letters)} letters)"):
                        for broker_name, letter_text in letters.items():
                            st.markdown(f"**{broker_name}**")
                            st.code(letter_text, language=None)

                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w") as zf:
                        for broker_name, letter_text in letters.items():
                            filename = f"ccpa_demand_{broker_name.replace(' ', '_').lower()}.txt"
                            zf.writestr(filename, letter_text)
                    zip_buffer.seek(0)

                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.download_button(
                            "📥 Download all as ZIP",
                            data=zip_buffer,
                            file_name=f"ccpa_demands_{datetime.now().strftime('%Y%m%d')}.zip",
                            mime="application/zip",
                        )
                    with col_b:
                        if st.button(f"➕ Log all {len(letters)} in the Campaign Tracker"):
                            progress_bar = st.progress(0)
                            for idx, broker_name in enumerate(confirmed_brokers):
                                broker_info = brokers_df[brokers_df["broker_name"] == broker_name].iloc[0]
                                channel = "Email" if broker_info["compliance_email"] else "Opt-out form"
                                add_request(
                                    config.TRACKER_DB_PATH,
                                    broker_name=broker_name,
                                    channel=channel,
                                    response_window_days=config.CCPA_RESPONSE_WINDOW_DAYS,
                                )
                                progress_bar.progress((idx + 1) / len(confirmed_brokers))
                            st.success(f"Logged {len(letters)} requests in the tracker.")
            else:
                st.info("Select at least one broker.")


# ---------------------------------------------------------------------------
# MODE 2: NY Expungement Guidance
# ---------------------------------------------------------------------------
elif mode == "2. NY Expungement Guidance":
    st.header("⚖️ New York Criminal Record Expungement Guidance")
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
elif mode == "3. Google De-Indexing":
    st.header("🔍 Google PII Removal Request")
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
elif mode == "4. Campaign Tracker":
    st.header("📊 Campaign Tracker")
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
