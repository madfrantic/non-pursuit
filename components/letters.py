"""
Data Broker Deletion — its own module (extracted from app.py) so
its logic lives in exactly one place regardless of what else calls it.

Reads the identity info entered on the Dashboard rather than collecting
it again -- only the record URL (specific to whichever broker/letter this
is) gets entered here.
"""
import io
import zipfile
from datetime import datetime

import streamlit as st

import config
import exposure_store
import runtime_mode
import usage_metrics
import database
from letter_compiler import compile_demand_letter
from mailto_builder import build_mailto_link, mailto_length, is_mailto_safe
from tracker import add_request
from validators import is_valid_url
import profile_state
import jurisdiction_router


def _render_letter(broker_name, user_name, user_location, user_email, record_url, template_type):
    """Thin adapter over the shared compiler -- the statutory text itself
    lives in utils/letter_compiler.py so the audit-trail ZIP renders from
    the identical code path."""
    return compile_demand_letter(
        broker_name,
        {"name": user_name, "location": user_location, "email": user_email,
         "relational_entities": (database.get_latest_target_profile(runtime_mode.db_path()) or {}).get("relational_entities", [])},
        record_url=record_url,
        template_type=template_type,
    )


def _render_jurisdiction_picker(profile):
    """Auto-route off profile_state, but let the user override the
    template. Auto-routing can be wrong -- a profile with a stale state
    field, a snowbird splitting time between two states -- and the
    consequence of guessing wrong is a letter that cites a statute that
    doesn't reach the recipient. Returns the chosen template_type."""
    auto = jurisdiction_router.route(profile)
    jurisdictions = jurisdiction_router.available_jurisdictions()
    labels = [j.label for j in jurisdictions]
    template_types = [j.template_type for j in jurisdictions]

    st.markdown("##### ⚖️ Statutory framework")
    default_index = template_types.index(auto.template_type)
    chosen_label = st.selectbox(
        "Applies to this letter",
        labels,
        index=default_index,
        help="Auto-selected from your saved location. Override if it guessed wrong.",
        key="jurisdiction_override",
    )
    chosen = jurisdictions[labels.index(chosen_label)]

    if chosen.template_type != auto.template_type:
        st.caption(f"Auto-detected **{auto.label}** from your saved profile — overridden below.")
    st.caption(f"{chosen.statute} · {chosen.response_window_days}-day statutory response window")
    st.caption(chosen.summary)
    if chosen.template_type == jurisdiction_router.NY_HYBRID:
        st.caption(jurisdiction_router.STATUTORY_WINDOW_NOTE)

    return chosen.template_type


def render(brokers_df):
    st.title("✉️ Data Broker Deletion")
    st.caption("Generate formal deletion demand letters for data brokers under California Civil Code § 1798.105.")

    profile = profile_state.get_profile(st.session_state)
    user_name = profile["full_name"]
    user_email = profile["email"]
    user_location = ", ".join(value for value in (profile["city"], profile["state"]) if value)
    relational_entities = (database.get_latest_target_profile(runtime_mode.db_path()) or {}).get("relational_entities", [])

    if not (user_name and user_email and user_location):
        st.warning("Add your name, email, and location on the **Dashboard** first -- these letters are personalized and need a real contact for the broker to respond to.")
        if st.button("👤 Go to Dashboard", type="primary"):
            st.session_state.pending_nav = "📊 Dashboard"
            st.rerun()
        return

    with st.container(border=True):
        info_cols = st.columns([3, 1])
        info_cols[0].markdown(f"**{user_name}**  \n{user_location} · {user_email}")
        if info_cols[1].button("Edit on Dashboard", icon="✏️", width="stretch"):
            st.session_state.pending_nav = "📊 Dashboard"
            st.rerun()
        record_url = st.text_input(
            "Record URL",
            value=st.session_state.record_url,
            placeholder="https://broker.com/record/...",
            help="The specific listing page for you on this broker's site -- captured automatically by Auto-search on the Master Dashboard, or pasted in manually.",
        )
    st.session_state.record_url = record_url

    if record_url and not is_valid_url(record_url):
        st.warning("Record URL should start with http:// or https://")

    ready = bool(record_url) and is_valid_url(record_url)

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
        return

    with st.container(border=True):
        template_type = _render_jurisdiction_picker(profile)

    batch_mode = st.toggle("Batch mode (select multiple brokers)", value=False)

    if not ready:
        st.info("Add a record URL above to generate letters.")

    elif not batch_mode:
        broker_options = brokers_df["broker_name"].tolist()
        selected_broker = st.selectbox("Select target data broker", broker_options)
        broker_info = brokers_df[brokers_df["broker_name"] == selected_broker].iloc[0]
        broker_email = broker_info["compliance_email"]
        optout_url = broker_info["optout_url"]
        broker_notes = broker_info["notes"]

        if broker_notes:
            st.info(broker_notes, icon="📝")

        search_url = broker_info["search_url"]
        st.subheader("🔍 Step 1: Confirm you're actually listed")

        if st.session_state.listed_confirmed.get(selected_broker, False):
            st.success(f"✅ Already confirmed on the Master Dashboard that you're listed on {selected_broker}.")
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
                exposure_store.record_check(runtime_mode.db_path(), f"broker:{selected_broker}", "Found exposure")

        if not confirmed_listed:
            st.info("Check the box above once you've confirmed you're listed to generate the letter.")
        else:
            rendered_letter = _render_letter(
                selected_broker, user_name, user_location, user_email, record_url, template_type,
            )

            st.subheader("📄 Generated demand letter")
            if relational_entities:
                with st.container(border=True):
                    st.subheader("⚖️ Statutory Relational Severance Clause (CCPA §1798.105)")
                    st.info("🛡️ Notice: Broker is legally mandated to break household cluster graphs and purge relational associate tags.")
            st.code(rendered_letter, language=None)
            st.caption("Use the copy icon in the corner above, or download below.")

            reviewed = st.checkbox(
                "🔎 I have reviewed this letter and confirm it's accurate before sending or logging it",
                key=f"reviewed_{selected_broker}_{template_type}",
            )
            if not reviewed:
                st.info("Review the letter above and check the box to unlock sending, downloading, and logging.")
                return

            # Record exactly which template this broker's letter was confirmed
            # under, so the audit-trail ZIP archives this same letter rather
            # than re-routing at export time and possibly picking a different
            # template than the one the user actually reviewed.
            broker_jurisdiction = st.session_state.setdefault("broker_jurisdiction", {})
            if broker_jurisdiction.get(selected_broker) != template_type:
                broker_jurisdiction[selected_broker] = template_type
                st.session_state.pop("audit_zip", None)

            st.subheader("🚀 Actions")

            col_a, col_b, col_c = st.columns(3)

            with col_a:
                if broker_email:
                    subject = f"CCPA Data Deletion Demand - {user_name}"
                    safe = is_mailto_safe(broker_email, subject, rendered_letter, config.MAILTO_SAFE_LENGTH)
                    mailto_link = build_mailto_link(broker_email, subject, rendered_letter)
                    st.link_button("✉️ Open email client", mailto_link, disabled=not safe)
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
                # The counter goes on the download, not on _render_letter():
                # that helper re-renders the preview on every rerun, so
                # counting there would measure widget interactions rather
                # than letters the user actually took away.
                if st.download_button(
                    label="Download as TXT",
                    icon="📥",
                    data=rendered_letter,
                    file_name=f"ccpa_demand_{selected_broker.replace(' ', '_').lower()}_{datetime.now().strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                ):
                    usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.LETTER_GENERATED)

            with col_c:
                if optout_url:
                    st.link_button("🔗 Official opt-out form", optout_url)
                    st.caption("Opens in new tab")

            if st.button("Log this request in the Campaign Tracker", icon="➕"):
                add_request(
                    runtime_mode.db_path(),
                    broker_name=selected_broker,
                    channel="Email" if broker_email else "Opt-out form",
                    response_window_days=config.CCPA_RESPONSE_WINDOW_DAYS,
                )
                usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.REQUEST_LOGGED)
                st.success(f"Logged {selected_broker} in the tracker.")

    else:
        broker_options = brokers_df["broker_name"].tolist()
        selected_brokers = st.multiselect("Select target brokers", broker_options, default=broker_options[:3])

        if selected_brokers:
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
                    row_cols[2].caption("✅ Confirmed on the Master Dashboard")
                    confirmed_brokers.append(broker_name)
                    continue

                if search_url:
                    row_cols[1].link_button("Search", search_url, key=f"batch_search_{broker_name}")
                else:
                    row_cols[1].caption("No search link on file")
                if row_cols[2].checkbox("Confirmed listed", key=f"batch_confirmed_{broker_name}"):
                    confirmed_brokers.append(broker_name)
                    st.session_state.listed_confirmed[broker_name] = True
                    exposure_store.record_check(runtime_mode.db_path(), f"broker:{broker_name}", "Found exposure")

            if not confirmed_brokers:
                st.info("Check off at least one broker above once you've confirmed you're listed there.")
            else:
                letters = {}
                for broker_name in confirmed_brokers:
                    letters[broker_name] = _render_letter(
                        broker_name, user_name, user_location, user_email, record_url, template_type,
                    )

                with st.expander(f"Preview ({len(letters)} letters)", expanded=True):
                    for broker_name, letter_text in letters.items():
                        st.markdown(f"**{broker_name}**")
                        st.code(letter_text, language=None)

                reviewed = st.checkbox(
                    f"🔎 I have reviewed all {len(letters)} letters above and confirm they're accurate "
                    "before downloading or logging them",
                    key=f"reviewed_batch_{template_type}",
                )
                if not reviewed:
                    st.info("Expand the preview above and check the box to unlock the ZIP download and tracker logging.")
                    return

                broker_jurisdiction = st.session_state.setdefault("broker_jurisdiction", {})
                if any(broker_jurisdiction.get(b) != template_type for b in confirmed_brokers):
                    for broker_name in confirmed_brokers:
                        broker_jurisdiction[broker_name] = template_type
                    st.session_state.pop("audit_zip", None)

                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zf:
                    for broker_name, letter_text in letters.items():
                        filename = f"ccpa_demand_{broker_name.replace(' ', '_').lower()}.txt"
                        zf.writestr(filename, letter_text)
                zip_buffer.seek(0)

                col_a, col_b = st.columns(2)
                with col_a:
                    # Counted per letter, not per click -- a batch of twelve
                    # is twelve demands generated, and recording it as 1
                    # would understate the batch path against the single one.
                    if st.download_button(
                        "Download all as ZIP",
                        icon="📥",
                        data=zip_buffer,
                        file_name=f"ccpa_demands_{datetime.now().strftime('%Y%m%d')}.zip",
                        mime="application/zip",
                    ):
                        usage_metrics.record_event(
                            config.USAGE_METRICS_DB_PATH,
                            usage_metrics.LETTER_GENERATED,
                            count=len(letters),
                        )
                with col_b:
                    if st.button(f"Log all {len(letters)} in the Campaign Tracker", icon="➕"):
                        progress_bar = st.progress(0)
                        for idx, broker_name in enumerate(confirmed_brokers):
                            broker_info = brokers_df[brokers_df["broker_name"] == broker_name].iloc[0]
                            channel = "Email" if broker_info["compliance_email"] else "Opt-out form"
                            add_request(
                                runtime_mode.db_path(),
                                broker_name=broker_name,
                                channel=channel,
                                response_window_days=config.CCPA_RESPONSE_WINDOW_DAYS,
                            )
                            progress_bar.progress((idx + 1) / len(confirmed_brokers))
                        usage_metrics.record_event(
                            config.USAGE_METRICS_DB_PATH,
                            usage_metrics.REQUEST_LOGGED,
                            count=len(confirmed_brokers),
                        )
                        st.success(f"Logged {len(letters)} requests in the tracker.")
        else:
            st.info("Select at least one broker.")
