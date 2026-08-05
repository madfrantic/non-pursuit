"""
Data Broker Deletion Letters — its own module (extracted from app.py) so
its logic lives in exactly one place regardless of what else calls it.
"""
import io
import zipfile
from datetime import datetime

import streamlit as st
from jinja2 import Environment, FileSystemLoader

import config
from mailto_builder import build_mailto_link, mailto_length, is_mailto_safe
from tracker import add_request


def _is_valid_email(value: str) -> bool:
    import re
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value.strip())) if value else False


def _is_valid_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://")) if value else False


def _render_letter(env, broker_name, user_name, user_location, user_email, record_url):
    template = env.get_template("ccpa_deletion_demand.j2")
    return template.render(
        broker_name=broker_name,
        user_name=user_name,
        user_location=user_location,
        user_email=user_email,
        record_url=record_url,
        current_date=datetime.now().strftime("%B %d, %Y"),
    )


def render(brokers_df):
    st.header(":material/mail: CCPA Data Deletion Demand Letters")
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
    if user_email and not _is_valid_email(user_email):
        errors.append("That email address doesn't look valid.")
    if record_url and not _is_valid_url(record_url):
        errors.append("Record URL should start with http:// or https://")

    for e in errors:
        st.warning(e)

    ready = bool(user_name and user_email and user_location and record_url) and not errors

    st.markdown("---")

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
        return

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
            st.success(f"✅ Already confirmed via the Dashboard that you're listed on {selected_broker}.")
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
            rendered_letter = _render_letter(env, selected_broker, user_name, user_location, user_email, record_url)

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
                    row_cols[2].caption("✅ Confirmed via the Dashboard")
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
                    letters[broker_name] = _render_letter(env, broker_name, user_name, user_location, user_email, record_url)

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
