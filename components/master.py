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
from osint_aggregator import run_full_osint_sweep
import pdf_generator
import profile_state
import presentation_mode

_log = get_logger("master")

_OSINT_STATUS_LABELS = {
    "high": "🔴 High",
    "medium": "🟡 Medium",
    "low": "🟢 Low",
    "unavailable": "⚪ Unavailable",
}


def _osint_profile():
    profile = profile_state.get_profile(st.session_state)
    return {
        "name": profile["full_name"],
        "handle": profile["handle"],
        "domain": profile["domain"],
        "state": profile["state"],
        "email": profile["email"],
    }


def _osint_status(result):
    if result.get("status") == "unavailable":
        return "unavailable"
    count = result.get("count", result.get("cert_count", 0))
    if count > 3:
        return "high"
    if count:
        return "medium"
    return "low"


def _render_osint_records(result):
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

    for record in records[:8]:
        if not isinstance(record, dict):
            st.write(str(record))
            continue
        try:
            label = next(
                (record.get(key) for key in (
                    "entity_name", "case_name", "contributor_name", "email", "subdomain", "domain", "platform", "service"
                ) if record.get(key)),
                "Finding",
            )
            detail = " · ".join(
                str(record[key]) for key in (
                    "filing_type", "filing_date", "court", "docket_number", "repository", "issuer", "registrar", "target_identifier", "confidence", "reason"
                ) if record.get(key)
            )
            with st.container(border=True):
                st.markdown(f"**{label}**")
                if detail:
                    st.caption(detail)
                url = record.get("url") or record.get("court_url") or record.get("profile_url")
                if url:
                    st.link_button("Open source", url, width="content")
        except Exception as e:
            _log.warning("Error rendering record: %s", e)
            continue


def _section_header(number, icon, name):
    """Dossier-style section rule: mono case-file kicker over a serif title.

    Presentational only -- st.subheader gave no hook for the SIU styling
    (Streamlit owns the h2 markup), so the header is emitted as markup
    the .siu-section rules in app.py can reach.
    """
    st.markdown(
        f'''
        <div class="siu-section">
            <div class="siu-section-no">Section {number:02d} &nbsp;//&nbsp; Case File</div>
            <div class="siu-section-name">{icon} {name}</div>
        </div>
        ''',
        unsafe_allow_html=True,
    )


def _render_osint_vector(result, title):
    if not result:
        st.markdown(f"##### {title}  ·  `⚪ Unavailable`")
        st.caption("No data retrieved.")
        return
    status_key = _osint_status(result)
    badge = _OSINT_STATUS_LABELS.get(status_key, "⚪ Unavailable")
    st.markdown(f"##### {title}  ·  `{badge}`")
    _render_osint_records(result)


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

    # --- Task 1: Dynamic greeting header ---
    subject_name = (profile.get("full_name") or "").strip()
    if subject_name:
        st.markdown(f"## 🕵️‍♂️ Case File Active: Hello, {subject_name}")
    else:
        st.markdown("## 🕵️‍♂️ Case File Active: Hello, Subject Unknown")

    # --- Combined Telemetry & Harvest Vector Card ---
    with st.container(border=True):
        st.markdown("##### 🌐 Client Telemetry & Collection Vector Analysis")
        st.caption("Live passive environmental detection merged with forensic breakdown of how each data point is harvested.")

        # Collect telemetry safely
        if st.session_state.get("presentation_mode"):
            client_ip = "198.51.100.42 [Mock Gateway]"
            reverse_dns = "pool-198-51-100-42.nycmny.fios.verizon.net"
            geo_info = "New York, NY [US-EAST]"
            user_agent = "Chrome 128 / macOS Sequoia 15.1"
            os_family = "macOS 15.1 (Sequoia)"
            browser_engine = "Blink / V8"
            accept_lang = "en-US, en;q=0.9"
            referrer = "Direct / None"
            dnt_status = "DNT: 0 (Not Set)"
        else:
            try:
                headers = st.context.headers
                client_ip = headers.get("X-Forwarded-For", headers.get("x-forwarded-for", "127.0.0.1 / Localhost"))
                reverse_dns = headers.get("X-Client-Hostname", headers.get("x-client-hostname", "Localhost / Loopback"))
                geo_info = headers.get("X-Timezone", headers.get("x-timezone", "Local Network / Subnet"))
                raw_ua = headers.get("User-Agent", headers.get("user-agent", "Unknown"))
                user_agent = raw_ua[:60] if raw_ua else "Unknown"
                # Parse OS and browser from UA string
                if "Mac" in raw_ua:
                    os_family = "macOS"
                elif "Windows" in raw_ua:
                    os_family = "Windows"
                elif "Linux" in raw_ua:
                    os_family = "Linux"
                else:
                    os_family = "Unknown OS"
                if "Chrome" in raw_ua:
                    browser_engine = "Blink / V8"
                elif "Firefox" in raw_ua:
                    browser_engine = "Gecko"
                elif "Safari" in raw_ua:
                    browser_engine = "WebKit"
                else:
                    browser_engine = "Unknown Engine"
                accept_lang = headers.get("Accept-Language", headers.get("accept-language", "Not Disclosed"))
                if len(accept_lang) > 40:
                    accept_lang = accept_lang[:40] + "…"
                referrer = headers.get("Referer", headers.get("referer", "Direct / None"))
                dnt_val = headers.get("DNT", headers.get("dnt", ""))
                gpc_val = headers.get("Sec-GPC", headers.get("sec-gpc", ""))
                if gpc_val == "1":
                    dnt_status = "GPC: ✅ Active"
                elif dnt_val == "1":
                    dnt_status = "DNT: ✅ Active"
                else:
                    dnt_status = "DNT/GPC: ❌ Not Set"
            except Exception:
                client_ip = "127.0.0.1 / Localhost"
                reverse_dns = "Localhost / Loopback"
                geo_info = "Local Network / Subnet"
                user_agent = "Unavailable"
                os_family = "Unknown"
                browser_engine = "Unknown"
                accept_lang = "Not Disclosed"
                referrer = "Direct / None"
                dnt_status = "Standard Masked"

        # --- Live Exposure Findings ---
        telemetry_block = f"""
🌐 **PUBLIC NETWORK & ROUTING**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 **Interface IP:** `{client_ip}`
🏷️ **rDNS Host:** `{reverse_dns}`
📍 **Inferred Region:** `{geo_info}`

💻 **DEVICE & PLATFORM FINGERPRINT**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💻 **Platform / OS:** `{os_family}`
🧭 **Browser Engine:** `{browser_engine}`
🔧 **Raw User-Agent:** `{user_agent}`

🕵️ **SESSION CONTEXT & PRIVACY HEADERS**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ **Global Privacy Control (GPC):** {dnt_status}
🗣️ **Accepted Languages:** `{accept_lang}`
🔗 **HTTP Referrer Origin:** `{referrer}`
"""
        st.markdown("### 🚨 LIVE EXPOSURE FINDINGS")
        st.markdown(telemetry_block)

        # --- Right / Collapsible: Forensic Breakdown ---
        with st.expander("🔬 How Tracking Works — Forensic Collection Vector Breakdown"):
            vec_net, vec_hw, vec_session = st.columns(3)

            with vec_net:
                st.markdown("**🌐 Network & Routing Vector**")
                st.info(
                    "📡 **Vector:** HTTP Handshake & Remote Headers "
                    "(`X-Forwarded-For`, `Remote_Addr`)\n\n"
                    "⚠️ **Risk:** Tied directly to physical ISP routing hub "
                    "without a VPN/proxy. Correlates across every site visit."
                )

            with vec_hw:
                st.markdown("**🧭 Hardware & Platform Fingerprint**")
                st.info(
                    "🧩 **Vector:** Client `User-Agent` Header & "
                    "Navigator DOM Properties\n\n"
                    "⚠️ **Risk:** Combined entropy (OS + browser + engine + "
                    "screen resolution) creates a persistent browser "
                    "fingerprint across sessions — even without cookies."
                )

            with vec_session:
                st.markdown("**🕵️ Active Session Context & Leaks**")
                st.info(
                    "🔍 **Vector:** Passive HTTP Request Metadata "
                    "(`Accept-Language`, `Referer`, `DNT`, `Sec-GPC`)\n\n"
                    "⚠️ **Risk:** Reveals localized geographic preference "
                    "and cross-site browsing origin. DNT/GPC is advisory only — "
                    "most commercial trackers ignore it."
                )

    # PROMINENT ENVIRONMENT MODE BANNER
    mode_cols = st.columns([1, 3])
    with mode_cols[0]:
        if runtime_mode.is_cloud_deployment():
            st.error("☁️ WEB MODE")
        elif runtime_mode.is_demo_mode():
            st.warning("🟡 DEMO MODE")
        else:
            st.success("🖥️ DESKTOP MODE")

    with mode_cols[1]:
        if runtime_mode.is_cloud_deployment():
            st.caption("**WEB / CLOUD RUNTIME** — Passive OSINT only (Gravatar, PGP, CertSpotter). Email scans work. Heavy sweeps disabled.")
        elif runtime_mode.is_demo_mode():
            st.caption("**DEMO SANDBOX** — Mock OSINT data shown. No real scanning.")
        else:
            st.caption("**DESKTOP RUNTIME** — Full active OSINT enabled. All email vectors available (700+ sites, Gravatar, PGP, breaches).")

    # Validate email is present for email OSINT
    email_valid = profile.get("email", "").strip() and "@" in profile.get("email", "")

    # Execution button
    if st.button("⚡ Execute Master Recon", type="primary", use_container_width=True):
        progress_text = "Running comprehensive recon sweep across all modules concurrently..."
        my_bar = st.progress(0, text=progress_text)
        try:
            if st.session_state.get("presentation_mode"):
                st.session_state.osint_findings = asyncio.run(presentation_mode.get_mock_osint_findings())
                my_bar.progress(100, text="Mock recon sweep complete (Presentation Mode).")
            else:
                is_cloud = runtime_mode.is_cloud_deployment()
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
        if missing:
            st.info(f"📋 Missing required fields: {', '.join(missing)}. Enter your details in the **👤 Profile** tab.")
        else:
            st.info("Click the button above to run the full spectrum recon.")
        return

    summary = findings.get("summary", {})
    vectors = findings.get("vectors", {})
    fp_count = vectors.get("footprint", {}).get("count", 0)
    email_count = vectors.get("email", {}).get("count", 0)
    total_exposure = summary.get("total_exposures", 0)
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📊 Exposure Score", f"🔴 {total_exposure}" if total_exposure > 2 else f"🟡 {total_exposure}" if total_exposure > 0 else f"🟢 {total_exposure}")
    c2.metric("🔴 Critical Breaches", email_count)
    c3.metric("👤 Exposed Handles", fp_count)
    c4.metric("📬 Deletion Targets", len(brokers_df))

    # Building the dossier costs a few hundred milliseconds, which is fine
    # once per sweep and wasteful on every unrelated widget rerun -- so it
    # is cached against the findings that produced it and dropped when a
    # new sweep lands. A failure here must never take down the dashboard
    # the presenter is standing in front of.
    if "osint_dossier_pdf" not in st.session_state:
        try:
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

    _section_header(1, "👤", "Online Identity &amp; Media Exposure")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            _render_osint_vector(findings.get("footprint", {}), "Social & Platform Footprint")
        with c2:
            st.markdown("##### 🖼️ Public Media & Avatars")
            fp_records = findings.get("footprint", {}).get("records", [])
            em_records = findings.get("email", {}).get("records", [])
            found_avatars = False
            for r in fp_records + em_records:
                if isinstance(r, dict) and r.get("avatar_url"):
                    st.image(r["avatar_url"], width=64, caption=r.get("platform") or r.get("service"))
                    found_avatars = True
            if not found_avatars:
                st.caption("No avatars discovered.")
                
    _section_header(2, "🔐", "Data Exposures &amp; Breaches")
    with st.container(border=True):
        # Email exposure section with detailed diagnostics
        email_vector = findings.get("email", {})
        email_count = email_vector.get("count", 0)

        if not profile.get("email"):
            st.markdown("##### 📧 Email & Identity Exposure  ·  `⚪ Not Available`")
            st.warning("No email address provided. Enter your email in the 👤 Profile tab to scan for email exposures (Gravatar, PGP, breaches, etc.)")
        elif not email_vector or email_vector.get("status") == "unavailable":
            st.markdown("##### 📧 Email & Identity Exposure  ·  `⚪ Scan Failed`")
            st.caption(f"Email scan failed for: {profile.get('email')}")
            st.caption("This could be due to: timeout, API limits, or network issues. Try again in a moment.")
        elif email_count == 0:
            st.markdown("##### 📧 Email & Identity Exposure  ·  `🟢 Clean`")
            st.caption(f"No exposures found for {profile.get('email')} in:")
            cols = st.columns(2)
            cols[0].caption("• Gravatar profiles\n• PGP key servers")
            cols[1].caption("• Known breaches\n• DNS validation")
        else:
            _render_osint_vector(email_vector, f"📧 Email & Identity Exposure ({email_count} findings)")

        st.divider()

        # GitHub exposure section
        _render_osint_vector(findings.get("github", {}), "Developer & Code Exposure")

    _section_header(3, "🏛️", "Legal, Financial &amp; Corporate Footprint")
    with st.container(border=True):
        sec_col, fec_col, court_col = st.columns(3)
        with sec_col:
            _render_osint_vector(findings.get("sec", {}), "SEC Filings")
        with fec_col:
            _render_osint_vector(findings.get("fec", {}), "FEC Contributions")
        with court_col:
            _render_osint_vector(findings.get("courtlistener", {}), "Court Dockets")
            
    with st.container(border=True):
        _render_osint_vector(findings.get("infrastructure", {}), "Domains & Certificates")

    _section_header(4, "⚔️", "Statutory Action Plan")
    with st.container(border=True):
        letters_component.render(brokers_df)
