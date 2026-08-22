"""Evidence — the capture chain behind every claimed removal.

New in the merge. chino had the `evidence` table and two `# TODO` stubs
where capture and hashing should have been; utils/optout_engine.py now
implements both, and this is where the chain is inspected.

The integrity column is the reason this page exists: a screenshot whose
file no longer hashes to the digest recorded at capture time is not
evidence of anything, and that has to be visible rather than assumed.
"""
from components import page_shell

db_path = page_shell.setup("Evidence", "🧾")

import streamlit as st

import broker_ledger
import optout_engine

st.title("🧾 Evidence chain")
st.caption("Each capture is hashed at the moment it is taken. If the file on disk "
           "no longer matches that hash, it is flagged here.")

records = broker_ledger.get_evidence(db_path)

if not records:
    page_shell.empty_state(
        "No evidence captured yet.",
        "Captures are recorded when a removal is verified, or when an opt-out "
        "run screenshots a broker page.")
else:
    verified = tampered = missing = 0
    rows = []
    for record in records:
        file_path = record.get("file_path") or ""
        file_hash = record.get("file_hash") or ""
        if not file_path or not file_hash:
            integrity, note = "—", "no file captured"
            missing += 1
        elif optout_engine.verify_evidence(file_path, file_hash):
            integrity, note = "✅ intact", ""
            verified += 1
        else:
            integrity, note = "⚠️ mismatch", "file changed or missing since capture"
            tampered += 1
        rows.append({
            "Broker": record.get("broker_name") or "—",
            "Type": record["evidence_type"],
            "Captured": str(record.get("captured_at") or "")[:19],
            "SHA256": (file_hash[:16] + "…") if file_hash else "—",
            "Integrity": integrity,
            "Note": note or (record.get("notes") or "")[:60],
        })

    cols = st.columns(3)
    cols[0].metric("Intact", verified)
    cols[1].metric("Mismatched", tampered)
    cols[2].metric("No file", missing)

    if tampered:
        st.warning(f"{tampered} capture(s) no longer match the hash recorded at "
                   "capture time. Treat those as unverifiable.")

    st.dataframe(rows, width="stretch", hide_index=True)

st.divider()
with st.expander("How the chain works"):
    st.markdown(
        "- A capture writes a PNG under `data/evidence/` and computes its SHA256 "
        "in the same call, so the two can't drift apart.\n"
        "- The digest goes into the ledger's `evidence` table; the PNG stays on disk.\n"
        "- Re-hashing the file and comparing is what this page does on every load.\n"
        "- `data/evidence/` is gitignored: a broker's results page is a page with "
        "your PII rendered on it."
    )
