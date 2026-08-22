"""Reports — a campaign summary for a profile.

chino's ReportsTab. chino hand-rolled HTML and plain-text report builders;
this app already had utils/data_export.py, utils/pdf_generator.py and
utils/audit_packager.py, so the report here is assembled from ledger state
and offered through the existing export paths rather than a fourth
generator.
"""
from components import page_shell

db_path = page_shell.setup("Reports", "📊")

import json
from datetime import datetime, timezone

import streamlit as st

import broker_ledger

st.title("📊 Reports")
st.caption("What the agent has done for a profile, and what it found.")

profile = page_shell.profile_picker(db_path, key="reports_profile")

if profile:
    stats = broker_ledger.get_dashboard_stats(db_path)
    scans = broker_ledger.get_scans(db_path, profile_id=profile["id"])
    removals = broker_ledger.get_removals(db_path, profile_id=profile["id"])
    evidence = broker_ledger.get_evidence(db_path)

    cols = st.columns(4)
    cols[0].metric("Brokers scanned", len({s["broker_id"] for s in scans}))
    cols[1].metric("Records found", sum(1 for s in scans if s["found"]))
    cols[2].metric("Removals confirmed", stats["removals_confirmed"])
    cols[3].metric("Evidence captured", stats["evidence_captured"])

    st.divider()
    st.subheader("Where records were found")
    found = [s for s in scans if s["found"]]
    if found:
        st.dataframe(
            [{"Broker": s.get("broker_name"), "Found on": str(s.get("completed_at"))[:19],
              "Record": s.get("result_url") or "—"} for s in found],
            width="stretch", hide_index=True)
    else:
        page_shell.empty_state("No records found for this profile yet.")

    st.divider()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": {"id": profile["id"], "name": profile["name"]},
        "summary": stats,
        "scans": [
            {"broker": s.get("broker_name"), "type": s["scan_type"],
             "status": s["status"], "found": bool(s["found"]),
             "completed_at": s.get("completed_at")}
            for s in scans
        ],
        "removals": [
            {"broker": r.get("broker_name"), "status": r["status"],
             "submitted_at": r.get("submitted_at"),
             "confirmed_at": r.get("confirmed_at")}
            for r in removals
        ],
        "evidence": [
            {"broker": e.get("broker_name"), "type": e["evidence_type"],
             "sha256": e.get("file_hash"), "captured_at": e.get("captured_at")}
            for e in evidence
        ],
    }

    st.download_button(
        "⬇️ Download report (JSON)",
        data=json.dumps(report, indent=2, default=str),
        file_name=f"agent_report_{profile['name'].replace(' ', '_')}_"
                  f"{datetime.now().strftime('%Y%m%d')}.json",
        mime="application/json",
        type="primary",
    )
    st.caption("The full statutory audit trail (letters, deadlines, verification "
               "logs) is still the sidebar's Audit Trail ZIP on the main app.")

    with st.expander("Activity log"):
        entries = broker_ledger.get_activity_log(db_path, limit=200)
        if entries:
            st.dataframe(
                [{"When": str(e["created_at"])[:19], "Action": e["action"],
                  "Entity": f"{e.get('entity_type') or ''} {e.get('entity_id') or ''}".strip(),
                  "Details": (e.get("details") or "")[:60]} for e in entries],
                width="stretch", hide_index=True)
            st.caption("Actions and ids only — never field values.")
        else:
            st.caption("Nothing logged yet.")
