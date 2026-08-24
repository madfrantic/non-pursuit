import sys
import os
from datetime import date, datetime

import streamlit as st
import pandas as pd

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
UTILS_DIR = os.path.join(ROOT_DIR, "utils")
if UTILS_DIR not in sys.path:
    sys.path.append(UTILS_DIR)

from tracker import add_request, get_all_requests, update_status, delete_request, purge_expired_notes, STATUS_OPTIONS
from calendar_export import build_ics
from data_export import build_json_export, build_csv_export, build_pdf_export
import audit_packager
import database
import demo_data
import discovered_accounts
import exposure_store
import config
import runtime_mode
import usage_metrics
import ny_sealing
import profile_state
import presentation_mode

import broker_ledger
import agent_scheduler
import debug_view

from components import vault_gate
from components import letters as letters_component
from components import dashboard as dashboard_component
from components import master as master_component
from components import campaign_timeline as campaign_timeline_component
from components import footprint as footprint_component
from components import nav

st.session_state.setdefault('nav_mode', 'Vault')
database.init_db(runtime_mode.db_path())

# One session counter per browser session rather than per script run --
# Streamlit reruns the whole file on every widget interaction, so counting
# unguarded here would measure clicks, not people.
if not st.session_state.get("_usage_session_counted"):
    usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.SESSION_STARTED)
    st.session_state._usage_session_counted = True


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


# Diagnostic view. No-op unless NON_PURSUIT_DEBUG_UNSTYLE is set; injected
# before the gate so the gate itself is inspectable too.
debug_view.inject()


# ---------------------------------------------------------------------------
# Vault gate
# ---------------------------------------------------------------------------
# Nothing below this renders until the ledger is unlocked. Placed directly
# after set_page_config (which must be the first Streamlit call) and before
# any widget, so a locked session cannot paint identity data behind the
# form. Demo mode is exempt -- see components/vault_gate.py.
if not vault_gate.require_unlock():
    st.stop()


# ---------------------------------------------------------------------------
# Autonomous background engine
# ---------------------------------------------------------------------------
# @st.cache_resource is what makes this exactly one scheduler. Streamlit
# reruns this whole script on every widget interaction and once per browser
# session, so an unguarded BackgroundScheduler() here would start a new
# daemon thread pool on every click. cache_resource caches on the function,
# not the session: the body runs once per server process and every later
# rerun -- and every other browser tab -- gets that same object back.
#
# The returned scheduler is deliberately never read into session state and
# never touched from a callback. Its jobs run on their own threads and talk
# only to broker_ledger and review_queue, both of which open a connection
# per call; see utils/agent_scheduler.py for why that matters.
#
# Demo mode gets no scheduler. Its database is a per-session temp file that
# disappears, and the hosted demo build (render.yaml) runs on a shared IP
# where unattended broker sweeps are exactly what must not happen.
@st.cache_resource(show_spinner=False)
def _start_agent_engine(db_path: str, review_db_path: str):
    return agent_scheduler.build_scheduler(db_path, review_db_path)


if not runtime_mode.is_demo_mode() and config.AGENT_SCHEDULER_ENABLED:
    broker_ledger.init_ledger(runtime_mode.db_path())
    agent_engine = _start_agent_engine(
        runtime_mode.db_path(), config.REVIEW_QUEUE_DB_PATH)
else:
    agent_engine = None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
# Start each new session with a blank profile for privacy and a fresh slate.
# Users can explicitly load a saved profile from the dashboard if desired.
profile_state.ensure_profile(st.session_state, None)  # Pass None to start with blank profile
profile_state.sync_profile(st.session_state, profile_state.get_profile(st.session_state))

for key, default in {
    "record_url": "",
    "listed_confirmed": {},
    "pending_nav": None,
    "profile_saved_auto_scan": False,
    "_auto_scan_last_pair": None,
    "_auto_scan_in_progress": False,
    "master_face_image_bytes": None,
    "presentation_mode": False,
    "_session_initialized": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# Scorched-earth initialization: clear ghost data from previous sessions
if not st.session_state.get("_session_initialized"):
    st.session_state._session_initialized = True
    # Clear all potential stale OSINT/scan data
    for ghost_key in list(st.session_state.keys()):
        if any(x in ghost_key for x in ["osint", "footprint", "email_results", "audit", "exposure", "facial"]):
            st.session_state.pop(ghost_key, None)
    st.cache_data.clear()


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
    if "last_verified" not in df.columns:
        df["last_verified"] = ""
    df["last_verified"] = df["last_verified"].fillna("")
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

st.logo(config.APP_WORDMARK_PATH, icon_image=config.APP_LOGO_PATH, size="large")
st.sidebar.markdown("---")

# One radio, not three. Grouping the tools into separate per-category
# radios would read better, but every quick-action button in the app --
# and every render test -- addresses the nav through this single
# key="nav_mode" widget, and a value can only live in one radio. So the
# options stay in one list, ordered by category, and the category rides
# along as each option's caption (Streamlit draws captions under the
# option, so a true group header above each block isn't available here).
NAV_SECTIONS = [
    ("Recon & Audit", [
        "👤 Identity Profile",
        "🔍 Intelligence Dossier",
        "🕸️ Deep Handle Footprint",
    ]),
    ("Legal & Deletion", [
        "✉️ Data Broker Deletion",
        "🚫 Google De-Indexing",
        "⚖️ NY Expungement",
    ]),
    ("Tracking & Proofs", [
        "📬 Opt-Out Tracker",
        "🗓️ Deletion Timeline",
    ]),
]

NAV_OPTIONS = [label for _, labels in NAV_SECTIONS for label in labels]
NAV_CAPTIONS = [section for section, labels in NAV_SECTIONS for _ in labels]

mode = st.sidebar.radio(
    "Tools",
    NAV_OPTIONS,
    captions=NAV_CAPTIONS,
    key="nav_mode",
    label_visibility="visible",
)

# The pages/ multi-page app. Defined in components/nav.py because a page
# has to draw these too -- app.py's sidebar doesn't exist while a pages/
# script is running.
nav.render_page_links()

# Presentation mode toggle for safe live demos
st.sidebar.markdown("---")
pres_enabled = st.sidebar.toggle(
    "🎭 Presentation mode",
    value=st.session_state.presentation_mode,
    help="Enable mock data and instant scan results for live demos",
)
if pres_enabled and not st.session_state.presentation_mode:
    presentation_mode.populate_demo_profile(st.session_state)
    st.session_state.presentation_mode = True
    st.toast("🎭 Presentation mode enabled with demo profile", icon="✨")
    st.rerun()
elif not pres_enabled and st.session_state.presentation_mode:
    st.session_state.presentation_mode = False
    st.rerun()
else:
    st.session_state.presentation_mode = pres_enabled

# ---------------------------------------------------------------------------
# Sidebar: runtime mode, demo seeding, audit package
# ---------------------------------------------------------------------------
st.sidebar.markdown("---")

with st.sidebar:
    st.caption("⚙️ Runtime environment")

    # Map friendly names to internal modes
    mode_options = {
        "Auto-detect": "auto",
        "🖥️ Desktop (full power)": "desktop",
        "☁️ Online / cloud (passive)": "cloud",
    }

    current_override = runtime_mode.get_runtime_override()
    # Find active index
    index = list(mode_options.values()).index(current_override) if current_override in mode_options.values() else 0

    selected_label = st.radio(
        # Kept as the accessible name for screen readers, hidden visually.
        "Select operating environment",
        options=list(mode_options.keys()),
        index=index,
        label_visibility="collapsed",
        help="Manually switch between unrestricted desktop execution (700+ sites) and passive online scanning to demonstrate environment handling."
    )

    # Update override on selection change
    chosen_mode = mode_options[selected_label]
    if chosen_mode != current_override:
        runtime_mode.set_runtime_override(chosen_mode)
        st.rerun()

    # Display active badge
    if runtime_mode.is_cloud_deployment():
        st.warning("☁️ **Online runtime active**\n- Passive recon only (Gravatar, PGP, certificate transparency)\n- Heavy sweeps disabled to protect the shared IP")
    else:
        st.success("🖥️ **Desktop runtime active**\n- Full active sweeps enabled\n- Unrestricted 700+ site checks")

vault_gate.render_lock_control()

if runtime_mode.is_demo_mode():
    if st.sidebar.button("⚡ Load presentation demo", width="stretch", type="primary",
                         help="Seed this session with a synthetic campaign already in progress."):
        seeded = demo_data.seed_demo_campaign(runtime_mode.db_path())
        database.insert_target_profile(runtime_mode.db_path(), demo_data.DEMO_PROFILE)
        demo_city, _, demo_state = demo_data.DEMO_LOCATION.partition(", ")
        profile_state.sync_profile(st.session_state, {
            "full_name": demo_data.DEMO_NAME,
            "email": demo_data.DEMO_EMAIL,
            "city": demo_city,
            "state": demo_state,
            "handle": "",
            "domain": "",
        })
        st.session_state.record_url = demo_data.DEMO_RECORD_URL
        st.session_state.demo_loaded = True
        usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.DEMO_SEEDED)
        st.toast(
            f"Loaded {seeded['requests']} requests and {seeded['accounts']} accounts.",
            icon="⚡",
        )
        st.rerun()

# The audit package is assembled entirely in memory, so it works
# identically on the local build and a hosted container with no writable
# disk. Built on demand rather than every rerun -- it compiles a letter per
# broker, which is wasted work on every unrelated widget interaction.
_audit_requests = get_all_requests(runtime_mode.db_path())
if _audit_requests:
    if st.sidebar.button("📦 Download Complete Audit Trail (.ZIP)", width="stretch",
                         help="Bundle demands, deadlines and verification logs into one archive."):
        usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.AUDIT_PACKAGE_BUILT)
        _routing_profile = profile_state.get_profile(st.session_state)
        st.session_state.audit_zip = audit_packager.build_audit_package(
            requests=_audit_requests,
            discovered=discovered_accounts.get_all(runtime_mode.db_path()),
            exposure_checks=exposure_store.get_all_checks(runtime_mode.db_path()),
            target_profile={
                "name": st.session_state.user_name,
                "location": st.session_state.user_location,
                "email": st.session_state.user_email,
                # state/country drive jurisdiction routing in audit_packager --
                # unused by the letter template itself, so carrying them here
                # is harmless for any code path that doesn't route.
                "state": _routing_profile["state"],
                "country": _routing_profile["country"],
                "relational_entities": (database.get_latest_target_profile(runtime_mode.db_path()) or {}).get("relational_entities", []),
            },
            record_url=st.session_state.record_url,
            default_window=config.CCPA_RESPONSE_WINDOW_DAYS,
            osint_findings=(
                st.session_state.get("osint_findings")
                if st.session_state.get("osint_findings_appended") else None
            ),
            # Per-broker template_type recorded when the user reviewed and
            # confirmed that broker's letter in Data Broker Deletion --
            # keeps the archived letter byte-for-byte identical to what was
            # actually reviewed, rather than re-routed fresh at export time.
            broker_template_types=st.session_state.get("broker_jurisdiction", {}),
        )

    if st.session_state.get("audit_zip"):
        st.sidebar.download_button(
            "📥 Download Complete Audit Trail",
            data=st.session_state.audit_zip,
            file_name=f"non_pursuit_audit_{datetime.now().strftime('%Y%m%d')}.zip",
            mime="application/zip",
            width="stretch",
        )

# Feature-usage figures for the showcase. Collapsed by default so it isn't
# competing for attention during a walkthrough. These are event counts only
# -- see utils/usage_metrics.py for why nothing a user typed can appear here.
with st.sidebar.expander("📈 Usage", expanded=False):
    for _label, _count in usage_metrics.summary(config.USAGE_METRICS_DB_PATH):
        st.markdown(f"{_label} &nbsp;**{_count}**", unsafe_allow_html=True)
    st.caption("Feature counts only — no entered values are recorded.")


# ---------------------------------------------------------------------------
# MODE: Identity Profile
# ---------------------------------------------------------------------------
if mode == "👤 Identity Profile":
    dashboard_component.render()

elif mode == "🔍 Intelligence Dossier":
    master_component.render(brokers_df)

elif mode == "🕸️ Deep Handle Footprint":
    footprint_component.render()

elif mode == "✉️ Data Broker Deletion":
    letters_component.render(brokers_df)

elif mode == "⚖️ NY Expungement":
    st.title("⚖️ New York record-sealing screening")
    st.caption("Screens one conviction against all five New York sealing pathways, then drafts the CPL 160.59 motion. Guidance, not a legal determination.")

    with st.expander("How the five pathways differ", icon="📖"):
        st.markdown(
            """
            | Statute | Trigger | Reaches | Seal |
            | --- | --- | --- | --- |
            | **CPL 160.50** | Automatic | Dismissals, acquittals | Full |
            | **CPL 160.55** | Automatic | Violations, traffic infractions | **Partial** — court file stays public |
            | **CPL 160.57** | Automatic (Clean Slate) | Misdemeanours (3 yr), felonies (8 yr) | Full |
            | **CPL 160.58** | Petition | Drug convictions after diversion/DTAP | Conditional — a new arrest unseals |
            | **CPL 160.59** | Petition | ≤2 convictions, ≤1 felony, 10 yr | Full, at the judge's discretion |

            The two clocks run differently on purpose. Clean Slate measures from **release**
            from incarceration; CPL 160.59 measures ten years from **sentencing** and then
            excludes time served, which pushes the filing date later.
            """
        )

    with st.form("ny_sealing_intake"):
        st.subheader("Applicant")
        ac = st.columns(3)
        applicant_name = ac[0].text_input("Full name", value=st.session_state.get("user_name", ""))
        applicant_aka = ac[1].text_input("AKA(s)")
        applicant_nysid = ac[2].text_input("NYSID", placeholder="Optional")
        ac2 = st.columns(3)
        applicant_dob = ac2[0].date_input("Date of birth", value=None, min_value=date(1920, 1, 1))
        applicant_address = ac2[1].text_input("Street address")
        applicant_csz = ac2[2].text_input("City, State ZIP")

        st.subheader("Case")
        cc = st.columns(3)
        docket = cc[0].text_input("Docket or indictment number")
        court_name = cc[1].text_input("Court name", placeholder="NY County Criminal Court")
        county = cc[2].text_input("County", placeholder="New York")

        st.subheader("Offense")
        oc = st.columns(3)
        charge_level = oc[0].selectbox("Charge level", ny_sealing.CHARGE_LEVELS, index=3)
        penal_law = oc[1].text_input("Penal Law section", placeholder="PL 155.25")
        offense_description = oc[2].text_input("Offense description", placeholder="Petit Larceny")
        oc2 = st.columns(3)
        felony_class = oc2[0].selectbox("Felony class", ["None", *ny_sealing.FELONY_CLASSES])
        sentence_term = oc2[1].text_input("Sentence term", placeholder="Time served")
        is_out_of_state = oc2[2].checkbox("Conviction outside New York")

        ex = st.columns(4)
        is_sex_offense = ex[0].checkbox("Article 130 sex offense")
        requires_sora = ex[1].checkbox("SORA registration required")
        is_violent_felony = ex[2].checkbox("Violent felony (PL 70.02)")
        is_attempt = ex[3].checkbox("Attempt / conspiracy")

        st.subheader("Timeline")
        tc = st.columns(3)
        conviction_date = tc[0].date_input("Conviction date", value=None)
        sentencing_date = tc[1].date_input("Sentencing date", value=None)
        incarceration_served = tc[2].checkbox("Incarceration was served")
        tc2 = st.columns(3)
        release_date = tc2[0].date_input("Release date", value=None, disabled=not incarceration_served)
        supervision_completed = tc2[1].checkbox("Probation/parole completed", value=True)
        conditional_discharge = tc2[2].checkbox("Conditional discharge imposed")

        st.subheader("Current status")
        sc = st.columns(4)
        pending_ny = sc[0].checkbox("Pending NY charges")
        pending_out_of_state = sc[1].checkbox("Pending out-of-state felony")
        total_convictions = sc[2].number_input("Total convictions", min_value=0, max_value=20, value=1)
        total_felonies = sc[3].number_input("Of those, felonies", min_value=0, max_value=20, value=0)
        subsequent_date = st.date_input("Most recent subsequent conviction date", value=None)
        diversion_completed = st.checkbox("Judicial diversion or DTAP completed (CPL 160.58)")

        st.subheader("Reasons for the court (CPL 160.59 affidavit)")
        discretionary_factors = st.text_area(
            "Why should the court grant sealing?",
            placeholder="Rehabilitation, employment, education, community ties, letters of support.",
            height=90, label_visibility="collapsed")

        audit_targets = st.multiselect(
            "Commercial audit targets",
            ["Checkr", "Sterling", "HireRight", "LexisNexis"],
            default=["Checkr", "Sterling", "HireRight", "LexisNexis"])
        submitted = st.form_submit_button("Run screening", type="primary")

    if submitted:
        case_payload = {
            "case_metadata": {
                "docket_or_indictment_no": docket, "court_name": court_name, "county": county,
            },
            "offense_details": {
                "charge_level": charge_level,
                "penal_law_section": penal_law,
                "offense_description": offense_description,
                "felony_class": None if felony_class == "None" else felony_class,
                "is_sex_offense": is_sex_offense or None,
                "requires_sora": requires_sora,
                "is_violent_felony": is_violent_felony,
                "is_attempt": is_attempt,
                "is_out_of_state": is_out_of_state,
            },
            "timeline_inputs": {
                "conviction_date": conviction_date,
                "sentencing_date": sentencing_date,
                "sentence_term": sentence_term,
                "incarceration_served": incarceration_served,
                "release_date": release_date if incarceration_served else None,
                "probation_parole_completed": supervision_completed,
                "conditional_discharge_imposed": conditional_discharge,
                "diversion_or_dtap_completed": diversion_completed,
            },
            "current_status_flags": {
                "has_pending_ny_charges": pending_ny,
                "has_pending_out_of_state_felony": pending_out_of_state,
                "subsequent_conviction_date": subsequent_date,
                "total_convictions": total_convictions,
                "total_felony_convictions": total_felonies,
            },
        }

        screening = ny_sealing.screen(case_payload)
        usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.SEALING_SCREENED)

        headline = screening.headline
        if headline is None:
            st.error("Enter a charge level to screen this record.")
        else:
            _TONE = {
                ny_sealing.STATUS_SEALED: st.success,
                ny_sealing.STATUS_ELIGIBLE_TO_PETITION: st.success,
                ny_sealing.STATUS_PENDING: st.warning,
                ny_sealing.STATUS_DISQUALIFIED: st.error,
                ny_sealing.STATUS_NOT_APPLICABLE: st.info,
            }
            _TONE[headline.status](
                f"**{headline.statute} — {headline.status}** · {headline.label}")
            st.write(headline.reason)
            if headline.seal_scope == ny_sealing.SCOPE_PARTIAL:
                st.warning(
                    "This is a **partial** seal. DCJS, police and prosecutor records are "
                    "sealed; the court file is not, so the conviction stays findable in "
                    "court records.", icon="⚠️")
            elif headline.seal_scope == ny_sealing.SCOPE_CONDITIONAL:
                st.warning(
                    "This seal is **conditional** — a later misdemeanour or felony arrest "
                    "unseals it.", icon="⚠️")

            st.subheader("Every pathway")
            st.dataframe(
                [
                    {
                        "Statute": p.statute,
                        "Pathway": p.label,
                        "Trigger": p.mechanism.title(),
                        "Status": p.status,
                        "Seal": p.seal_scope.title(),
                        "Earliest date": str(p.threshold_date or "—"),
                        "Why": p.reason,
                    }
                    for p in screening.pathways
                ],
                width="stretch", hide_index=True)

            for note in screening.charge.classification_notes:
                st.caption(f"Classification: {note}")

            st.caption(
                "Statutory clock arithmetic in this screen has not had the manual "
                "sign-off CLAUDE.md requires for deadline math. Verify every date "
                "against the Certificate of Disposition.")

            petition = screening.by_statute(ny_sealing.CPL_160_59)
            if petition and petition.applicable and petition.status != ny_sealing.STATUS_DISQUALIFIED:
                st.subheader("CPL 160.59 motion")
                record = ny_sealing.build_record(
                    {
                        "name": applicant_name, "aka": applicant_aka,
                        "nysid": applicant_nysid, "dob": applicant_dob,
                        "address": applicant_address, "city_state_zip": applicant_csz,
                        "email": st.session_state.get("user_email", ""),
                    },
                    [case_payload],
                    discretionary_factors=discretionary_factors,
                )
                if record.blocking_issues:
                    st.warning("**Before filing, resolve:**\n\n" + "\n".join(
                        f"- {issue}" for issue in record.blocking_issues))
                if record.venue:
                    st.write(f"**File in:** {record.venue}")
                if record.service_counties:
                    st.write("**Serve the District Attorney of:** "
                             + ", ".join(record.service_counties))
                st.download_button(
                    "⬇️ Download draft motion (PDF)",
                    data=ny_sealing.fill_motion(record),
                    file_name=f"cpl-160-59-motion-{(docket or 'draft').replace('/', '-')}.pdf",
                    mime="application/pdf",
                    type="primary")
                st.caption(
                    "A draft for checking your answers — not the court's form. File on the "
                    "official Notice of Motion published by the NYS Unified Court System.")

            st.subheader("State versus private audit")
            for instruction in ny_sealing.audit_instructions(audit_targets, screening):
                st.write(f"- {instruction}")

            st.subheader("Employment questionnaire script")
            st.code("I have no reportable conviction that is legally required to be disclosed for this question. Please evaluate any record under New York law and provide the report and basis for any adverse action.")

            st.subheader("Commercial-report dispute language")
            st.code("I dispute the completeness and accuracy of the criminal-record information reported about me. Please reinvestigate under 15 U.S.C. § 1681e(b), delete information that is inaccurate, incomplete, sealed, or not legally reportable, and provide the results and source of your investigation. New York Human Rights Law § 296(16) also restricts discriminatory use of criminal-history information.")

    st.link_button("Official NY courts guide", config.NY_COURT_EXPUNGEMENT_URL, icon="📚")
    st.info("Verify every date and disposition against the Certificate of Disposition and DCJS record. Consult a qualified New York criminal-defense attorney for case-specific advice.", icon="⚠️")


# ---------------------------------------------------------------------------
# MODE 3: Google De-Indexing
# ---------------------------------------------------------------------------
elif mode == "🚫 Google De-Indexing":
    st.title("🚫 Google PII removal request")
    st.caption("Request removal of personally identifiable information from Google Search results.")

    g_name = st.session_state.user_name
    g_email = st.session_state.user_email

    if not g_name or not g_email:
        with st.container(border=True):
            st.markdown("#### 📝 Start with your identity details")
            st.caption(
                "Google's PII removal request is personalised — it needs your real name and a "
                "contact address they can verify the request against."
            )
            if st.button("👤 Enter your profile details", type="primary"):
                st.session_state.pending_nav = "👤 Identity Profile"
                st.rerun()
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

        st.subheader("Standardized PII justification statement")

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

    st.subheader("Submit to Google")
    st.link_button("Google PII removal portal", config.GOOGLE_PII_REMOVAL_URL, icon="🔗")
    st.caption("Opens Google's official removal request form")

    with st.expander("Before submitting", icon="✅"):
        st.markdown(
            """
            **Required information:**
            - URLs of the pages containing your PII
            - Screenshots of the search results
            - Your contact information for verification
            - Specific type of PII (address, phone, SSN, etc.)

            **Processing time:**
            - Google typically reviews requests within a few days
            - You will receive email confirmation of the decision
            - Approved removals take effect within 24-48 hours

            **Important notes:**
            - This only removes content from Google Search, not the original website
            - Contact the website hosting the information directly for complete removal
            - Keep records of your submission for follow-up
            """
        )


# ---------------------------------------------------------------------------
# MODE 4: Campaign Tracker
# ---------------------------------------------------------------------------
elif mode == "📬 Opt-Out Tracker":
    st.title("📈 Campaign tracker")
    st.caption("Every request logged from the other tools shows up here, with its response deadline tracked automatically.")

    with st.expander("Log a request manually", icon="➕"):
        with st.form("manual_log_form"):
            m_broker = st.text_input("Broker / recipient name")
            m_channel = st.selectbox("Channel", ["Email", "Opt-out form", "Mail", "Other"])
            m_window = st.number_input("Response window (days)", min_value=1, value=config.CCPA_RESPONSE_WINDOW_DAYS)
            m_notes = st.text_input("Notes (optional)")
            submitted = st.form_submit_button("Log request")
            if submitted and m_broker:
                add_request(runtime_mode.db_path(), m_broker, m_channel, int(m_window), m_notes)
                usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.REQUEST_LOGGED)
                st.success(f"Logged {m_broker}.")

    cleared_count = purge_expired_notes(runtime_mode.db_path(), config.PII_RETENTION_DAYS)
    if cleared_count:
        st.toast(f"Cleared notes on {cleared_count} request(s) completed over {config.PII_RETENTION_DAYS} days ago.", icon="🗑️")

    requests_list = get_all_requests(runtime_mode.db_path())

    st.caption(
        f"🔒 Notes on completed requests are cleared automatically after {config.PII_RETENTION_DAYS} days — "
        "this app doesn't hold onto your data forever."
    )

    if not requests_list:
        with st.container(border=True):
            st.markdown("#### 📭 No deletion requests logged yet")
            st.caption(
                "Requests land here automatically once you generate a demand letter and log it — "
                "each one starts its own statutory response countdown. You can also add one by "
                "hand with **Log a request manually** above."
            )
            if st.button("✉️ Generate your first demand letter", type="primary"):
                st.session_state.pending_nav = "✉️ Data Broker Deletion"
                st.rerun()
    else:
        overdue_count = sum(1 for r in requests_list if r["is_overdue"])
        c1, c2, c3 = st.columns(3)
        c1.metric("Total tracked", len(requests_list))
        c2.metric("Overdue", overdue_count)
        c3.metric("Complete", sum(1 for r in requests_list if r["status"] == "Complete"))

        export_cols = st.columns(4)
        open_requests = [r for r in requests_list if r["status"] != "Complete"]
        # Each download_button returns True only on the run where it was
        # clicked, so these collect into one counter increment rather than
        # four near-identical record_event calls.
        _exported = False
        if open_requests:
            _exported |= export_cols[0].download_button(
                "Calendar (.ics)",
                icon="📅",
                data=build_ics(requests_list),
                file_name="non_pursuit_deadlines.ics",
                mime="text/calendar",
                width="stretch",
            )
        _exported |= export_cols[1].download_button(
            "All data (.json)",
            icon="📥",
            data=build_json_export(
                requests_list,
                exposure_store.get_all_checks(runtime_mode.db_path()),
                discovered_accounts.get_all(runtime_mode.db_path()),
            ),
            file_name="non_pursuit_data_export.json",
            mime="application/json",
            width="stretch",
            help="Everything tracked in this app -- campaign requests and self-search history -- as one portable file you control.",
        )
        _exported |= export_cols[2].download_button(
            "Requests (.csv)",
            icon="📋",
            data=build_csv_export(requests_list),
            file_name="non_pursuit_requests.csv",
            mime="text/csv",
            width="stretch",
            help="Just the campaign requests table, for opening in a spreadsheet.",
        )
        _exported |= export_cols[3].download_button(
            "Report (.pdf)",
            icon="📄",
            data=build_pdf_export(requests_list, exposure_store.get_all_checks(runtime_mode.db_path())),
            file_name="non_pursuit_report.pdf",
            mime="application/pdf",
            width="stretch",
            help="A readable summary to hand to someone else -- an attorney, a family member helping out.",
        )
        if _exported:
            usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.EXPORT_DOWNLOADED)

        for r in requests_list:
            cols = st.columns([3, 2, 2, 2, 2, 1])
            cols[0].markdown(f"**{r['broker_name']}**  \n{r['channel']}")
            cols[1].markdown(f"Sent: {r['date_sent']}")
            deadline_label = f"Due: {r['deadline']}"
            if r["is_overdue"]:
                cols[2].markdown(f":red[🚨 Overdue ({deadline_label})]")
            else:
                cols[2].markdown(f"{deadline_label} ({r['days_remaining']}d left)")

            new_status = cols[3].selectbox(
                "Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(r["status"]),
                key=f"status_{r['id']}", label_visibility="collapsed",
            )
            if new_status != r["status"]:
                update_status(runtime_mode.db_path(), r["id"], new_status)
                st.rerun()

            if r["notes"]:
                cols[4].caption(r["notes"])

            if cols[5].button("", icon="🗑️", key=f"delete_{r['id']}", help=f"Delete {r['broker_name']}"):
                delete_request(runtime_mode.db_path(), r["id"])
                st.rerun()


elif mode == "🗓️ Deletion Timeline":
    campaign_timeline_component.render()


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(f"{config.APP_TITLE} — {config.APP_TAGLINE} For educational purposes only. Not legal advice.", text_alignment="center")
