"""Removals — opt-out records and their lifecycle.

chino's OptOutTab, minus the autonomous submission. chino's OptOutEngine
marked removals SUBMITTED on its own; this app does not submit on a user's
behalf without a person pressing the button, so a removal that automation
cannot finish is recorded REQUIRES_MANUAL and queued.
"""
from components import page_shell

db_path = page_shell.setup("Removals", "✉️")

import streamlit as st

import broker_ledger
from agent_engines import RemovalPlanner
from broker_agent_models import RemovalStatus

_, review_db = page_shell.ledger_paths()

st.title("✉️ Removals")
st.caption("One record per broker that held your data, from found to confirmed.")

profile = page_shell.profile_picker(db_path, key="removals_profile")

if profile:
    stats = broker_ledger.get_dashboard_stats(db_path)
    cols = st.columns(4)
    cols[0].metric("Pending", stats["removals_pending"])
    cols[1].metric("Submitted", stats["removals_submitted"])
    cols[2].metric("Confirmed", stats["removals_confirmed"])
    cols[3].metric("Failed", stats["removals_failed"])

    action_cols = st.columns(2)
    with action_cols[0]:
        if st.button("Open removals for found records", type="primary"):
            created = RemovalPlanner(db_path, review_db).prepare_removals(profile["id"])
            st.success(f"Opened {len(created)} removal record(s).")
            st.rerun()
    with action_cols[1]:
        if st.button("Retry failed"):
            count = RemovalPlanner(db_path, review_db).retry_failed(profile["id"])
            st.success(f"Reset {count} removal(s) to pending.")
            st.rerun()

    st.info(
        "**Automation does not submit.** It fills and stops, and files the "
        "submission as a queue item for you. Submitting an opt-out is a legal "
        "assertion in your name — a person makes it.",
        icon="🛡️",
    )

    st.divider()
    removals = broker_ledger.get_removals(db_path, profile_id=profile["id"])
    if not removals:
        page_shell.empty_state("No removals yet.",
                               "Run a scan first — removals open for brokers that "
                               "actually had a record.")
    else:
        st.dataframe(
            [
                {
                    "Broker": r.get("broker_name") or r["broker_id"],
                    "Status": r["status"],
                    "Method": r.get("submission_method") or "—",
                    "Retries": f"{r['retry_count']}/{r['max_retries']}",
                    "Submitted": str(r.get("submitted_at") or "—")[:19],
                    "Confirmed": str(r.get("confirmed_at") or "—")[:19],
                    "Note": (r.get("notes") or r.get("failure_reason") or "")[:70],
                }
                for r in removals
            ],
            width="stretch", hide_index=True,
        )
