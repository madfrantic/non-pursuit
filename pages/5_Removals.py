"""Removals — opt-out records and their lifecycle.

chino's OptOutTab, minus the autonomous submission. chino's OptOutEngine
marked removals SUBMITTED on its own; this app does not submit on a user's
behalf without a person pressing the button, so a removal that automation
cannot finish is recorded REQUIRES_MANUAL and queued.

WHY THE GOOGLE SECTION READS A DIFFERENT TABLE

The removals list above it comes from broker_ledger, whose vocabulary is
RemovalStatus (pending/submitted/confirmed). DELISTED is not in that
vocabulary -- it belongs to campaign_manager's broker_campaigns ledger,
which is the one that tracks a record from statutory demand through
verified takedown. Google escalation keys off DELISTED specifically,
because Refresh Outdated Content only actions a URL that is already gone,
so this section joins to that ledger rather than the one driving the
table. Two ledgers on one page is not ideal, but showing the escalation
anywhere else would put it away from the removals work it finishes.
"""
from components import page_shell

db_path = page_shell.setup("Removals", "✉️")

import streamlit as st

import broker_ledger
import campaign_manager
import profile_state
import remediation
import runtime_mode
from agent_engines import RemovalPlanner

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

# --- Google search-result escalation ---------------------------------
# A broker taking the record down does not remove it from Google. This
# is where a verified takedown becomes the next request rather than the
# end of the job.
st.divider()
st.subheader("🚫 Escalate to Google")

campaign_db = runtime_mode.db_path()
campaign_manager.init_db(campaign_db)
delisted = [
    c for c in campaign_manager.get_all_campaigns(campaign_db)
    if c["effective_status"] == campaign_manager.STATUS_DELISTED
]
escalations = remediation.plan_google_escalations(
    delisted, profile_state.get_profile(st.session_state), eligible_only=False)
summary = remediation.google_escalation_summary(escalations)

if not escalations:
    page_shell.empty_state(
        "Nothing delisted yet.",
        "Once a broker record is verified gone, it shows up here so you can ask "
        "Google to drop the search result that outlives it.")
else:
    st.caption(
        f"{summary['eligible']} of {summary['total']} delisted record(s) can be "
        f"escalated. The broker page is gone; the search result may not be.")

    for item in escalations:
        title = f"**{item['broker_name']}** · {item['domain']}"
        with st.container(border=True):
            head = st.columns([4, 2, 2])
            head[0].markdown(title)
            head[1].markdown(
                f":gray[delisted {item['delisted_on']}]" if item["delisted_on"] else "")
            head[2].link_button(
                "Check if still indexed", item["index_probe_url"],
                width="stretch", icon="🔎")

            if not item["eligible"]:
                st.caption(f":orange[Not escalatable — {item['blocked_reason']}]")
                continue

            st.code(item["record_url"], language=None)
            st.caption("Copy this URL — both Google tools identify the content by it.")

            action_cols = st.columns(len(item["actions"]))
            for col, action in zip(action_cols, item["actions"]):
                with col:
                    col.link_button(
                        action["label"], action["url"], width="stretch",
                        type="primary" if action["primary"] else "secondary",
                        icon="🔗")
                    with st.expander("What this does"):
                        st.write(action["why"])
                        st.write(f"**How:** {action['how']}")
                        st.caption(action["note"])

    st.info(
        "**Neither form can be pre-filled.** Both are multi-step Google forms, not "
        "links that accept a URL parameter — so the button opens the tool and the "
        "URL above is what you paste into it. Google's Indexing API is not an "
        "option here: it is restricted to job-posting and broadcast-event pages, "
        "and its removal verb only accepts domains you own.",
        icon="ℹ️")
