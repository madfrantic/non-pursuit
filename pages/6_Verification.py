"""Verification — re-scan to prove a removal actually took.

chino's VerificationTab. chino's engine was a stub that called a database
method which did not exist; utils/agent_engines.VerificationEngine re-runs
the real broker probe.
"""
from components import page_shell

db_path = page_shell.setup("Verification", "✅")

import streamlit as st

import broker_ledger
import runtime_mode
from agent_engines import VerificationEngine
from broker_agent_models import RemovalStatus

_, review_db = page_shell.ledger_paths()

st.title("✅ Verification")
st.caption("A broker saying it removed you and the record actually being gone "
           "are different claims. This checks the second one.")

profile = page_shell.profile_picker(db_path, key="verify_profile")

if profile:
    submitted = broker_ledger.get_removals(
        db_path, profile_id=profile["id"], status=RemovalStatus.SUBMITTED.value)

    st.metric("Awaiting verification", len(submitted))

    if not runtime_mode.live_scanning_enabled():
        st.warning("Live scanning is disabled in this runtime; verification needs it.")
    elif submitted and st.button("Verify all submitted", type="primary"):
        with st.spinner("Re-scanning brokers..."):
            results = VerificationEngine(db_path, review_db)\
                .verify_all_submitted(profile["id"])
        confirmed = sum(1 for r in results if r["verified"] is True)
        still = sum(1 for r in results if r["verified"] is False)
        unknown = sum(1 for r in results if r["verified"] is None)
        st.success(f"{confirmed} confirmed gone · {still} still listed · "
                   f"{unknown} couldn't be checked")
        st.rerun()

    st.info(
        "**\"Couldn't be checked\" is not \"still listed.\"** When a broker blocks "
        "the re-scan, that is recorded as unknown rather than as evidence of "
        "non-compliance — which is what it would become if we guessed.",
        icon="ℹ️",
    )

    st.divider()
    st.subheader("Confirmed removals")
    confirmed_rows = broker_ledger.get_removals(
        db_path, profile_id=profile["id"], status=RemovalStatus.CONFIRMED.value)
    if confirmed_rows:
        st.dataframe(
            [{"Broker": r.get("broker_name"), "Confirmed": str(r.get("confirmed_at"))[:19]}
             for r in confirmed_rows],
            width="stretch", hide_index=True)
    else:
        page_shell.empty_state("Nothing confirmed removed yet.")
