"""
The owner's view of everything this install has been told.

Every other screen in Non-Pursuit shows one slice of the case file: the
profile form, the demands, the discovered accounts, the agent ledger. This
is the only place they are put side by side, which is useful for the person
who owns the install and is precisely the view that must not exist for
anyone else -- hence utils/admin_auth.py in front of it.

WHAT IT DOES NOT DO

It reads. There is no edit, no re-run, no delete-everything button here:
a screen that aggregates every record in the database is the worst place
to also put a destructive control, and each of those actions already has
a home on the page that owns that data.

Values are shown decrypted, because a list of Fernet ciphertexts answers
no question the owner has. That is the same decision every other page
makes -- what is different here is the breadth, which is why the gate is
a separate secret rather than the vault password.
"""
from datetime import datetime

import pandas as pd
import streamlit as st

import admin_auth
import broker_ledger
import config
import database
import discovered_accounts
import exposure_store
import runtime_mode
import usage_metrics
from tracker import get_all_requests

from components import theme


def _prompt() -> None:
    """The unlock form. Rendered instead of the dashboard, never beside it."""
    theme.masthead("restricted", "Owner console &nbsp;·&nbsp; <b>authentication required</b>")
    st.caption(
        "This console lists every record held by this install. It is not part "
        "of the app a visitor uses."
    )
    with st.form("admin_unlock"):
        candidate = st.text_input("Admin key", type="password")
        submitted = st.form_submit_button("Unlock console", type="primary")
    if not submitted:
        return
    if admin_auth.verify(candidate):
        st.session_state[admin_auth.AUTHENTICATED_KEY] = True
        st.rerun()
    else:
        # No distinction between "wrong key" and "no key configured" --
        # the difference is only useful to someone who should not be here.
        st.error("Rejected.")


def require_admin() -> bool:
    """Render the gate if needed. True when the console may draw.

    Returns rather than calling st.stop() so a caller can decide what to
    do with a refusal; pages/11_Admin.py stops, and app.py never reaches
    here at all because nav does not draw the link.
    """
    if not admin_auth.is_configured():
        theme.masthead("not configured", "Owner console &nbsp;·&nbsp; <b>no admin key set</b>")
        st.info(
            f"Set `{admin_auth.ADMIN_KEY_ENV}` in your `.env` (or in Streamlit "
            "secrets) and restart the app to enable this console."
        )
        return False
    if admin_auth.is_authenticated(st.session_state):
        return True
    _prompt()
    return False


def _profiles_frame(db_path: str) -> pd.DataFrame:
    rows = database.get_all_target_profiles(db_path)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    # relational_entities is a list of dicts -- readable as text, unusable
    # as a dataframe cell.
    if "relational_entities" in frame.columns:
        frame["relational_entities"] = frame["relational_entities"].apply(
            lambda v: ", ".join(e.get("name", "") for e in (v or [])) or "—")
    return frame


def _section(title: str, frame: pd.DataFrame, empty: str) -> int:
    """One table plus its own CSV. Returns the row count.

    Per-section export rather than one archive button: the owner asking
    "what do I hold about brokers" and the owner asking "what did the
    agent do" are different questions, and the full bundle already exists
    as the audit package in the main app's sidebar.
    """
    st.markdown(f"##### {title}")
    if frame.empty:
        st.caption(empty)
        st.markdown("")
        return 0
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        f"Export {title.lower()} (.csv)",
        data=frame.to_csv(index=False).encode("utf-8"),
        file_name=f"non_pursuit_admin_{title.lower().replace(' ', '_')}"
                  f"_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        key=f"admin_export_{title}",
    )
    st.markdown("")
    return len(frame)


def render() -> None:
    """Draw the console. Assumes require_admin() already returned True."""
    db_path = runtime_mode.db_path()

    theme.masthead(
        "owner console",
        f"{config.APP_TITLE} &nbsp;·&nbsp; <b>all stored records</b> "
        f"&nbsp;·&nbsp; {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    )

    if admin_auth.key_is_weak():
        st.warning(
            f"The configured admin key is shorter than "
            f"{admin_auth.MIN_KEY_LENGTH} characters. This console returns "
            "every record in the database — lengthen it."
        )

    if runtime_mode.is_demo_mode():
        st.info(
            "Demo mode: this session reads a throwaway per-visitor database, "
            "so what follows is that session's synthetic data, not the real "
            "case file."
        )

    profiles = _profiles_frame(db_path)
    requests = pd.DataFrame(get_all_requests(db_path))
    accounts = pd.DataFrame(discovered_accounts.get_all(db_path))

    checks = exposure_store.get_all_checks(db_path)
    exposure = pd.DataFrame(
        [{"category": k, **(v if isinstance(v, dict) else {"value": v})}
         for k, v in (checks or {}).items()]
    )

    # The agent ledger lives in the same file but its tables are only
    # created once the console has been opened, so a fresh install has
    # none of them -- read defensively rather than assuming a schema.
    try:
        ledger_profiles = pd.DataFrame(broker_ledger.get_profiles(db_path, active_only=False))
        scans = pd.DataFrame(broker_ledger.get_scans(db_path))
        removals = pd.DataFrame(broker_ledger.get_removals(db_path))
        activity = pd.DataFrame(broker_ledger.get_activity_log(db_path, limit=500))
    except Exception as exc:  # noqa: BLE001 -- a missing table must not 500 the page
        st.caption(f"Agent ledger unavailable: {exc}")
        ledger_profiles = scans = removals = activity = pd.DataFrame()

    cols = st.columns(4)
    cols[0].metric("Profile saves", len(profiles))
    cols[1].metric("Demands", len(requests))
    cols[2].metric("Discovered accounts", len(accounts))
    cols[3].metric("Agent scans", len(scans))

    st.markdown('<div class="np-rule"></div>', unsafe_allow_html=True)

    tabs = st.tabs(["Identity", "Demands", "Recon", "Agent ledger", "Usage"])

    with tabs[0]:
        _section("Profile saves", profiles,
                 "No profile has been saved on this install.")
        st.caption(
            "One row per save, newest first — the profile form appends "
            "rather than overwrites, so this is the edit history."
        )

    with tabs[1]:
        _section("Deletion demands", requests,
                 "No deletion requests logged.")

    with tabs[2]:
        _section("Discovered accounts", accounts,
                 "No accounts discovered yet.")
        _section("Exposure checks", exposure,
                 "No exposure checks recorded.")

    with tabs[3]:
        _section("Agent profiles", ledger_profiles, "No agent profiles.")
        _section("Scans", scans, "No scans recorded.")
        _section("Removals", removals, "No removals recorded.")
        _section("Activity log", activity, "No agent activity logged.")

    with tabs[4]:
        counts = usage_metrics.summary(config.USAGE_METRICS_DB_PATH)
        _section(
            "Feature usage",
            pd.DataFrame(counts, columns=["event", "count"]) if counts else pd.DataFrame(),
            "No usage recorded.",
        )
        st.caption(
            "Event counts only. usage_metrics never records an entered "
            "value, so nothing here is PII."
        )

    st.markdown('<div class="np-rule"></div>', unsafe_allow_html=True)
    if st.button("Lock console"):
        admin_auth.sign_out(st.session_state)
        st.rerun()
