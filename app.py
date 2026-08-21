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
import usage_metrics
import ny_sealing
import profile_state
import presentation_mode

from components import letters as letters_component
from components import dashboard as dashboard_component
from components import master as master_component

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


# ---------------------------------------------------------------------------
# SIU design system
# ---------------------------------------------------------------------------
# One injected stylesheet, sharing its palette and type scale with
# demo_pitch.html so the live app and the pitch deck read as the same
# artifact. Everything here is presentational: paint-only properties, or
# non-interactive overlays pinned with pointer-events: none. Nothing can
# intercept a click or move a widget's hit box.
#
# TYPOGRAPHY RESET: every custom font-size / font-family / text-transform /
# line-height override has been stripped from this block, so all text falls
# back to Streamlit's default scale and font stack. Colour, tracking, weight
# and the cinematic layer are untouched.
#
# The two emoji rules below are the deliberate exception. Headers across the
# app carry decorative emoji (st.title("⚖️ ..."), st.markdown("##### 🧬 ...")),
# and they are scaled in *em* so they track whatever size the rebuild gives
# their header instead of fighting it. Keep them relative -- an earlier
# revision used a blanket 2.5rem on every leaf <span>, which also hit inline
# code, status badges and caption fragments.
st.markdown(
    """
    <style>
        /* ---------------------------------------------------------------
           PALETTE + TYPE STACKS  (mirrors demo_pitch.html)

           The two type stacks are intentionally kept but currently
           unreferenced -- nothing in this block sets font-family any more.
           They are the starting point for the rebuilt scale.
           --------------------------------------------------------------- */
        :root {
            --siu-navy:     #0B1325;
            --siu-slate:    #152238;
            --siu-brass:    #D4AF37;
            --siu-brass-dim:#8A7328;
            --siu-bone:     #E8E2D4;
            --siu-bone-dim: #9AA3B2;
            --siu-evidence: #FF3B30;

            --siu-serif: 'Iowan Old Style', 'Palatino Linotype', Palatino,
                         'Book Antiqua', Georgia, 'Times New Roman', serif;
            --siu-mono:  'Courier New', Courier, monospace;
        }

        /* ---------------------------------------------------------------
           1. BASE TEXT -- colour only. Sizing and family are Streamlit's.
           --------------------------------------------------------------- */
        html, body, [data-testid="stAppViewContainer"] {
            background-color: var(--siu-navy);
        }
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] li {
            color: var(--siu-bone);
        }

        /* Headline colour + the brass glow on h1. No scale of our own. */
        [data-testid="stMain"] h1 {
            font-weight: 700;
            letter-spacing: 0.01em;
            color: var(--siu-brass);
            text-shadow: 0 2px 0 rgba(0, 0, 0, 0.45), 0 0 48px rgba(212, 175, 55, 0.18);
            margin-bottom: 0.2em;
        }
        [data-testid="stMain"] h2 {
            font-weight: 700;
            color: var(--siu-bone);
        }
        [data-testid="stMain"] h3 {
            font-weight: 700;
            color: var(--siu-bone);
        }
        /* Sub-headers stay metadata-brass, tracked out. */
        [data-testid="stMain"] h4,
        [data-testid="stMain"] h5,
        [data-testid="stMain"] h6 {
            font-weight: 700;
            letter-spacing: 0.10em;
            color: var(--siu-brass);
        }
        /* KEPT (see module comment): emoji ride their header. Relative
           units, so these survive whatever scale replaces the old one. */
        [data-testid="stMain"] :is(h1, h2, h3) span { font-size: 1.05em !important; }
        [data-testid="stMain"] :is(h4, h5, h6) span { font-size: 1.35em !important; }

        [data-testid="stMain"] [data-testid="stCaptionContainer"],
        [data-testid="stMain"] [data-testid="stCaptionContainer"] p {
            letter-spacing: 0.03em;
            color: var(--siu-bone-dim) !important;
        }
        /* Inline code and telemetry readouts: brass on a brass wash. The
           browser's own monospace default carries the family. */
        [data-testid="stMain"] code,
        [data-testid="stMain"] kbd,
        [data-testid="stMain"] pre {
            color: var(--siu-brass);
            background: rgba(212, 175, 55, 0.07);
        }

        /* ---------------------------------------------------------------
           2. SIDEBAR -- the terminal beside the case file.

           The blanket `[data-testid="stSidebar"] *:not([data-testid=
           "stIconMaterial"])` Courier rule is gone with the rest of the
           font-family overrides. Its :not() was load-bearing and must come
           back with it: Streamlit draws its icons as Material Symbols
           ligatures -- <span>keyboard_double_arrow_left</span> rendered by
           the icon font -- so forcing a font onto every descendant prints
           the ligature names as literal text. Nothing sets a family here
           now, so the icons render correctly on their own.
           --------------------------------------------------------------- */
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, var(--siu-slate) 0%, var(--siu-navy) 100%);
            border-right: 1px solid rgba(212, 175, 55, 0.28);
        }
        [data-testid="stSidebar"] :is(h1, h2, h3, h4, h5, h6) {
            font-weight: 700;
            letter-spacing: 0.20em;
            color: var(--siu-brass) !important;
        }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
            letter-spacing: 0.02em;
            color: var(--siu-bone);
        }
        /* No colour override on sidebar captions. The only one is the
           SYSTEM RUNTIME CONTROL label, which is deliberately left on
           Streamlit's own caption colour so it sits at the same baseline
           as the widget labels around it rather than being painted. */
        /* The nav radio's own label sits flush with the sidebar padding,
           while every option below it is indented by its radio button --
           measured at 30px vs 54px, so the TOOLS emoji hung 24px to the
           left of the option emoji it should line up with. Scoped by the
           widget key (key="nav_mode") so no other widget label shifts. */
        [data-testid="stSidebar"] .st-key-nav_mode [data-testid="stWidgetLabel"] {
            padding-left: 24px;
        }
        /* KEPT (see module comment): sidebar nav emoji, scaled in em. */
        [data-testid="stSidebar"] :is(h1, h2, h3, h4, h5, h6) span,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p span {
            font-size: 1.25em !important;
            vertical-align: -0.08em;
        }

        /* ---------------------------------------------------------------
           3. METRICS -- brass numerals, tabular figures so the digits stop
              shifting on rerun. Default Streamlit sizing.
           --------------------------------------------------------------- */
        [data-testid="stMetricValue"] {
            font-weight: 700;
            color: var(--siu-brass);
            font-variant-numeric: tabular-nums;
        }
        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] p {
            font-weight: 700;
            letter-spacing: 0.16em;
            color: var(--siu-bone-dim) !important;
        }
        [data-testid="stMetricDelta"] {
            letter-spacing: 0.08em;
        }

        /* ---------------------------------------------------------------
           4. CARDS -- brass edge and the deck's glow.
           --------------------------------------------------------------- */
        @keyframes pulse-glow {
            0%, 100% {
                box-shadow: 0 0 10px rgba(212, 175, 55, 0.2),
                            0 4px 10px rgba(0, 0, 0, 0.4);
                border-color: rgba(212, 175, 55, 0.40);
            }
            50% {
                box-shadow: 0 0 22px rgba(212, 175, 55, 0.38),
                            0 4px 12px rgba(0, 0, 0, 0.45);
                border-color: rgba(212, 175, 55, 0.72);
            }
        }
        /* Streamlit 1.62 hangs st.container(border=True) off a
           stLayoutWrapper -- there is no stVerticalBlockBorderWrapper in
           this version, and a bare stVerticalBlock selector would also
           catch columns and expander bodies. The direct-child combinator
           is what keeps this on bordered containers only (every
           st.container in this repo passes border=True). The BorderWrapper
           selector is kept as a forward-compat alias. */
        [data-testid="stMain"] [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"],
        [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
            border: 1px solid var(--siu-brass);
            border-radius: 4px;
            padding: 20px 22px;
            background: linear-gradient(180deg, rgba(21, 34, 56, 0.92) 0%, rgba(11, 19, 37, 0.92) 100%);
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.2);
            animation: pulse-glow 5.5s ease-in-out infinite;
        }
        /* Nested cards keep the edge but drop the animation -- stacked
           pulses read as noise rather than atmosphere. */
        [data-testid="stMain"] [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"]
            [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] {
            animation: none;
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.15);
        }
        /* Metric cards: same edge, brass tab across the top. */
        [data-testid="stVerticalBlock"] > div:has(> [data-testid="stMetric"]) {
            background: linear-gradient(180deg, rgba(21, 34, 56, 0.95) 0%, rgba(11, 19, 37, 0.95) 100%);
            border: 1px solid var(--siu-brass);
            border-top: 4px solid var(--siu-brass);
            border-radius: 4px;
            padding: 18px 20px;
            margin-bottom: 22px;
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.2);
            animation: pulse-glow 5.5s ease-in-out infinite;
        }

        /* ---------------------------------------------------------------
           5. CONTROLS -- brass, tracked out like console keys.
           --------------------------------------------------------------- */
        /* st.link_button renders an <a>, not a <button> -- it needs to be
           in this list for the "Run" links beside the dork queries to pick
           up the same treatment as every real button. */
        [data-testid="stMain"] button,
        [data-testid="stMain"] [data-testid="stLinkButton"] a,
        [data-testid="stSidebar"] button,
        [data-testid="stSidebar"] [data-testid="stLinkButton"] a {
            font-weight: 700;
            letter-spacing: 0.12em;
            border-radius: 3px;
        }
        [data-testid="stMain"] label p {
            letter-spacing: 0.10em;
            color: var(--siu-bone-dim) !important;
        }
        [data-testid="stMain"] hr {
            border-color: rgba(212, 175, 55, 0.30);
        }

        /* Terminal command buttons -- the Google dork vectors in the
           Master Dossier. Streamlit stamps a widget's key onto its
           container as st-key-<key>, which is the only hook a widget
           gives us; components/master.py keys every dork button
           dork_sweep_global / dork_run_<broker> so this selector can
           reach them without touching every link button in the app. */
        [class*="st-key-dork_"] a {
            letter-spacing: 0.14em;
            color: var(--siu-brass) !important;
            background: rgba(212, 175, 55, 0.06) !important;
            border: 1px solid rgba(212, 175, 55, 0.45) !important;
            border-radius: 2px;
            transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
        }
        /* Streamlit puts the label in a <p> inside the anchor, and the
           markdown-paragraph rule above is more specific than an anchor
           selector -- without this the commands render in bone, not brass. */
        [class*="st-key-dork_"] a p {
            font-weight: 700;
            letter-spacing: inherit;
            color: inherit !important;
        }
        [class*="st-key-dork_"] a:hover {
            background: rgba(212, 175, 55, 0.16) !important;
            border-color: var(--siu-brass) !important;
            box-shadow: 0 0 14px rgba(212, 175, 55, 0.35);
            color: var(--siu-bone) !important;
        }
        /* The global sweep is the primary action: heavier edge, wider
           tracking, and the brass glow already used on the case cards. */
        .st-key-dork_sweep_global a {
            border-width: 2px !important;
            letter-spacing: 0.22em;
            padding: 0.6rem 1rem;
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.2);
        }

        /* Blackout submit -- the Profile form's "Save & Execute Master
           Recon". type="primary" paints it brass-on-navy by default; this
           inverts it to brass-on-black. Scoped by the widget key
           (key="save_master_recon") so no other primary button changes.
           The nested selectors cover the label too: Streamlit renders it
           in a <p> inside the <button>, which would otherwise keep the
           primary button's own text colour. */
        .st-key-save_master_recon button,
        .st-key-save_master_recon button:hover,
        .st-key-save_master_recon button:focus,
        .st-key-save_master_recon button:active {
            background-color: #000000 !important;
            color: #D4AF37 !important;
            border: 1px solid #D4AF37 !important;
        }
        .st-key-save_master_recon button p,
        .st-key-save_master_recon button div,
        .st-key-save_master_recon button span {
            color: #D4AF37 !important;
        }

        /* ---------------------------------------------------------------
           6. CINEMATIC LAYER -- CRT scanlines, terminal flicker, vignette.
              Every layer is pointer-events: none, so the app underneath
              stays fully clickable.
           --------------------------------------------------------------- */
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
        /* Vignette: the deck's lens falloff, painted under the scanlines. */
        [data-testid="stApp"]::before,
        .stApp::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            z-index: 9998;
            background: radial-gradient(120% 100% at 50% 40%,
                        transparent 45%, rgba(0, 0, 0, 0.32) 82%, rgba(0, 0, 0, 0.62) 100%);
        }
        /* Flicker rides the single main content wrapper rather than every
           text node: one compositor layer instead of hundreds, and the
           dips never fall below 0.985 so body copy stays legible. */
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

        /* ---------------------------------------------------------------
           7. NAMED COMPONENTS -- case-file banner (app.py) and dossier
              section rules (components/master.py).
           --------------------------------------------------------------- */
        .siu-banner {
            animation: pulse-glow 5.5s ease-in-out infinite;
        }

        .siu-section {
            margin: 34px 0 14px;
            border-top: 1px solid rgba(212, 175, 55, 0.30);
            padding-top: 14px;
        }
        .siu-section .siu-section-no {
            letter-spacing: 0.28em;
            color: var(--siu-brass);
        }
        .siu-section .siu-section-name {
            font-weight: 700;
            color: var(--siu-bone);
            letter-spacing: 0.01em;
            margin-top: 2px;
        }

        /* ---------------------------------------------------------------
           8. CHROME + ACCESSIBILITY
           --------------------------------------------------------------- */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        .block-container {padding-top: 1rem; padding-bottom: 0rem;}

        /* Motion sensitivity outranks atmosphere: scanlines and vignette
           stay (both static), every animation stops. */
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

# The wordmark image that used to sit above this was removed; the tagline
# is now the sidebar's masthead and takes the space it freed.
st.sidebar.markdown(
    f'<h2 style="font-size: 2em; font-weight: bold; margin-bottom: 0; '
    f'margin-top: 0; text-align: center; text-transform: uppercase;">'
    f'{config.APP_TAGLINE}</h2>',
    unsafe_allow_html=True,
)
st.sidebar.markdown("---")

mode = st.sidebar.radio(
    "**🛠️ TOOLS**",
    [
        "🛡️ Profile",
        "🔍 Intelligence Dossier",
        "✉️ Data Broker Deletion",
        "⚖️ NY Expungement",
        "🚫 Google De-Indexing",
        "📬 Opt-Out Tracker",
    ],
    key="nav_mode",
    label_visibility="visible",
)

# Presentation mode toggle for safe live demos
st.sidebar.markdown("---")
pres_enabled = st.sidebar.toggle(
    "🎭 PRESENTATION MODE",
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
    # Inline <p> rather than st.caption: the caption element carries
    # Streamlit's own muted colour, which is what kept this off stark
    # white. font-size is pinned to 0.875rem (14px) so it still sits on
    # exactly the same baseline as the PRESENTATION MODE toggle label.
    st.markdown(
        '<p style="font-size: 0.875rem; font-weight: 700; color: #FFFFFF; '
        'margin: 0 0 0.25rem 0;">⚙️ SYSTEM RUNTIME CONTROL</p>',
        unsafe_allow_html=True,
    )

    # Map friendly names to internal modes
    mode_options = {
        "AUTO-DETECT": "auto",
        "🖥️ Desktop (Full Power)": "desktop",
        "☁️ Online / Cloud (Passive)": "cloud"
    }
    
    current_override = runtime_mode.get_runtime_override()
    # Find active index
    index = list(mode_options.values()).index(current_override) if current_override in mode_options.values() else 0

    selected_label = st.radio(
        # Kept as the accessible name for screen readers, hidden visually.
        "SELECT OPERATING ENVIRONMENT:",
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

elif mode == "🔍 Intelligence Dossier":
    master_component.render(brokers_df)

elif mode == "✉️ Data Broker Deletion":
    letters_component.render(brokers_df)

elif mode == "⚖️ NY Expungement":
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
        usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.SEALING_SCREENED)
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
        st.info("Nothing logged yet. Generate a letter under **Data Broker Deletion** and click \"Log this request\", or add one manually above.")
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


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(f"{config.APP_TITLE} — {config.APP_TAGLINE}. For educational purposes only. Not legal advice.", text_alignment="center")
