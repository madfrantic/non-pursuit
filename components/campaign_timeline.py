"""
Deletion timeline: the active compliance pipeline, broker by broker.

The Opt-Out Tracker answers "what have I sent?". This view answers the
question that actually matters to someone waiting on a deletion: for each
broker, how much of the statutory window is left, and which ones have
already blown it and need escalating.

Rows are rendered one at a time rather than as a dataframe because each
one carries actions -- start the clock, run a verification ping, escalate
-- and a dataframe can't hold buttons. Same row-of-columns idiom the
Opt-Out Tracker page already uses, so the two read as one app.
"""
from datetime import datetime

import streamlit as st

import campaign_manager
import config
import recon_worker
import runtime_mode

# Effective status -> (badge, colour name for st.markdown's :color[] syntax).
_BADGES = {
    campaign_manager.STATUS_DISCOVERED: ("⚪ DISCOVERED", "gray"),
    campaign_manager.STATUS_DISPATCHED: ("🟡 DISPATCHED", "orange"),
    campaign_manager.STATUS_CLOCK_ACTIVE: ("🟡 CLOCK ACTIVE", "orange"),
    campaign_manager.STATUS_DELISTED: ("🟢 DELISTED", "green"),
    campaign_manager.STATUS_NON_COMPLIANT: ("🔴 NON-COMPLIANT", "red"),
}


def _badge(effective_status: str) -> str:
    label, colour = _BADGES.get(effective_status, (effective_status, "gray"))
    return f":{colour}[{label}]"


def _remaining_label(record: dict) -> str:
    """The right-hand 'what now' column for one campaign."""
    status = record["effective_status"]
    days = record["days_remaining"]

    if status == campaign_manager.STATUS_DELISTED:
        removed = record.get("date_verified_removed")
        dispatched = record.get("date_dispatched")
        if removed and dispatched:
            try:
                day_count = (
                    datetime.strptime(removed, campaign_manager.DATE_FORMAT).date()
                    - datetime.strptime(dispatched, campaign_manager.DATE_FORMAT).date()
                ).days
                return f"Delisted on day {day_count}"
            except ValueError:
                pass
        return "Delisted"

    if status == campaign_manager.STATUS_NON_COMPLIANT:
        overdue_by = abs(days) if days is not None else 0
        return f":red[{overdue_by} day(s) overdue]"

    if days is None:
        return "Not yet dispatched"
    return f"{days} day(s) remaining"


def _render_row(record: dict, db_path: str) -> None:
    cols = st.columns([3, 2, 2, 2, 2, 2])

    domain = record.get("domain") or ""
    cols[0].markdown(f"**{record['broker_name']}**  \n:gray[{domain}]")
    cols[1].markdown(record.get("date_dispatched") or ":gray[—]")
    cols[2].markdown(record.get("statutory_deadline") or ":gray[—]")
    cols[3].markdown(_badge(record["effective_status"]))
    cols[4].markdown(_remaining_label(record))

    with cols[5]:
        status = record["effective_status"]
        campaign_id = record["id"]

        if status == campaign_manager.STATUS_DISCOVERED:
            if st.button(
                "Mark dispatched", key=f"ct_dispatch_{campaign_id}", width="stretch",
                help=f"Start the {config.CCPA_RESPONSE_WINDOW_DAYS}-day statutory clock",
            ):
                campaign_manager.mark_dispatched(db_path, campaign_id)
                st.rerun()

        elif status == campaign_manager.STATUS_DELISTED:
            st.markdown(":green[Case closed]")

        else:
            if st.button(
                "Verify now", key=f"ct_verify_{campaign_id}", width="stretch",
                help="Ping the broker endpoint and record whether the profile still resolves",
            ):
                _run_verification(db_path, record)
                st.rerun()

            if status == campaign_manager.STATUS_NON_COMPLIANT:
                if st.button(
                    "Escalate", key=f"ct_escalate_{campaign_id}", width="stretch",
                    help="Log a regulatory escalation against this broker",
                ):
                    campaign_manager.mark_non_compliant(
                        db_path, campaign_id,
                        evidence="Escalation logged: statutory window expired without removal.",
                    )
                    st.rerun()


def _run_verification(db_path: str, record: dict) -> None:
    """One manual verification ping, with the result surfaced as a toast.

    Gated on the same live-scanning switch the footprint scanner uses --
    the hosted build must not make outbound requests on a visitor's behalf.
    """
    if not runtime_mode.live_scanning_enabled():
        st.toast("Live verification is disabled on this build.", icon="🚫")
        return

    outcome = recon_worker.verify_campaign(db_path, record)
    if outcome["result"] == recon_worker.RESULT_REMOVED:
        st.toast(f"{record['broker_name']}: record is gone — marked delisted.", icon="🟢")
    elif outcome["result"] == recon_worker.RESULT_STILL_LISTED:
        st.toast(f"{record['broker_name']}: still listed ({outcome['detail']}).", icon="🟡")
    else:
        st.toast(f"{record['broker_name']}: inconclusive ({outcome['detail']}).", icon="⚪")


def render(show_title: bool = True) -> None:
    db_path = runtime_mode.db_path()
    campaign_manager.init_db(db_path)

    if show_title:
        st.title("🗓️ Deletion timeline")
        st.caption(
            "Every dispatched demand under its statutory clock. A broker that misses "
            f"its {config.CCPA_RESPONSE_WINDOW_DAYS}-day window turns red and becomes "
            "grounds for a regulatory complaint."
        )

    with st.expander("Add a broker to the pipeline", icon="➕"):
        with st.form("campaign_add_form"):
            c_name = st.text_input("Broker name")
            c_domain = st.text_input("Domain", placeholder="spokeo.com")
            c_url = st.text_input("Profile URL (optional)", placeholder="https://...")
            c_dispatched = st.checkbox("Demand already sent — start the clock now")
            if st.form_submit_button("Add to pipeline") and c_name and c_domain:
                campaign_id = campaign_manager.create_or_update_campaign(
                    db_path, c_name.strip(), c_domain.strip(), c_url.strip() or None
                )
                if c_dispatched:
                    campaign_manager.mark_dispatched(db_path, campaign_id)
                st.success(f"Added {c_name.strip()} to the pipeline.")
                st.rerun()

    counts = campaign_manager.summarize(db_path)
    if not counts["TOTAL"]:
        st.info(
            "No campaigns tracked yet. Add a broker above, or run a search under "
            "**Intelligence Dossier** to discover records worth demanding removal of."
        )
        return

    with st.container(horizontal=True):
        st.metric("Tracked", counts["TOTAL"], border=True)
        st.metric("Clock active", counts[campaign_manager.STATUS_CLOCK_ACTIVE]
                  + counts[campaign_manager.STATUS_DISPATCHED], border=True)
        st.metric("Delisted", counts[campaign_manager.STATUS_DELISTED], border=True)
        st.metric("Non-compliant", counts[campaign_manager.STATUS_NON_COMPLIANT], border=True)

    st.markdown("---")

    header = st.columns([3, 2, 2, 2, 2, 2])
    for col, label in zip(
        header, ["Broker", "Dispatched", "Deadline", "Status", "Remaining", "Action"]
    ):
        col.markdown(f"**{label}**")

    active = campaign_manager.get_active_campaigns(db_path)
    for record in active:
        _render_row(record, db_path)

    closed = [
        r for r in campaign_manager.get_all_campaigns(db_path)
        if r["effective_status"] == campaign_manager.STATUS_DELISTED
    ]
    if closed:
        with st.expander(f"Closed cases ({len(closed)})", icon="✅"):
            for record in closed:
                _render_row(record, db_path)

    if not runtime_mode.persistent_storage_enabled():
        st.caption(
            "🟡 Demo build — campaigns live only in this browser session and are "
            "discarded when you close the tab."
        )
