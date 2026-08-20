"""
Online Footprint: scan a handle across the WhatsMyName platform list to
surface accounts the user forgot they had.

This is the discovery half of the app's loop. The broker pages deal with
records companies hold about you; this deals with accounts you created
yourself and stopped thinking about, which are just as much exposure and
are the only kind you can close directly. Anything found here can be
pushed into a worklist and triaged toward closure.

Scan results live in session state and the scan itself only runs on an
explicit button press -- Streamlit reruns the whole script on every
widget interaction, and a scan that re-fired on each keystroke would
mean hundreds of outbound requests nobody asked for.

Progress is drawn from inside the scanner's callback, which is safe
because scan_account() owns the event loop on this same script thread
for the duration of the call.
"""
import pandas as pd
import streamlit as st

import config
import runtime_mode
import demo_data
import discovered_accounts
import footprint_scanner
import wmn_dataset
from applog import get_logger

_log = get_logger("footprint")

CONFIDENCE_LABEL = {
    footprint_scanner.CONFIRMED: "🟢 Confirmed",
    footprint_scanner.POSSIBLE: "🟡 Review",
}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_dataset(refresh: bool):
    """Cached so the ~250 KB parse doesn't repeat on every rerun. Keyed on
    `refresh` so the refresh button actually bypasses the cache instead of
    being swallowed by it."""
    return wmn_dataset.ensure_dataset(
        config.WMN_DATASET_PATH, refresh=refresh, timeout=config.FOOTPRINT_TIMEOUT_SECONDS
    )


def _run_mock_scan(handle):
    """Demo-mode stand-in. A real sweep would send ~50 requests from the
    shared demo host on behalf of whoever is at the keyboard -- one IP
    scanning for strangers, which gets rate-limited fast and puts an
    audience member's handle on the wire. Ticks through the same progress
    UI so the demo still shows the mechanic."""
    import time

    results = demo_data.mock_scan_results(handle)
    progress = st.progress(0.0, text=f"Scanning {len(results)} platforms…")
    for index, result in enumerate(results, start=1):
        if result["confidence"] == footprint_scanner.CONFIRMED:
            label = f"Found: {result['platform']} ({index}/{len(results)})"
        else:
            label = f"Scanning {len(results)} platforms… ({index}/{len(results)})"
        progress.progress(index / len(results), text=label)
        time.sleep(0.05)
    progress.empty()
    return results


def _run_scan(handle, sites):
    """Scan with a live progress bar, returning every result."""
    progress = st.progress(0.0, text=f"Scanning {len(sites)} platforms…")

    def on_progress(completed, total, result):
        if result["confidence"] == footprint_scanner.CONFIRMED:
            label = f"Found: {result['platform']} ({completed}/{total})"
        else:
            label = f"Scanning {total} platforms… ({completed}/{total})"
        progress.progress(completed / total, text=label)

    try:
        results = footprint_scanner.scan_account(
            handle,
            sites,
            concurrency=config.FOOTPRINT_CONCURRENCY,
            timeout=config.FOOTPRINT_TIMEOUT_SECONDS,
            on_progress=on_progress,
        )
    finally:
        progress.empty()
    return results


def _results_frame(results):
    return pd.DataFrame(
        [
            {
                "Platform": r["platform"],
                "Category": r["category"],
                "Confidence": CONFIDENCE_LABEL.get(r["confidence"], r["confidence"]),
                "Profile URL": r["profile_url"],
                "Why": r["reason"],
            }
            for r in results
        ]
    )


def _render_saved_accounts():
    purged = discovered_accounts.purge_finished(
        runtime_mode.db_path(), config.PII_RETENTION_DAYS
    )
    if purged:
        st.toast(f"Cleared {purged} finished account(s) past retention.", icon="🗑️")

    saved = discovered_accounts.get_all(runtime_mode.db_path())
    if not saved:
        st.info("Nothing saved yet. Run a scan above and save what it finds to build a closure worklist.")
        return

    st.caption(
        f"🔒 Accounts marked Closed or Ignored are cleared automatically after "
        f"{config.PII_RETENTION_DAYS} days."
    )

    for row in saved:
        with st.container(border=True):
            cols = st.columns([3, 2, 2, 2, 1])
            cols[0].markdown(f"**{row['platform']}**  \n{row['category'] or '—'}")
            confidence = CONFIDENCE_LABEL.get(row["confidence"], row["confidence"])
            cols[1].markdown(f"{confidence}  \nFound: {row['discovered_date']}")

            if row["profile_url"]:
                cols[2].link_button("Open profile", row["profile_url"], icon="🔗", width="stretch")

            new_status = cols[3].selectbox(
                "Status",
                discovered_accounts.STATUS_OPTIONS,
                index=discovered_accounts.STATUS_OPTIONS.index(row["status"]),
                key=f"footprint_status_{row['id']}",
                label_visibility="collapsed",
            )
            if new_status != row["status"]:
                discovered_accounts.update_status(runtime_mode.db_path(), row["id"], new_status)
                st.rerun()

            if cols[4].button("", icon="🗑️", key=f"footprint_delete_{row['id']}",
                              help=f"Remove {row['platform']}"):
                discovered_accounts.delete_account(runtime_mode.db_path(), row["id"])
                st.rerun()


def render():
    st.title("🌐 Online footprint")
    if runtime_mode.live_scanning_enabled():
        st.caption(
            "Find accounts you forgot you made. Every check runs from this machine — "
            "no API keys, no third-party service, nothing about your handle is uploaded anywhere."
        )
    else:
        st.caption(
            "Find accounts you forgot you made. This hosted demo returns sample matches "
            "instead of scanning — no requests are sent from this server on your behalf."
        )

    if "footprint_results" not in st.session_state:
        st.session_state.footprint_results = None
        st.session_state.footprint_handle = ""

    with st.container(border=True):
        handle = st.text_input(
            "👤 Username / handle to scan",
            value=st.session_state.footprint_handle,
            placeholder="the handle you've reused for years",
        )

        st.text_input(
            "✉️ Email address to scan",
            placeholder="Coming soon — email lookups are not enabled yet",
            disabled=True,
        )
        st.caption(
            "Email scanning is deliberately off. Probing platforms by email triggers "
            "account-existence alerts and password-reset mail, so it needs a hand-vetted "
            "list of non-notifying lookups rather than the username list reused blindly."
        )

        deep = st.toggle(
            "Deep recon (full ~700-site list)",
            help="Off scans ~50 core platforms in a few seconds. On is far more thorough "
                 "and noticeably slower, with more rate-limit pressure.",
        )
        scan_clicked = st.button("Scan footprint", icon="🛰️", type="primary", disabled=not handle.strip())

    if scan_clicked and not runtime_mode.live_scanning_enabled():
        st.session_state.footprint_results = _run_mock_scan(handle.strip())
        st.session_state.footprint_handle = handle.strip()
        st.rerun()

    if scan_clicked:
        try:
            with st.spinner("Loading platform list…"):
                dataset, status = _load_dataset(refresh=False)
        except Exception as exc:
            _log.error("Could not load WhatsMyName dataset: %s", exc)
            st.error(
                "Couldn't load the platform list. This needs internet access the first time "
                "so it can download and cache the site data — check your connection and retry."
            )
            return

        sites = wmn_dataset.select_sites(dataset, deep=deep)
        _, missing = wmn_dataset.resolve_fast_sites(dataset)
        if missing and not deep:
            st.warning(
                f"{len(missing)} core platform(s) are no longer in the upstream list and were "
                f"skipped: {', '.join(missing)}"
            )

        st.session_state.footprint_results = _run_scan(handle, sites)
        st.session_state.footprint_handle = handle.strip()
        st.rerun()

    results = st.session_state.footprint_results
    if results is None:
        st.caption(_attribution_line())
        return

    summary = footprint_scanner.summarize(results)
    found = footprint_scanner.discoveries(results)

    cols = st.columns(4)
    cols[0].metric("Confirmed", summary[footprint_scanner.CONFIRMED])
    cols[1].metric("Needs review", summary[footprint_scanner.POSSIBLE])
    cols[2].metric("Not found", summary[footprint_scanner.NOT_FOUND])
    cols[3].metric("Unreachable", summary[footprint_scanner.ERROR])

    if not found:
        st.success(
            f"No accounts surfaced for **{st.session_state.footprint_handle}** across "
            f"{len(results)} platforms."
        )
        return

    st.subheader(f"{len(found)} account(s) for “{st.session_state.footprint_handle}”")
    st.caption(
        "🟢 Confirmed means the platform returned its own account-exists signal. "
        "🟡 Review means the response was ambiguous — a CAPTCHA wall or a "
        "JavaScript-rendered page — so it needs a human look rather than being discarded."
    )

    st.dataframe(
        _results_frame(found),
        width="stretch",
        hide_index=True,
        column_config={
            "Profile URL": st.column_config.LinkColumn("Profile URL", display_text="Open"),
            "Why": st.column_config.TextColumn("Why", width="medium"),
        },
    )

    actions = st.columns(2)
    actions[0].download_button(
        "Export footprint (.csv)",
        icon="📋",
        data=_results_frame(found).to_csv(index=False).encode("utf-8"),
        file_name=f"footprint_{st.session_state.footprint_handle}.csv",
        mime="text/csv",
        width="stretch",
    )
    if actions[1].button("Add to audit tracker", icon="📌", type="primary", width="stretch"):
        saved = discovered_accounts.save_discoveries(runtime_mode.db_path(), found)
        st.success(f"Saved {saved} account(s) to your closure worklist below.")

    st.divider()
    st.subheader("Closure worklist")
    _render_saved_accounts()
    st.caption(_attribution_line())


@st.cache_data(ttl=3600, show_spinner=False)
def _attribution_line():
    """Attribution is required by CC BY-SA whether or not a scan has run.
    Cached because it would otherwise reparse the whole 250 KB site list on
    every rerun just to draw one credit line, and falls back to a generic
    credit so a missing cache never costs a download."""
    cached = wmn_dataset.read_cache(config.WMN_DATASET_PATH)
    return wmn_dataset.attribution(cached or {})
