"""Settings — broker set, the background engine, and vault state.

chino's SettingsTab plus its MainWindow._load_initial_brokers seeding.
"""
from components import page_shell

db_path = page_shell.setup("Settings", "⚙️")

import streamlit as st

import broker_ledger
import config
import database
import optout_engine
import runtime_mode
from broker_agent_models import Broker, coerce_difficulty

st.title("⚙️ Settings")

st.subheader("Broker set")
brokers = broker_ledger.get_brokers(db_path)
st.caption(f"{len(brokers)} broker(s) in the ledger.")

if st.button("Seed / refresh from data/brokers.csv", type="primary"):
    import broker_probe

    contacts = broker_probe.load_broker_contacts(config.BROKERS_CSV_PATH)
    count = 0
    for name, row in contacts.items():
        broker_ledger.add_broker(db_path, Broker(
            name=name,
            url=row.get("search_url", ""),
            opt_out_url=row.get("optout_url", ""),
            search_url=row.get("search_url", ""),
            difficulty=optout_engine.difficulty_for(name),
            compliance_email=row.get("compliance_email", ""),
            notes=row.get("notes", ""),
        ))
        count += 1
    st.success(f"Seeded {count} broker(s) from the verified CSV registry.")
    st.rerun()

st.caption("Sourced from data/brokers.csv — the registry with human-verified "
           "compliance emails — rather than chino's hardcoded list, which had "
           "no contact addresses.")

if brokers:
    edited = st.dataframe(
        [{"Broker": b["name"], "Difficulty": b["difficulty"],
          "Opt-out": b.get("opt_out_url") or "—",
          "Compliance email": b.get("compliance_email") or "—"} for b in brokers],
        width="stretch", hide_index=True)

    with st.expander("Change a broker's difficulty"):
        labels = {b["id"]: b["name"] for b in brokers}
        bid = st.selectbox("Broker", list(labels), format_func=lambda i: labels[i])
        from broker_agent_models import BrokerDifficulty
        new_difficulty = st.selectbox(
            "Difficulty", [d.value for d in BrokerDifficulty],
            index=[d.value for d in BrokerDifficulty].index(
                coerce_difficulty(next(b["difficulty"] for b in brokers if b["id"] == bid)).value))
        if st.button("Update"):
            broker_ledger.update_broker_difficulty(db_path, bid, new_difficulty)
            st.rerun()
        st.caption("`captcha` and `manual_only` stop automation from opening the "
                   "page at all and send the task straight to the review queue.")

st.divider()
st.subheader("Background engine")
st.markdown(
    f"- Monitoring scan every **{config.AGENT_SCAN_INTERVAL_DAYS} days**\n"
    f"- Verification every **{config.AGENT_VERIFY_INTERVAL_DAYS} days**\n"
    f"- Enabled in config: **{config.AGENT_SCHEDULER_ENABLED}**"
)
tasks = broker_ledger.get_scheduled_tasks(db_path)
if tasks:
    st.dataframe(
        [{"Task": t["task_type"], "Profile": t.get("profile_id"),
          "Every (days)": t.get("interval_days"),
          "Last run": str(t.get("last_run") or "—")[:19],
          "Next run": str(t.get("next_run") or "—")[:19]} for t in tasks],
        width="stretch", hide_index=True)
else:
    st.caption("No scheduled tasks recorded yet — they register when the app "
               "starts with at least one profile.")

st.info(
    "The background engine **scans and verifies only**. It never submits an "
    "opt-out: a demand made in your name is reviewed by you first, which is "
    "why unattended submission is not something this app does.",
    icon="🛡️",
)

st.divider()
st.subheader("Vault")
unlocked = database.vault_is_unlocked()
st.markdown(f"- Vault unlocked: **{'yes' if unlocked else 'no'}**")
st.markdown(f"- Master password set: **{'yes' if database.vault_exists(db_path) else 'no'}**")
st.caption(
    f"Key derived with PBKDF2-HMAC-SHA256 at {database.KDF_ITERATIONS:,} iterations "
    "over a random per-install salt. The password itself is never stored, and "
    "records written before the vault existed remain readable."
)

st.divider()
st.subheader("Runtime")
badge, detail = runtime_mode.mode_badge()
st.markdown(f"**{badge}** — {detail}")
