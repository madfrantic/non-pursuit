"""Queue — the work automation deliberately stopped short of.

chino's QueueTab, backed by utils/review_queue.py rather than chino's
separate manual_queue table. One queue, one closed reason vocabulary; see
utils/broker_ledger.py for why the second table was not ported.
"""
from components import page_shell

page_shell.setup("Queue", "📋")

import streamlit as st

import config
import review_queue

st.title("📋 Review queue")
st.caption("Every item here is a step a machine refused to take on your behalf.")

db = config.REVIEW_QUEUE_DB_PATH
stats = review_queue.stats(db)

cols = st.columns(4)
cols[0].metric("Pending", stats.get("pending", 0))
cols[1].metric("Resolved", stats.get("resolved", 0))
cols[2].metric("Skipped", stats.get("skipped", 0))
cols[3].metric("Total", stats.get("total", 0))

st.divider()

pending = review_queue.pending_items(db)
if not pending:
    page_shell.empty_state("Nothing waiting on you.",
                           "Items appear here when a scan or opt-out hits a CAPTCHA, "
                           "an identity check, or a broker with no automator.")
else:
    for item in pending:
        with st.container(border=True):
            head, action = st.columns([4, 1])
            with head:
                st.markdown(f"**{item['target']}** — {item['task_type']}")
                st.caption(item["guidance"])
                if item.get("note"):
                    st.text(item["note"])
                if item.get("current_url"):
                    st.markdown(f"[Open the page →]({item['current_url']})")
                if item.get("screenshot_path"):
                    st.caption(f"Screenshot: `{item['screenshot_path']}`")
                st.caption(f"Queued {str(item['created_at'])[:19]} · priority {item['priority']}")
            with action:
                # Addressed by row id, never by position -- see the
                # review_queue module docstring for why that distinction
                # is load-bearing.
                if st.button("Done", key=f"done_{item['id']}", type="primary"):
                    review_queue.resolve_item(db, item["id"], review_queue.RESOLVED)
                    st.rerun()
                if st.button("Skip", key=f"skip_{item['id']}"):
                    review_queue.resolve_item(db, item["id"], review_queue.SKIPPED)
                    st.rerun()

with st.expander("Closed items"):
    closed = [i for i in review_queue.all_items(db) if i["status"] != review_queue.PENDING]
    if closed:
        st.dataframe(
            [{"Target": i["target"], "Task": i["task_type"], "Reason": i["reason"],
              "Outcome": i["status"], "Closed": str(i.get("resolved_at") or "")[:19]}
             for i in closed],
            width="stretch", hide_index=True)
    else:
        st.caption("None yet.")
