"""Scanner — search the broker set for a profile's records.

chino's ScannerTab. The scan runs through utils/agent_engines.ScannerEngine,
which is wired to the real broker_probe, not chino's stub handler.
"""
from components import page_shell

db_path = page_shell.setup("Scanner", "🔍")

import streamlit as st

import broker_ledger
import runtime_mode
from agent_engines import RemovalPlanner, ScannerEngine
from broker_agent_models import coerce_difficulty

_, review_db = page_shell.ledger_paths()

st.title("🔍 Scanner")
st.caption("Search each broker for this profile's records and log what came back.")

profile = page_shell.profile_picker(db_path, key="scanner_profile")
brokers = broker_ledger.get_brokers(db_path)

if not brokers:
    page_shell.empty_state(
        "No brokers in the ledger.",
        "Seed them from data/brokers.csv on the Settings page.")
elif profile:
    st.subheader("Brokers")
    for broker in brokers:
        difficulty = coerce_difficulty(broker.get("difficulty"))
        marker = "🚧" if difficulty.queues_immediately else "✅"
        st.markdown(f"{marker} **{broker['name']}** — `{difficulty.value}`")
    st.caption("🚧 goes straight to the review queue: automation must not attempt "
               "a CAPTCHA-gated or manual-only broker.")

    if not runtime_mode.live_scanning_enabled():
        st.warning("Live scanning is disabled in this runtime. Switch to desktop "
                   "mode in the sidebar to run a real sweep.")
    elif st.button("Run scan", type="primary"):
        progress = st.progress(0.0, text="Starting...")

        def on_progress(current, total, message):
            progress.progress(current / max(total, 1), text=message)

        scanner = ScannerEngine(db_path, review_db)
        scanner.set_progress_callback(on_progress)
        with st.spinner("Scanning brokers..."):
            results = scanner.scan_profile(profile["id"])
        progress.empty()
        opened = RemovalPlanner(db_path, review_db).prepare_removals(profile["id"])
        st.success(f"Scanned {len(results)} broker(s); opened {len(opened)} removal(s).")
        st.rerun()

    st.divider()
    st.subheader("Scan history")
    scans = broker_ledger.get_scans(db_path, profile_id=profile["id"])
    if not scans:
        page_shell.empty_state("No scans recorded for this profile yet.")
    else:
        st.dataframe(
            [
                {
                    "Broker": s.get("broker_name") or s["broker_id"],
                    "Type": s["scan_type"],
                    "Status": s["status"],
                    "Found": "yes" if s["found"] else "no",
                    "When": str(s.get("completed_at") or s.get("created_at"))[:19],
                    "Note": (s.get("error_message") or "")[:80],
                }
                for s in scans
            ],
            width="stretch", hide_index=True,
        )
