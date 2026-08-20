"""
Master Dashboard: the statutory countdowns, the relational exposure map,
and the discovered-accounts worklist on one page.

Results and Online Footprint used to be separate destinations, which
split a single question -- "what is exposed, and what is the clock on
it?" -- across two places the user had to remember to visit. They read
the same profile and write the same SQLite file, so the split was
navigational, not structural.

Tabs rather than one long column: this page carries a broker-by-broker
table, a graphviz chart, and a scan table that can run to hundreds of
rows, and stacking them vertically is how the old Dashboard buried its
own results. The deadline strip above the tabs stays visible in every
tab -- an overdue statutory window is the one thing on this page that
must not be one click away.

Auto-scanning on profile save: when the Dashboard saves a new or changed
profile (name, email, phone, location), the Master Dashboard detects it
via a session flag and automatically runs the reconnaissance sweep. The
sweep is cached per unique (handle, email) pair, so changing just the
location doesn't re-scan, but a new handle does.
"""
import streamlit as st

import config
import runtime_mode
import discovered_accounts
import facial_recognition
import footprint_scanner
import email_scanner
import wmn_dataset
import yandex_osint
from tracker import get_all_requests

from components import footprint as footprint_component
from components import results as results_component
from applog import get_logger

_log = get_logger("master")


def _render_deadline_strip():
    """Statutory countdowns, pinned above the tabs."""
    requests = get_all_requests(runtime_mode.db_path())
    if not requests:
        st.info(
            "No deletion requests logged yet — generate a letter under "
            "**Data Broker Deletion Letters** and the countdown starts here."
        )
        return

    overdue = [r for r in requests if r["is_overdue"]]
    complete = [r for r in requests if r["status"] == "Complete"]
    active = [r for r in requests if r["status"] != "Complete"]

    cols = st.columns(4)
    cols[0].metric("🚨 Overdue", len(overdue))
    cols[1].metric("⏳ Active", len(active))
    cols[2].metric("✅ Complete", len(complete))
    cols[3].metric("📬 Total tracked", len(requests))

    if overdue:
        st.error(
            f"🚨 {len(overdue)} request(s) are past the {config.CCPA_RESPONSE_WINDOW_DAYS}-day "
            "statutory window — that lapse is the enforceable part of this campaign."
        )

    for request in sorted(active, key=lambda r: r["deadline"]):
        window = max(request["response_window_days"], 1)
        elapsed = window - request["days_remaining"]
        label = f"{request['broker_name']} — due {request['deadline']}"
        if request["is_overdue"]:
            label += f" (overdue by {abs(request['days_remaining'])}d)"
        else:
            label += f" ({request['days_remaining']}d left)"
        st.progress(min(max(elapsed / window, 0.0), 1.0), text=label)


def _run_auto_scan():
    """Execute handle + email scans if profile was just saved and scans
    haven't been run yet for this (handle, email) pair.

    Guarded against Streamlit infinite reruns with session flags:
    - profile_saved_auto_scan: set by Dashboard when profile is saved
    - _auto_scan_last_pair: track the last scanned (handle, email) so we
      don't rescan if only location changed
    - _auto_scan_in_progress: temporary flag to avoid scan during current rerun
    """
    if not st.session_state.get("profile_saved_auto_scan"):
        return

    # Avoid running scan multiple times in rapid succession
    if st.session_state.get("_auto_scan_in_progress"):
        return

    handle = (st.session_state.get("footprint_handle") or "").strip()
    email = (st.session_state.get("footprint_email") or "").strip()
    current_pair = (handle, email)
    last_pair = st.session_state.get("_auto_scan_last_pair")

    if current_pair == last_pair and current_pair != ("", ""):
        # Same handle and email as last scan, skip to avoid redundant network hits
        st.session_state.profile_saved_auto_scan = False
        return

    if not (handle or email):
        # No handle or email entered yet, nothing to scan
        st.session_state.profile_saved_auto_scan = False
        return

    # Mark that we're scanning to avoid Streamlit rerun loops
    st.session_state._auto_scan_in_progress = True

    try:
        with st.spinner("🛰️ Scanning for accounts and email associations…"):
            # Load the full dataset
            try:
                dataset, _ = wmn_dataset.ensure_dataset(
                    config.WMN_DATASET_PATH, refresh=False, timeout=config.FOOTPRINT_TIMEOUT_SECONDS
                )
            except Exception as exc:
                _log.error("Could not load WhatsMyName dataset: %s", exc)
                st.error("Couldn't load the platform list. Check your internet connection.")
                return

            sites = wmn_dataset.select_sites(dataset, deep=True)
            results = []

            # Scan handle if provided
            if handle:
                results = footprint_scanner.scan_account(
                    handle,
                    sites,
                    concurrency=config.FOOTPRINT_CONCURRENCY,
                    timeout=config.FOOTPRINT_TIMEOUT_SECONDS,
                    per_host=config.FOOTPRINT_PER_HOST_CONCURRENCY,
                )

            # Scan email if provided
            email_results = []
            if email:
                email_results = email_scanner.scan_email(email)

            # Persist to discovered_accounts table
            all_discoveries = []
            all_discoveries.extend(results)
            all_discoveries.extend(email_scanner.email_discoveries(email_results))

            if all_discoveries:
                saved = discovered_accounts.save_discoveries(
                    runtime_mode.db_path(), all_discoveries
                )
                st.success(f"✅ Auto-scan complete: {saved} account(s) and associations found.")
            else:
                st.info("No accounts or associations found in this scan.")

        st.session_state._auto_scan_last_pair = current_pair
    finally:
        st.session_state.profile_saved_auto_scan = False
        st.session_state._auto_scan_in_progress = False


def _render_avatar_verification():
    """📷 Visual Identity Verification card.

    Display discovered accounts with avatar images and allow users to
    confirm "this is me" or "false positive" before they're included in
    the audit export. If a master photo is available, suggests whether the
    avatar looks like the user based on DeepFace comparison (local, in-memory,
    never persisted).
    """
    accounts = discovered_accounts.get_all(runtime_mode.db_path())
    if not accounts:
        st.info("No discovered accounts yet. Save your profile above to start scanning.")
        return

    st.subheader("📷 Visual Identity Verification")
    st.caption(
        "Review the profiles and photos discovered during the scan. "
        "Mark each as **Confirmed** if it's actually you, or **False Positive** if it's not. "
        "Only confirmed accounts will be included in your audit exports."
    )

    master_bytes = st.session_state.get("master_face_image_bytes")
    deepface_ready = runtime_mode.facial_recognition_enabled() and facial_recognition.facial_recognition_available()

    for idx, account in enumerate(accounts):
        with st.container(border=True):
            cols = st.columns([2, 1, 3])

            # Platform and identifier
            cols[0].markdown(f"**{account['platform']}**")
            cols[0].caption(f"*{account['target_identifier']}*")

            # Avatar if available
            avatar_url = account.get("avatar_url")
            if avatar_url:
                try:
                    cols[1].image(avatar_url, width=100)
                except Exception as e:
                    cols[1].caption("[Avatar unavailable]")

            # Verification controls: suggestion + toggle
            verdict_col = cols[2]

            # Biometric suggestion if we have master photo and avatar
            suggestion_dict = {}
            if deepface_ready and master_bytes and avatar_url:
                suggestion_dict = facial_recognition.suggest_verification(master_bytes, avatar_url)

            if suggestion_dict.get("available") and suggestion_dict.get("suggested_label"):
                verdict_col.markdown(suggestion_dict["suggested_label"])
                if suggestion_dict.get("verified"):
                    if verdict_col.button(
                        f"✅ Confirm suggested match",
                        key=f"confirm_suggest_{account['id']}",
                        help="Clicks to Confirmed in the dropdown below.",
                    ):
                        discovered_accounts.update_verification(
                            runtime_mode.db_path(),
                            account["id"],
                            discovered_accounts.VERIFIED_CONFIRMED,
                        )
                        st.session_state.pop("audit_zip", None)
                        st.rerun()

            # Manual verification toggle always present
            current_status = account.get("verification_status", discovered_accounts.VERIFIED_UNREVIEWED)
            new_status = verdict_col.selectbox(
                "Your verdict:",
                options=[
                    discovered_accounts.VERIFIED_UNREVIEWED,
                    discovered_accounts.VERIFIED_CONFIRMED,
                    discovered_accounts.VERIFIED_FALSE_POSITIVE,
                ],
                index=[
                    discovered_accounts.VERIFIED_UNREVIEWED,
                    discovered_accounts.VERIFIED_CONFIRMED,
                    discovered_accounts.VERIFIED_FALSE_POSITIVE,
                ].index(current_status),
                key=f"verify_{account['id']}",
                label_visibility="collapsed",
            )

            if new_status != current_status:
                discovered_accounts.update_verification(
                    runtime_mode.db_path(), account["id"], new_status
                )
                st.session_state.pop("audit_zip", None)  # Invalidate cached audit
                st.rerun()

            # Yandex reverse search link if avatar is available
            if avatar_url:
                yandex_url = yandex_osint.build_reverse_image_search_url(avatar_url)
                if yandex_url:
                    verdict_col.link_button(
                        "🔍 Reverse search on Yandex",
                        yandex_url,
                        help="Open a Yandex reverse-image search for this avatar in a new tab.",
                    )


def render(brokers_df):
    st.title("🛰️ Master Dashboard")
    st.caption(
        "Every exposure signal and every statutory clock in one place — broker listings, "
        "household linkages, and the accounts a footprint sweep turned up."
    )

    # Trigger auto-scan if profile was just saved
    _run_auto_scan()

    with st.container(border=True):
        st.markdown("##### ⚖️ CCPA statutory countdowns")
        _render_deadline_strip()

    exposure_tab, map_tab, accounts_tab = st.tabs([
        "🏢 Broker exposure",
        "🕸️ Relational Entity Exposure Map",
        "👤 Discovered accounts",
    ])

    with exposure_tab:
        results_component.render(brokers_df, show_title=False)

    with map_tab:
        results_component.render_entity_map()
        st.caption(
            "Add or edit associated individuals under 🔗 Household & Relational Entities "
            "on the Dashboard."
        )

    with accounts_tab:
        # Show avatar verification first, then the scan interface
        _render_avatar_verification()
        st.divider()
        st.markdown("##### or run a manual scan")
        footprint_component.render(show_title=False)
