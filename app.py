import base64
import sys
import os
from datetime import datetime

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
import ny_sealing
import profile_state
import presentation_mode

from components import letters as letters_component
from components import dashboard as dashboard_component
from components import master as master_component

database.init_db(runtime_mode.db_path())


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


def _image_data_uri(path):
    """Raw <img> tags inside a custom-HTML block can't reference a local
    file path directly -- base64-embedding it is the standard way to get a
    local image into markdown(unsafe_allow_html=True). Deliberately not
    cached: caching was keyed only on the path string, so editing these
    small logo files in place (as happened while iterating on crop/sizing)
    kept serving stale base64 data forever, with no visible sign why."""
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

# Custom 70s NYPD 'SIU-Light' & Giant Emojis CSS
st.markdown(
    """
    <style>
        /* Sidebar nav emoji scaling */
        [data-testid="stSidebar"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p {
            font-size: 1.2rem;
            line-height: 1.8;
        }

        /* Increase global text size in main dashboard */
        [data-testid="stMarkdownContainer"] p, 
        [data-testid="stMarkdownContainer"] h1, 
        [data-testid="stMarkdownContainer"] h2, 
        [data-testid="stMarkdownContainer"] h3 {
            font-size: 1.1rem;
        }
        
        /* Make st.metric headers, values, and deltas giant and bold */
        [data-testid="stMetricValue"] {
            font-size: 3.5rem !important;
            font-weight: bold;
            font-family: monospace;
        }
        [data-testid="stMetricLabel"] {
            font-size: 1.5rem !important;
            font-weight: bold;
        }
        [data-testid="stMetricDelta"] {
            font-size: 1.2rem !important;
        }
        
        /* TARGETING ALL APPLIED EMOJIS - GLOBAL "HUGE EMOJI" RULE */
        span:is(:has(svg, [aria-label]), :not(:has(*))) {
          font-size: 2.5rem !important;
          vertical-align: middle;
        }
        
        /* Specific bump for st.header / st.subheader with emojis */
        h1 span, h2 span, h3 span {
            font-size: 3rem !important;
            line-height: 1 !important;
        }

        /* Specific bump for metric emojis (like threat circles) */
        [data-testid="stMetricLabel"] span,
        [data-testid="stMetricValue"] span {
            font-size: 2.8rem !important;
        }

        /* Bordered Case-File Styling for Metric Containers (lighter blue) */
        [data-testid="stVerticalBlock"] > div:has(> [data-testid="stMetric"]) {
           background: linear-gradient(180deg, #243B6A 0%, #1E2F54 100%);
           border: 2px solid #314A81;
           border-top: 5px solid #D4AF37;
           border-radius: 6px;
           padding: 20px;
           margin-bottom: 25px;
           box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4);
        }

        /* Hide Streamlit default cruft */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        .block-container {padding-top: 1rem; padding-bottom: 0rem;}

        /* ==================================================================
           CINEMATIC LAYER -- 70s NYPD SIU surveillance-monitor treatment.
           Purely presentational: every rule below is either non-interactive
           (pointer-events: none) or a paint-only property, so nothing here
           can intercept a click or move a widget's hit box.
           ================================================================== */

        :root {
            --siu-brass: #D4AF37;
            --siu-navy: #0B1325;
            --siu-slate: #152238;
            --siu-serif: 'Iowan Old Style', 'Palatino Linotype', Palatino, Georgia, 'Times New Roman', serif;
            --siu-mono: 'Courier New', Courier, monospace;
        }

        /* --- 1. CRT scanline overlay -------------------------------------
           Fixed to the viewport and painted over the whole app like the
           feed on a surveillance monitor. pointer-events: none is what
           keeps the app fully clickable underneath it. */
        [data-testid="stApp"]::after,
        .stApp::after {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            z-index: 9999;
            opacity: 0.05;
            background: repeating-linear-gradient(
                180deg,
                rgba(0, 0, 0, 0.95) 0px,
                rgba(0, 0, 0, 0.95) 1px,
                transparent 1px,
                transparent 3px
            );
        }

        /* --- 2. Analog terminal flicker ----------------------------------
           Applied to the single main content wrapper rather than to every
           text node: one compositor layer instead of hundreds, and the
           dips are brief and shallow (never below 0.985) so body copy
           stays legible while reading. */
        @keyframes flicker {
            0%, 91%, 100% { opacity: 1; }
            92%           { opacity: 0.985; }
            93%           { opacity: 1; }
            96%           { opacity: 0.99; }
            97%           { opacity: 1; }
        }
        [data-testid="stMain"] .block-container {
            animation: flicker 9s linear infinite;
        }

        /* --- 3. Glowing brass borders ------------------------------------
           Bordered st.container blocks (the OSINT result cards) and the
           metric cards breathe a low brass glow. */
        @keyframes pulse-glow {
            0%, 100% {
                box-shadow: 0 0 10px rgba(212, 175, 55, 0.2),
                            0 4px 10px rgba(0, 0, 0, 0.4);
                border-color: rgba(212, 175, 55, 0.35);
            }
            50% {
                box-shadow: 0 0 22px rgba(212, 175, 55, 0.38),
                            0 4px 12px rgba(0, 0, 0, 0.45);
                border-color: rgba(212, 175, 55, 0.60);
            }
        }
        /* Streamlit 1.62 hangs st.container(border=True) off a
           stLayoutWrapper -- there is no stVerticalBlockBorderWrapper in
           this version, and a bare stVerticalBlock selector would also
           catch columns and expander bodies. The direct-child combinator
           is what makes this hit bordered containers and nothing else
           (every st.container in this repo passes border=True). The
           BorderWrapper selector is kept as a forward-compat alias. */
        [data-testid="stMain"] [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"],
        [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
            border: 1px solid rgba(212, 175, 55, 0.35);
            border-radius: 6px;
            background: linear-gradient(180deg, rgba(30, 47, 84, 0.55) 0%, rgba(17, 27, 51, 0.55) 100%);
            animation: pulse-glow 5.5s ease-in-out infinite;
        }
        /* Nested cards keep the border but drop the animation -- stacked
           pulses read as noise rather than atmosphere. */
        [data-testid="stMain"] [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"]
            [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] {
            animation: none;
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.15);
        }
        [data-testid="stVerticalBlock"] > div:has(> [data-testid="stMetric"]) {
            animation: pulse-glow 5.5s ease-in-out infinite;
        }

        /* --- 4. Dossier typography ---------------------------------------
           Serif carries the authority in headline sizes; Courier carries
           the metadata (h4/h5/h6, captions, code) the way the case-file
           banner already does. */
        [data-testid="stMain"] h1,
        [data-testid="stMain"] h2,
        [data-testid="stMain"] h3,
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            font-family: var(--siu-serif) !important;
            letter-spacing: 0.02em;
        }
        [data-testid="stMain"] h4,
        [data-testid="stMain"] h5,
        [data-testid="stMain"] h6,
        [data-testid="stSidebar"] h4,
        [data-testid="stSidebar"] h5,
        [data-testid="stSidebar"] h6 {
            font-family: var(--siu-mono) !important;
            font-size: 1.05rem !important;
            letter-spacing: 0.04em;
            color: #E8E2D4;
        }
        /* The global giant-emoji rule sizes every bare span at 2.5rem,
           which dwarfs a 1.05rem Courier sub-header. Scaled to sit on the
           cap height instead -- delete this block to restore the
           uniformly giant emojis. */
        [data-testid="stMain"] :is(h4, h5, h6) span,
        [data-testid="stSidebar"] :is(h4, h5, h6) span {
            font-size: 1.5rem !important;
        }
        /* Emoji spans inside headers must not inherit Courier -- the
           existing giant-emoji rule sizes them, this keeps them drawing
           from the system emoji font. */
        [data-testid="stMain"] :is(h1, h2, h3, h4, h5, h6) span,
        [data-testid="stSidebar"] :is(h1, h2, h3, h4, h5, h6) span {
            font-family: inherit;
        }

        /* SIU case-file banner: mono kicker, serif title, brass glow. */
        .siu-banner {
            animation: pulse-glow 5.5s ease-in-out infinite;
        }
        .siu-banner .siu-kicker,
        .siu-banner .siu-file {
            font-family: var(--siu-mono) !important;
        }
        .siu-banner .siu-title {
            font-family: var(--siu-serif) !important;
        }

        /* Dossier section headers rendered by components/master.py. */
        .siu-section {
            margin: 34px 0 14px;
            border-top: 1px solid rgba(212, 175, 55, 0.30);
            padding-top: 14px;
        }
        .siu-section .siu-section-no {
            font-family: var(--siu-mono);
            font-size: 0.72rem;
            letter-spacing: 0.28em;
            text-transform: uppercase;
            color: var(--siu-brass);
        }
        .siu-section .siu-section-name {
            font-family: var(--siu-serif);
            font-size: 1.6rem;
            font-weight: 700;
            color: #F8FAFC;
            letter-spacing: 0.02em;
            margin-top: 2px;
        }

        /* --- Accessibility guard ----------------------------------------
           Motion sensitivity outranks atmosphere: the scanlines stay
           (they are static), every animation stops. */
        @media (prefers-reduced-motion: reduce) {
            [data-testid="stMain"] .block-container,
            [data-testid="stMain"] [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"],
            [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"],
            [data-testid="stVerticalBlock"] > div:has(> [data-testid="stMetric"]),
            .siu-banner {
                animation: none !important;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


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

st.sidebar.markdown(
    f"""
    <div style="width: 100%; display: flex; align-items: center; justify-content: center; margin-bottom: 0.25rem;">
        <img src="{_image_data_uri(config.APP_WORDMARK_PATH)}" style="height: 48px;" alt="{config.APP_TITLE}">
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f'<p style="width: 100%; text-align: center; text-transform: uppercase; '
    f'font-weight: 700; font-size: 0.8rem; color: {config.TEXT_COLOR}; margin: 0;">'
    f'{config.APP_TAGLINE}</p>',
    unsafe_allow_html=True,
)
st.sidebar.markdown("---")

mode = st.sidebar.radio(
    "Select tool",
    [
        "🛡️ Profile",
        "🔍 Master Intelligence Dossier",
        "✉️ Data Broker Deletion Letters",
        "⚖️ NY Expungement Guidance",
        "🚫 Google De-Indexing",
        "📬 Opt-Out Tracker",
    ],
    key="nav_mode",
    label_visibility="visible",
)

# Presentation mode toggle for safe live demos
st.sidebar.markdown("---")
pres_enabled = st.sidebar.toggle(
    "🎭 Presentation Mode",
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
    st.markdown("### ⚙️ SYSTEM RUNTIME CONTROL")
    
    # Map friendly names to internal modes
    mode_options = {
        "Auto-Detect": "auto",
        "🖥️ Desktop (Full Power)": "desktop",
        "☁️ Online / Cloud (Passive)": "cloud"
    }
    
    current_override = runtime_mode.get_runtime_override()
    # Find active index
    index = list(mode_options.values()).index(current_override) if current_override in mode_options.values() else 0

    selected_label = st.radio(
        "Select Operating Environment:",
        options=list(mode_options.keys()),
        index=index,
        help="Manually switch between unrestricted desktop execution (700+ sites) and passive online scanning to demonstrate environment handling."
    )
    
    # Update override on selection change
    chosen_mode = mode_options[selected_label]
    if chosen_mode != current_override:
        runtime_mode.set_runtime_override(chosen_mode)
        st.rerun()

    # Display active badge
    if runtime_mode.is_cloud_deployment():
        st.warning("☁️ **ONLINE RUNTIME ACTIVE**\n- Passive Recon (Gravatar, PGP, Certs)\n- Heavy sweeps disabled (Cloud IP Guard)")
    else:
        st.success("🖥️ **DESKTOP RUNTIME ACTIVE**\n- Full Active Sweeps Enabled\n- Unrestricted 700+ site sockets")

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
            # confirmed that broker's letter in Data Broker Deletion Letters --
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

st.markdown(
    """
    <div class="siu-banner" style="
        background: linear-gradient(180deg, #1A2744 0%, #111B33 100%);
        border: 2px solid #D4AF37;
        border-radius: 4px;
        padding: 16px 20px;
        margin-bottom: 24px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    ">
        <div>
            <div class="siu-kicker" style="font-family: 'Courier New', Courier, monospace; color: #D4AF37; font-size: 0.75rem; letter-spacing: 2.5px; text-transform: uppercase;">
                NEW YORK POLICE DEPT // SPECIAL INVESTIGATIONS UNIT
            </div>
            <div class="siu-title" style="font-family: 'Georgia', serif; color: #F8FAFC; font-size: 1.75rem; font-weight: 700; letter-spacing: 1px; margin-top: 2px;">
                🛡️ NON-PURSUIT : DIVISION OF OSINT
            </div>
        </div>
        <div class="siu-file" style="text-align: right; font-family: 'Courier New', Courier, monospace; border-left: 2px solid #314A81; padding-left: 18px;">
            <div style="color: #D4AF37; font-weight: 700; font-size: 0.85rem;">FILE: CONFIDENTIAL</div>
            <div style="color: #F8FAFC; font-size: 0.7rem; letter-spacing: 1px;">DIRECTIVE § 1798 / NY-SHIELD</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)


# ---------------------------------------------------------------------------
# MODE: Dashboard
# ---------------------------------------------------------------------------
if mode == "🛡️ Profile":
    dashboard_component.render()

elif mode == "🔍 Master Intelligence Dossier":
    master_component.render(brokers_df)

elif mode == "✉️ Data Broker Deletion Letters":
    letters_component.render(brokers_df)

elif mode == "⚖️ NY Expungement Guidance":
    st.title("⚖️ New York record-sealing intake")
    st.caption("Compare your paperwork against a transparent screening calculation. This is general information, not a legal determination.")

    with st.form("ny_sealing_intake"):
        st.subheader("Case metadata")
        jurisdiction = st.text_input("Jurisdiction", value="New York State")
        court_type = st.selectbox("Court type", ["Criminal Court", "Supreme Court"])
        docket = st.text_input("Docket or indictment number")

        st.subheader("Offense details")
        penal_law = st.text_input("Penal Law section", placeholder="PL 155.25")
        offense_description = st.text_input("Offense description", placeholder="Petit Larceny")
        charge_level = st.selectbox("Charge level", ["Violation", "Misdemeanor", "Felony"])
        felony_class = st.selectbox("Felony class", ["None", "A", "B", "C", "D", "E", "I"])
        is_sex_offense = st.checkbox("Sex offense under Article 130 / COR 168-a")
        is_article_220_drug = st.checkbox("Article 220 drug felony exception applies")

        st.subheader("Timeline and current status")
        sentencing_date = st.date_input("Sentencing date", value=None)
        incarceration_served = st.checkbox("Incarceration was served")
        release_date = st.date_input("Release date", value=None, disabled=not incarceration_served)
        supervision_completed = st.checkbox("Probation/parole completed")
        completion_date = st.date_input("Supervision completion date", value=None, disabled=not supervision_completed)
        pending_ny = st.checkbox("Pending New York charges")
        pending_out_of_state = st.checkbox("Pending out-of-state felony")
        subsequent_date = st.date_input("Most recent subsequent conviction date", value=None)

        audit_targets = st.multiselect(
            "Commercial audit targets",
            ["Checkr", "Sterling", "HireRight", "LexisNexis"],
            default=["Checkr", "Sterling", "HireRight", "LexisNexis"],
        )
        submitted = st.form_submit_button("Calculate screening result", type="primary")

    if submitted:
        payload = {
            "case_metadata": {"jurisdiction": jurisdiction, "court_type": court_type, "docket_or_indictment_no": docket},
            "offense_details": {
                "penal_law_section": penal_law, "offense_description": offense_description,
                "charge_level": charge_level, "felony_class": None if felony_class == "None" else felony_class,
                "is_sex_offense": is_sex_offense, "is_article_220_drug": is_article_220_drug,
            },
            "timeline_inputs": {
                "sentencing_date": sentencing_date, "incarceration_served": incarceration_served,
                "release_date": release_date if incarceration_served else None,
                "probation_parole_completed": supervision_completed, "completion_date": completion_date,
            },
            "current_status_flags": {
                "has_pending_ny_charges": pending_ny, "has_pending_out_of_state_felony": pending_out_of_state,
                "subsequent_conviction_date": subsequent_date,
            },
        }
        result = ny_sealing.eligibility(payload)
        if result["status"] == ny_sealing.STATUS_SEALED:
            st.success(f"Screening result: {result['status']}")
        elif result["status"] == ny_sealing.STATUS_PENDING:
            st.warning(f"Screening result: {result['status']}")
        else:
            st.error(f"Screening result: {result['status']}")
        st.write(result["reason"])
        if "threshold_date" in result:
            st.write(f"Clock start: {result['start_date']} | Threshold date: {result['threshold_date']} | Days elapsed: {result['days_elapsed']}")
        st.info(result.get("implementation_window_note", "Use the official court process to verify the result."))

        st.subheader("State versus private audit")
        for instruction in ny_sealing.audit_instructions(audit_targets):
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
        st.warning("Enter your name and email on the **Dashboard** first — this request is personalized and needs a real contact for verification.")
        if st.button("👤 Go to Dashboard", type="primary"):
            st.session_state.pending_nav = "🛡️ Profile"
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
        st.info("Nothing logged yet. Generate a letter under **Data Broker Deletion Letters** and click \"Log this request\", or add one manually above.")
    else:
        overdue_count = sum(1 for r in requests_list if r["is_overdue"])
        c1, c2, c3 = st.columns(3)
        c1.metric("Total tracked", len(requests_list))
        c2.metric("Overdue", overdue_count)
        c3.metric("Complete", sum(1 for r in requests_list if r["status"] == "Complete"))

        export_cols = st.columns(4)
        open_requests = [r for r in requests_list if r["status"] != "Complete"]
        if open_requests:
            export_cols[0].download_button(
                "Calendar (.ics)",
                icon="📅",
                data=build_ics(requests_list),
                file_name="non_pursuit_deadlines.ics",
                mime="text/calendar",
                width="stretch",
            )
        export_cols[1].download_button(
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
        export_cols[2].download_button(
            "Requests (.csv)",
            icon="📋",
            data=build_csv_export(requests_list),
            file_name="non_pursuit_requests.csv",
            mime="text/csv",
            width="stretch",
            help="Just the campaign requests table, for opening in a spreadsheet.",
        )
        export_cols[3].download_button(
            "Report (.pdf)",
            icon="📄",
            data=build_pdf_export(requests_list, exposure_store.get_all_checks(runtime_mode.db_path())),
            file_name="non_pursuit_report.pdf",
            mime="application/pdf",
            width="stretch",
            help="A readable summary to hand to someone else -- an attorney, a family member helping out.",
        )

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


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(f"{config.APP_TITLE} — {config.APP_TAGLINE}. For educational purposes only. Not legal advice.", text_alignment="center")
