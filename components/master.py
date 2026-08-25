"""
Master Dashboard: the statutory countdowns, the relational exposure map,
and the discovered-accounts worklist on one page.
"""
import asyncio
from datetime import datetime

import streamlit as st

import config
import runtime_mode
import discovered_accounts
import facial_recognition
from tracker import get_all_requests
import database

from components import letters as letters_component
from applog import get_logger
from google_dork import (
    build_broker_dork_url,
    build_combined_dork_url,
    build_dork_query,
    domain_from_url,
)
from osint_aggregator import run_full_osint_sweep
import pdf_generator
import profile_state
import client_context
import presentation_mode
import usage_metrics

_log = get_logger("master")

_OSINT_STATUS_LABELS = {
    "high": "🔴 High",
    "medium": "🟡 Medium",
    "low": "🟢 Low",
    "unavailable": "⚪ Unavailable",
    # A module the runtime declined to run. Without its own label a
    # skipped vector fell through to "🟢 Low", which is the false
    # negative this status exists to prevent.
    "skipped": "⚪ Not Scanned",
}

def _greeting_name(profile):
    """First name of the active target profile, lowercased.

    Falls back through the profile shapes the dossier can be handed:
    first_name when the form filled it, otherwise the leading token of
    full_name, otherwise the demo persona so a seeded walkthrough is
    never greeted by an empty string.
    """
    raw = (profile.get("first_name") or profile.get("full_name") or "").strip()
    if not raw:
        raw = DEMO_TARGET_NAME
    return raw.split()[0].lower()

# The persona the breach panel falls back to when no target has been
# entered, so the section is never blank in a walkthrough. Matches
# presentation_mode.MOCK_PROFILE -- one demo identity, not two -- and is
# always drawn with the sample-target caption beside it.
DEMO_TARGET_NAME = presentation_mode.MOCK_PROFILE["name"]
DEMO_TARGET_EMAIL = presentation_mode.MOCK_PROFILE["email"]
DEMO_TARGET_DOMAIN = presentation_mode.MOCK_PROFILE["domain"]

# The breach list renders this many findings up front, inside a fixed
# scroll box, with the remainder one expander away.
BREACH_PREVIEW_LIMIT = 3
BREACH_SCROLLER_HEIGHT = 400

_OSINT_CONFIDENCE_BADGES = {
    "CONFIRMED": "🟢 Confirmed",
    "POSSIBLE": "🟡 Possible",
    "NOT_FOUND": "⚪ Not Found",
}


def _severity(count, high_threshold):
    """(icon, label) for an exposure count.

    One shared scale so the dossier's tiles can't disagree with each other
    about what "high" means: anything above the tile's threshold is high,
    anything at all is medium, nothing is clean.
    """
    if count > high_threshold:
        return ("🔴", "High Risk")
    if count:
        return ("🟡", "Medium Risk")
    return ("🟢", "Low / Clean")


def _osint_profile():
    profile = profile_state.get_profile(st.session_state)
    return {
        "name": profile.get("full_name") or profile.get("name") or "",
        "full_name": profile.get("full_name") or profile.get("name") or "",
        "handle": profile.get("handle") or profile.get("username") or "",
        "domain": profile.get("domain") or "",
        "state": profile.get("state") or "",
        "email": profile.get("email") or "",
        # Disambiguators for the FEC contributor match (utils/osint/fec.py).
        "city": profile.get("city") or "",
        "zip_code": profile.get("zip_code") or "",
    }


def _osint_status(result):
    if result.get("status") == "unavailable":
        return "unavailable"
    # Before the count test below: a skipped module has a count of 0 and
    # would otherwise be graded "low", i.e. clean.
    if result.get("status") == "skipped":
        return "skipped"
    count = result.get("count", result.get("cert_count", 0))
    if count > 3:
        return "high"
    if count:
        return "medium"
    return "low"


def _add_to_worklist(record, label, identifier):
    """Persist one dossier finding into the discovered-accounts worklist.

    The dossier is otherwise read-only: it shows what a sweep turned up and
    then the finding evaporates on the next rerun. Writing it here is what
    turns a card into tracked work -- save_discoveries() upserts on
    (platform, target_identifier), so re-adding the same finding refreshes
    it rather than duplicating it, and never resets triage the user has
    already done on that row.
    """
    saved = discovered_accounts.save_discoveries(runtime_mode.db_path(), [{
        "platform": label,
        "category": record.get("category") or record.get("vector") or "OSINT finding",
        "target_identifier": identifier,
        "profile_url": record.get("profile_url") or record.get("url") or "",
        "avatar_url": record.get("avatar_url") or "",
        "confidence": record.get("confidence") or "POSSIBLE",
    }])
    # The audit ZIP embeds the worklist, so a stale cached copy would omit
    # whatever was just added.
    st.session_state.pop("audit_zip", None)
    return saved


def _render_osint_records(result, key_prefix="", identifier_hint="", limit=None):
    """Draw one vector's findings.

    `limit` caps what renders immediately and puts the rest behind an
    expander. A breach sweep can return dozens of rows, each its own
    bordered card with buttons, and an unbounded list pushed the whole
    statutory action plan below three screens of scroll.
    """
    if not result or not isinstance(result, dict):
        st.caption("⚠️ No data available.")
        return

    records = result.get("records", [])
    if result.get("module") == "infrastructure":
        records = result.get("certificates", [])
        domain_info = result.get("domain_info") or {}
        if domain_info:
            records = [domain_info, *records]

    if not records:
        st.caption("No records returned.")
        return

    held_back = []
    if limit is not None and len(records) > limit:
        records, held_back = records[:limit], records[limit:]

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            st.write(str(record))
            continue
        try:
            label = next(
                (record.get(key) for key in (
                    "platform", "service", "site", "entity_name", "case_name", "contributor_name", "subdomain", "domain", "repository", "name", "email"
                ) if record.get(key)),
                "Finding",
            )
            detail = " · ".join(
                str(record[key]) for key in (
                    "vector", "category", "filing_type", "filing_date", "date", "court", "docket_number", "repository", "recipient", "amount", "issuer", "registrar", "target_identifier", "reason", "breach", "description"
                ) if record.get(key)
            )
            # Sources disagree on case. The live scanners emit "CONFIRMED"
            # (holehe_scanner, email_scanner, footprint_scanner all define
            # the verdicts as uppercase constants); the presentation
            # fixtures in utils/presentation_mode.py spell every row
            # "confirmed". Both the badge lookup and the `actionable` test
            # below were case-sensitive, so every mock finding rendered
            # stripped of its verdict badge AND of its worklist button --
            # a populated panel that looked inert. Normalising here fixes
            # all sources at once rather than editing one fixture file.
            confidence = str(record.get("confidence") or "").upper()
            badge = _OSINT_CONFIDENCE_BADGES.get(confidence)
            with st.container(border=True):
                st.markdown(f"**{label}**" + (f" · {badge}" if badge else ""))
                if detail:
                    st.caption(detail)

                url = record.get("profile_url") or record.get("court_url") or record.get("url")
                identifier = record.get("target_identifier") or identifier_hint

                # Only a finding that actually points at an account is
                # worth tracking for closure. A NOT_FOUND row is the
                # absence of one, and a row with no identifier has no
                # stable key to upsert against.
                actionable = confidence in ("CONFIRMED", "POSSIBLE") and bool(identifier)

                action_cols = st.columns([1, 1]) if (url and actionable) else None
                url_slot = action_cols[0] if action_cols else st
                add_slot = action_cols[1] if action_cols else st

                if url:
                    url_slot.link_button("Open source", url, width="content")
                if actionable:
                    add_key = f"worklist_{key_prefix}_{index}"
                    if add_slot.button(
                        "➕ Add to Closure Worklist",
                        key=add_key,
                        width="content",
                        help=f"Track {label} as an account to close, and include it in the audit export.",
                    ):
                        _add_to_worklist(record, label, identifier)
                        st.toast(f"Added {label} to the closure worklist.", icon="➕")
        except Exception as e:
            _log.warning("Error rendering record: %s", e)
            continue

    if held_back:
        with st.expander(f"Show {len(held_back)} more finding{'s' if len(held_back) != 1 else ''}"):
            _render_osint_records(
                {**result, "records": held_back, "module": result.get("module")},
                key_prefix=f"{key_prefix}_more",
                identifier_hint=identifier_hint,
            )


def _broker_domain(row):
    """Bare domain for a broker row, preferring its search URL.

    brokers.csv carries both a search_url and an optout_url, and pandas
    hands back a float NaN for blank cells -- hence the isinstance check
    rather than a plain truthiness test.
    """
    for column in ("search_url", "optout_url"):
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            domain = domain_from_url(value.strip())
            if domain:
                return domain
    return ""


def _render_verification_vectors(brokers_df):
    """Google dork strings for confirming exposure by hand, per broker.

    These are search *strings*, not scrapes -- no request leaves the
    machine until the operator chooses to run one. The query is shown as
    text rather than hidden behind a link on purpose: confirming a
    listing exists with your own eyes is the step that precedes a
    deletion demand, and CCPA § 1798.105 asks a broker to delete a record
    the requester can actually point to.
    """
    st.markdown("### 🔍 Manual verification")
    st.caption(
        "Paste a query into Google to confirm the broker still lists you. "
        "Nothing here contacts the broker -- these are search strings only."
    )

    profile = profile_state.get_profile(st.session_state)
    name = (profile.get("full_name") or "").strip()
    location = ", ".join(
        value.strip() for value in (profile.get("city"), profile.get("state"))
        if isinstance(value, str) and value.strip()
    )

    if not name:
        st.warning(
            "Enter your full name on the 👤 Identity Profile page -- a dork without a name "
            "returns the whole broker site, not your listing."
        )
        return

    if brokers_df is None or brokers_df.empty:
        st.caption("No brokers on file to build queries against.")
        return

    rows = [(row["broker_name"], _broker_domain(row)) for _, row in brokers_df.iterrows()]
    rows = [(broker_name, domain) for broker_name, domain in rows if domain]

    if not rows:
        st.caption("No broker domains on file -- check the search_url column in brokers.csv.")
        return

    domains = [domain for _, domain in rows]

    with st.container(border=True):
        # Each button carries its own query in the tooltip. The string is
        # what makes this verifiable -- an operator has to be able to read
        # the operators being run in their name before trusting a result --
        # so it stays reachable even though the button now does the work.
        st.link_button(
            "🔍 Search all brokers at once",
            build_combined_dork_url(name, location, domains),
            key="dork_sweep_global",
            help=build_dork_query(name, location, domains),
            width="stretch",
        )
        st.caption(
            f"One search across all {len(rows)} broker domains. "
            "Hover any command to read the exact query it runs."
        )
        st.divider()

        # Three across keeps the commands on one line at desktop width and
        # collapses to a stack on narrow viewports without extra CSS.
        for index in range(0, len(rows), 3):
            for column, (broker_name, domain) in zip(
                st.columns(3), rows[index:index + 3]
            ):
                with column:
                    st.link_button(
                        f"🔍 {broker_name}",
                        build_broker_dork_url(name, location, domain),
                        key=f"dork_run_{broker_name}",
                        help=build_dork_query(name, location, [domain]),
                        width="stretch",
                    )


def _section_header(icon, name):
    st.subheader(f"{icon} {name}")


def _render_osint_vector(result, title, key_prefix="", identifier_hint="", limit=None):
    if not result:
        st.markdown(f"##### {title}  ·  `⚪ Unavailable`")
        st.caption("No data retrieved.")
        return
    status_key = _osint_status(result)
    badge = _OSINT_STATUS_LABELS.get(status_key, "⚪ Unavailable")
    st.markdown(f"##### {title}  ·  `{badge}`")
    _render_osint_records(
        result, key_prefix=key_prefix, identifier_hint=identifier_hint, limit=limit,
    )


# What the network column says when there is no geolocation answer. Each
# one names the actual cause: the panel used to print "Local Network /
# Subnet" for all of them, including the hosted runtime where it was
# simply wrong.
_NETWORK_NOTES = {
    "ok": "",
    "private": "Private/LAN address — no public registry entry to resolve.",
    "rate_limited": "Geolocation rate-limited by ipinfo.io (HTTP 429). Header data below is still live.",
    "unreachable": "Geolocation lookup did not answer (timeout, DNS, or blocked egress).",
    "malformed": "Geolocation returned an unreadable response.",
    "no_proxy_header": "Local request — no forwarded address, so nothing to geolocate.",
}

# Presentation mode fakes the *network* block only. A demo needs a
# plausible public IP and a residential PTR record -- a localhost address
# on the projector proves nothing -- but it must not fake the device
# block: those three fields describe the laptop actually on stage, the
# audience can see it, and the mock reported "macOS 15.1 (Sequoia)" to
# every presenter regardless of what they were running.
_PRESENTATION_NETWORK = {
    "client_ip": "198.51.100.42",
    "reverse_dns": "pool-198-51-100-42.nycmny.fios.verizon.net",
    "geo_info": "New York, NY [US-EAST]",
    "network_note": "🎭 Presentation mode — sample network identity.",
}


@st.cache_data(ttl=900, show_spinner=False)
def _cached_ip_lookup(ip):
    """One geolocation call per address per 15 minutes.

    Streamlit reruns the whole script on every widget interaction, so an
    uncached lookup here meant one ipinfo.io request per click. The free
    tier is roughly a thousand a day; a demo can spend that in an
    afternoon and then show 429s on stage.
    """
    return client_context.lookup(ip)


def _connection_telemetry(lookup_fn=None):
    """Everything the network/device/session columns render.

    Split out of render() so it can be tested without a browser: it takes
    only headers and st.context, and returns strings.
    """
    presenting = bool(st.session_state.get("presentation_mode"))

    try:
        headers = st.context.headers or {}
    except Exception:
        _log.exception("st.context.headers unavailable")
        headers = {}

    if presenting:
        # Empty headers, so describe() short-circuits without a lookup:
        # the network block is sampled below, and spending quota on an
        # address we are about to overwrite would display nothing.
        ctx = client_context.describe({})
        status = "ok"
    else:
        ctx = client_context.describe(headers, lookup_fn=lookup_fn or _cached_ip_lookup)
        status = ctx["status"]

    # Real headers even in presentation mode: this is the machine on stage.
    user_agent, os_family, browser_engine = client_context.device_profile(headers)

    accept_lang = headers.get("Accept-Language") or headers.get("accept-language") or ""
    if not accept_lang:
        # The browser reports its locale to Streamlit even when the header
        # is stripped by a proxy.
        accept_lang = getattr(st.context, "locale", None) or "Not disclosed"
    if len(accept_lang) > 40:
        accept_lang = accept_lang[:40] + "…"

    dnt_val = headers.get("DNT") or headers.get("dnt") or ""
    gpc_val = headers.get("Sec-GPC") or headers.get("sec-gpc") or ""
    if gpc_val == "1":
        dnt_status = "GPC: ✅ Active"
    elif dnt_val == "1":
        dnt_status = "DNT: ✅ Active"
    else:
        dnt_status = "DNT/GPC: ❌ Inactive"

    # Browser-reported timezone beats the registry's guess when both are
    # present -- it comes from the visitor's own clock, not from where
    # their ISP happens to be registered.
    browser_tz = getattr(st.context, "timezone", None)
    geo_info = ctx["location"] or ctx.get("timezone") or browser_tz or "Not resolvable"
    if ctx["location"] and browser_tz:
        geo_info = f"{ctx['location']} · {browser_tz}"

    telemetry = {
        "client_ip": ctx["client_ip"] or "Not forwarded (local request)",
        "reverse_dns": ctx.get("hostname") or "No PTR record",
        "geo_info": geo_info,
        "user_agent": user_agent,
        "os_family": os_family,
        "browser_engine": browser_engine,
        "accept_lang": accept_lang,
        "referrer": headers.get("Referer") or headers.get("referer") or "Direct / None",
        "dnt_status": dnt_status,
        "network_note": _NETWORK_NOTES.get(status, ""),
    }
    if presenting:
        telemetry.update(_PRESENTATION_NETWORK)
    return telemetry


def render(brokers_df):
    # Clear ghost results on page load/navigation to eliminate stale cache
    if not st.session_state.get("_master_page_loaded"):
        st.cache_data.clear()
        # Purge stale result keys from session
        for ghost_key in ["osint_findings_appended", "facial_recognition_matches"]:
            st.session_state.pop(ghost_key, None)
        st.session_state._master_page_loaded = True

    st.title("🔍 Intelligence Dossier")
    st.caption(
        "Unified Executive Summary encompassing online identity, data exposures, "
        "public footprint, and statutory action plans."
    )

    # Always read fresh profile state (not stale from input widget cache)
    profile = profile_state.get_profile(st.session_state)

    # The active target profile's first name, lowercased -- the seeded demo
    # persona renders as "hello, jane". Nothing here is hardcoded: on a live
    # install this is whoever the operator entered on the Identity Profile
    # page, which for a self-service run is themselves.
    first_name = _greeting_name(profile)
    if first_name:
        st.markdown(f"## **hello, {first_name}**")

    # --- Combined Telemetry & Harvest Vector Card ---
    with st.container(border=True):
        st.markdown("### 🌐 What Your Connection Reveals Online")
        st.caption("A live look at what any website or tracker passively harvests about your identity and environment from this request alone.")

        tele = _connection_telemetry()

        net_col, device_col, session_col = st.columns(3)
        with net_col:
            st.markdown("#### 📡 Network")
            st.markdown(f"**Public IP Address**  \n<div style='font-size: 1.25rem; font-weight: 600; color: #D4AF37; font-family: var(--np-mono); margin-bottom: 0.4rem;'>{tele['client_ip']}</div>", unsafe_allow_html=True)
            st.markdown(f"**Reverse DNS Host**  \n<div style='font-size: 1.0rem; color: #E8E2D4; font-family: var(--np-mono); margin-bottom: 0.4rem;'>{tele['reverse_dns']}</div>", unsafe_allow_html=True)
            st.markdown(f"**Inferred Location**  \n<div style='font-size: 1.0rem; color: #E8E2D4; font-family: var(--np-mono);'>{tele['geo_info']}</div>", unsafe_allow_html=True)
            if tele["network_note"]:
                st.caption(tele["network_note"])
        with device_col:
            st.markdown("#### 💻 Device")
            st.markdown(f"**Operating System**  \n<div style='font-size: 1.25rem; font-weight: 600; color: #D4AF37; font-family: var(--np-mono); margin-bottom: 0.4rem;'>{tele['os_family']}</div>", unsafe_allow_html=True)
            st.markdown(f"**Browser Engine**  \n<div style='font-size: 1.0rem; color: #E8E2D4; font-family: var(--np-mono); margin-bottom: 0.4rem;'>{tele['browser_engine']}</div>", unsafe_allow_html=True)
            st.markdown(f"**User Agent String**  \n<div style='font-size: 0.9rem; color: #B4BDCA; font-family: var(--np-mono); word-break: break-all;'>{tele['user_agent']}</div>", unsafe_allow_html=True)
        with session_col:
            st.markdown("#### 🛡️ Session")
            st.markdown(f"**Privacy Signal (DNT/GPC)**  \n<div style='font-size: 1.2rem; font-weight: 600; font-family: var(--np-mono); margin-bottom: 0.4rem;'>{tele['dnt_status']}</div>", unsafe_allow_html=True)
            st.markdown(f"**Accepted Languages**  \n<div style='font-size: 1.0rem; color: #E8E2D4; font-family: var(--np-mono); margin-bottom: 0.4rem;'>{tele['accept_lang']}</div>", unsafe_allow_html=True)
            st.markdown(f"**Referrer Origin**  \n<div style='font-size: 1.0rem; color: #E8E2D4; font-family: var(--np-mono);'>{tele['referrer']}</div>", unsafe_allow_html=True)

        with st.expander("How this is collected", icon=":material/info:"):
            vec_net, vec_hw, vec_session = st.columns(3)

            with vec_net:
                st.markdown("**Network & routing**")
                st.caption(
                    "Read from the HTTP handshake (`X-Forwarded-For`, remote address). "
                    "Tied to your ISP's routing hub without a VPN or proxy, and "
                    "correlates across every site you visit."
                )

            with vec_hw:
                st.markdown("**Device fingerprint**")
                st.caption(
                    "Read from the `User-Agent` header and navigator properties. "
                    "The combined entropy (OS + browser + engine) creates a "
                    "persistent fingerprint across sessions, even without cookies."
                )

            with vec_session:
                st.markdown("**Session context**")
                st.caption(
                    "Read from `Accept-Language`, `Referer`, `DNT`, and `Sec-GPC`. "
                    "Reveals language and browsing origin. DNT/GPC are advisory "
                    "only -- most commercial trackers ignore them."
                )

    # Full detail lives in the sidebar's runtime environment control; this
    # is just a reminder of which recon path the button below will take.
    if runtime_mode.is_cloud_deployment():
        st.caption("☁️ Cloud runtime -- passive OSINT only (Gravatar, PGP, certificate transparency). Heavy sweeps disabled.")
    elif runtime_mode.is_demo_mode():
        st.caption("🟡 Demo sandbox -- mock OSINT data shown, no real scanning.")
    else:
        st.caption("🖥️ Desktop runtime -- full active OSINT enabled (700+ sites, Gravatar, PGP, breaches).")

    # Validate email is present for email OSINT
    email_valid = profile.get("email", "").strip() and "@" in profile.get("email", "")

    # Execution button or automatic trigger from profile save
    trigger_recon = st.button("📄 Generate Dossier", type="primary", use_container_width=True)
    if not trigger_recon and st.session_state.get("profile_saved_auto_scan"):
        trigger_recon = True
        st.session_state.profile_saved_auto_scan = False

    if trigger_recon:
        progress_text = "Running comprehensive recon sweep across all modules concurrently..."
        my_bar = st.progress(0, text=progress_text)
        try:
            if st.session_state.get("presentation_mode"):
                st.session_state.osint_findings = asyncio.run(presentation_mode.get_mock_osint_findings())
                usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.FOOTPRINT_SCAN_RUN)
                usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.EMAIL_SCAN_RUN)
                my_bar.progress(100, text="Mock recon sweep complete (Presentation Mode).")
            else:
                is_cloud = runtime_mode.is_cloud_deployment()
                if profile.get("handle") and not is_cloud:
                    usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.FOOTPRINT_SCAN_RUN)
                if email_valid:
                    usage_metrics.record_event(config.USAGE_METRICS_DB_PATH, usage_metrics.EMAIL_SCAN_RUN)

                st.session_state.osint_findings = asyncio.run(
                    run_full_osint_sweep(_osint_profile(), passive_only=is_cloud)
                )
                if is_cloud:
                    my_bar.progress(100, text="Passive recon sweep complete (Heavy scans skipped in Cloud Mode).")
                else:
                    my_bar.progress(100, text="Full active recon sweep complete.")
                    
            st.session_state.pop("audit_zip", None)
            st.session_state.pop("osint_dossier_pdf", None)
        except Exception as exc:
            _log.error("OSINT sweep failed: %s", exc)
            st.error("The sweep could not be completed.")
            my_bar.empty()

    findings = st.session_state.get("osint_findings")
    if not findings:
        missing = []
        if not profile.get("full_name"):
            missing.append("Full name")
        if not email_valid:
            missing.append("Valid email address")
        with st.container(border=True):
            if missing:
                st.markdown("#### 📋 Enter your profile details to start")
                st.caption(
                    "Recon needs " + " and ".join(f"**{field}**" for field in missing).lower()
                    + " before it can sweep anything. Nothing leaves this machine until you run the sweep."
                )
                if st.button("👤 Go to Identity Profile", type="primary", key="dossier_empty_profile"):
                    st.session_state.pending_nav = "👤 Identity Profile"
                    st.rerun()
            else:
                st.markdown("#### ⚡ Ready to sweep")
                st.caption(
                    "Your profile is complete. Run the recon sweep to build the dossier — "
                    "email exposures, platform footprint, and public records in one pass."
                )
                st.caption("Use **📄 Generate Dossier** above to begin.")
        return

    summary = findings.get("summary", {})
    vectors = findings.get("vectors", {})

    fp_vector = findings.get("footprint") or vectors.get("footprint") or {}
    email_vector = findings.get("email") or vectors.get("email") or {}
    github_vector = findings.get("github") or vectors.get("github") or {}
    sec_vector = findings.get("sec") or vectors.get("sec") or {}
    fec_vector = findings.get("fec") or vectors.get("fec") or {}
    court_vector = findings.get("courtlistener") or vectors.get("courtlistener") or {}
    infra_vector = findings.get("infrastructure") or vectors.get("infrastructure") or {}

    fp_records = fp_vector.get("records", [])
    email_records = email_vector.get("records", [])
    github_records = github_vector.get("records", [])
    sec_records = sec_vector.get("records", [])
    fec_records = fec_vector.get("records", [])
    court_records = court_vector.get("records", [])
    infra_records = infra_vector.get("certificates", [])

    fp_count = len(fp_records)
    email_count = len(email_records)

    total_exposure = (
        fp_count + email_count + len(github_records) +
        len(sec_records) + len(fec_records) + len(court_records) + len(infra_records)
    )
    
    if "summary" in findings and isinstance(findings["summary"], dict):
        findings["summary"]["total_exposures"] = total_exposure
    
    # The same false negative the footprint panel below guards against,
    # in metric form: a skipped sweep leaves fp_count at 0, which
    # _severity grades "🟢 Low / Clean" -- a clean bill of health for
    # handles nothing ever looked at. An em dash reports the count as
    # unknown, which is what it is.
    fp_skipped = fp_vector.get("status") == "skipped"
    handles_value = "—" if fp_skipped else fp_count
    handles_tier = ("⚪", "Not scanned") if fp_skipped else _severity(fp_count, 2)
    # The headline metric sums seven vectors, so a skipped one makes it an
    # undercount -- and "🟢 Low / Clean" is the most reassuring thing on
    # the page to be wrong about. It reports the scan as partial instead.
    score_tier = ("⚪", "Partial scan") if fp_skipped else _severity(total_exposure, 2)

    # One severity vocabulary across all four tiles. Severity rides in the
    # delta slot with delta_color="off" so it reads as a neutral label --
    # Streamlit's default green/red arrows would say "improving/worsening",
    # which is not what a standing exposure count means.
    c1, c2, c3, c4 = st.columns(4)
    for column, label, value, tier in (
        (c1, "Exposure score", total_exposure, score_tier),
        (c2, "Email exposures", email_count, _severity(email_count, 1)),
        (c3, "Exposed handles", handles_value, handles_tier),
        # Deletion targets is a workload, not a risk -- how many brokers are
        # on file to demand against. It gets a count and no severity colour.
        (c4, "Deletion targets", len(brokers_df), None),
    ):
        column.metric(
            f"{tier[0] if tier else '📬'} {label}",
            value,
            delta=tier[1] if tier else None,
            delta_color="off",
        )

    # Build or retrieve the intelligence dossier PDF bytes
    try:
        if "osint_dossier_pdf" not in st.session_state or not st.session_state.get("osint_dossier_pdf"):
            st.session_state.osint_dossier_pdf = pdf_generator.build_dossier_bytes(
                _osint_profile(), findings
            )
    except Exception as exc:
        _log.error("Dossier PDF generation failed: %s", exc)
        st.session_state.osint_dossier_pdf = None

    if st.session_state.get("osint_dossier_pdf"):
        st.download_button(
            "📄 Download Intelligence Dossier (PDF)",
            data=st.session_state.osint_dossier_pdf,
            file_name=f"non_pursuit_osint_dossier_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
            help="Target profile, email exposures and account footprint as one shareable report.",
        )
    else:
        st.caption("⚠️ The PDF dossier could not be generated for these results.")

    st.markdown("---")

    _section_header("👤", "Online identity & media exposure")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            # Social & Platform Footprint section with diagnostics consistent with Email Exposure
            handle_target = profile.get("handle") or (findings.get("profile") or {}).get("handle") or ""

            if fp_count > 0 or fp_records:
                _render_osint_vector(
                    fp_vector, f"👤 Social & Platform Footprint ({fp_count} findings)",
                    key_prefix="footprint", identifier_hint=handle_target,
                )
                fp_checks = fp_vector.get("checks", [])
                if fp_checks and len(fp_checks) > fp_count:
                    with st.expander(f"🔍 Probed platforms log ({fp_count} hits of {len(fp_checks)} platforms checked)"):
                        st.caption(f"Showing all {len(fp_checks)} platforms probed:")
                        for chk in fp_checks:
                            if isinstance(chk, dict):
                                chk_plat = chk.get("platform") or chk.get("service") or "Platform"
                                chk_conf = chk.get("confidence", "UNKNOWN")
                                chk_icon = "🟢" if chk_conf in ("CONFIRMED", "POSSIBLE") else "⚪"
                                st.caption(f"{chk_icon} **{chk_plat}**: `{chk_conf}`")
            # Checked before the handle branch, and before the clean
            # branch below, because when the runtime declined to run the
            # sweep that is the whole reason there is nothing here --
            # telling the operator to go enter a handle would just set
            # them up for the same empty panel a second time.
            elif fp_vector.get("status") == "skipped":
                st.markdown("##### 👤 Social & Platform Footprint  ·  `⚪ Not Scanned (Disabled in Cloud)`")
                st.warning(
                    "**This is not a clean result — nothing was checked.** The full handle sweep "
                    "is disabled on the online runtime: it is a heavy outbound scan and this build "
                    "shares one IP with every other visitor."
                )
                st.caption("Switch to 🖥️ Desktop runtime in the sidebar to run it for real.")
            elif not handle_target:
                st.markdown("##### 👤 Social & Platform Footprint  ·  `⚪ Not Available`")
                st.warning("No username / handle provided. Enter your handle on the 👤 Identity Profile page to scan for social platform footprints.")
            elif fp_vector.get("status") == "unavailable":
                st.markdown("##### 👤 Social & Platform Footprint  ·  `⚪ Scan Failed`")
                st.caption(f"Footprint scan failed for: {handle_target}")
                st.caption("This could be due to: timeout, rate limits, or network issues. Try again in a moment.")
            else:
                st.markdown("##### 👤 Social & Platform Footprint  ·  `🟢 Clean`")
                st.caption(f"No exposed accounts found for @{handle_target} across probed platforms.")
        with c2:
            st.markdown("##### 🖼️ Public Media & Avatars")
            found_avatars = False
            for r in fp_records + email_records:
                if isinstance(r, dict) and r.get("avatar_url"):
                    st.image(r["avatar_url"], width=64, caption=r.get("platform") or r.get("service"))
                    found_avatars = True
            if not found_avatars:
                st.caption("No avatars discovered.")
                
    _section_header("🔐", "Data exposures & breaches")
    with st.container(border=True):
        # Email exposure section with detailed diagnostics
        email_target = (
            profile.get("email")
            or (findings.get("profile") or {}).get("email")
            or DEMO_TARGET_EMAIL
        )
        target_is_sample = email_target == DEMO_TARGET_EMAIL and not profile.get("email")
        if target_is_sample:
            # Labelled, always. An unlabelled fallback persona means a real
            # visitor who entered nothing sees a populated breach panel and
            # has no way to tell it is not about them.
            st.caption(
                f"🎭 Sample target — **{DEMO_TARGET_NAME}** (`{DEMO_TARGET_EMAIL}`). "
                "Enter your own address on the 👤 Identity Profile page to scan for real."
            )

        if email_count > 0 or email_records:
            # Fixed-height scroller: the sweep can return dozens of rows,
            # and the section below it (the statutory action plan) is the
            # one the demo actually walks to. BREACH_PREVIEW_LIMIT rows
            # render immediately, the rest sit in the expander inside the
            # same scroll box.
            with st.container(height=BREACH_SCROLLER_HEIGHT):
                _render_osint_vector(
                    email_vector, f"📧 Email & Identity Exposure ({email_count} findings)",
                    key_prefix="email", identifier_hint=email_target,
                    limit=BREACH_PREVIEW_LIMIT,
                )
            em_checks = email_vector.get("checks", [])
            if em_checks and len(em_checks) > email_count:
                with st.expander(f"🔍 Probed services log ({email_count} hits of {len(em_checks)} services checked)"):
                    st.caption(f"Showing all {len(em_checks)} passive checks conducted across web services and registries:")
                    for chk in em_checks:
                        if isinstance(chk, dict):
                            chk_plat = chk.get("platform") or chk.get("service") or "Service"
                            chk_conf = chk.get("confidence", "UNKNOWN")
                            chk_icon = "🟢" if chk_conf in ("CONFIRMED", "POSSIBLE") else "⚪"
                            st.caption(f"{chk_icon} **{chk_plat}**: `{chk_conf}`")
        elif target_is_sample:
            # Reached before any sweep has run against the sample persona:
            # say what the panel is waiting for rather than reporting the
            # demo identity as clean.
            st.markdown("##### 📧 Email & Identity Exposure  ·  `⚪ Not Scanned`")
            st.caption(
                f"No sweep has run for {DEMO_TARGET_EMAIL} yet — press "
                "**📄 Generate Dossier** above, or enter your own address "
                "on the 👤 Identity Profile page."
            )
        elif email_vector.get("status") == "unavailable":
            st.markdown("##### 📧 Email & Identity Exposure  ·  `⚪ Scan Failed`")
            st.caption(f"Email scan failed for: {email_target}")
            st.caption("This could be due to: timeout, API limits, or network issues. Try again in a moment.")
        else:
            st.markdown("##### 📧 Email & Identity Exposure  ·  `🟢 Clean`")
            st.caption(f"No exposures found for {email_target} in:")
            cols = st.columns(2)
            cols[0].caption("• Gravatar profiles\n• PGP key servers")
            cols[1].caption("• Known breaches\n• DNS validation")

        st.divider()

        # GitHub exposure section
        gh_vector = findings.get("github") or findings.get("vectors", {}).get("github") or {}
        _render_osint_vector(
            gh_vector, "Developer & Code Exposure",
            key_prefix="github", identifier_hint=profile.get("handle") or "",
        )

    _section_header("🏛️", "Legal, financial & corporate footprint")
    with st.container(border=True):
        sec_col, fec_col, court_col = st.columns(3)
        with sec_col:
            sec_vector = findings.get("sec") or findings.get("vectors", {}).get("sec") or {}
            _render_osint_vector(sec_vector, "SEC Filings", key_prefix="sec")
        with fec_col:
            fec_vector = findings.get("fec") or findings.get("vectors", {}).get("fec") or {}
            _render_osint_vector(fec_vector, "FEC Contributions", key_prefix="fec")
        with court_col:
            cl_vector = findings.get("courtlistener") or findings.get("vectors", {}).get("courtlistener") or {}
            _render_osint_vector(cl_vector, "Court Dockets", key_prefix="court")

    with st.container(border=True):
        infra_vector = findings.get("infrastructure") or findings.get("vectors", {}).get("infrastructure") or {}
        _render_osint_vector(
            infra_vector, "Domains & Certificates",
            key_prefix="infra", identifier_hint=profile.get("domain") or "",
        )

    _render_verification_vectors(brokers_df)

    _section_header("⚔️", "Statutory action plan")
    with st.container(border=True):
        letters_component.render(brokers_df)
