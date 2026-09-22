"""Vault — profiles and the PII they are searched on.

chino's VaultTab. Values are encrypted through database._encrypt before
they reach SQLite; nothing on this page writes a plaintext value to disk
or to the log.
"""
from components import page_shell

db_path = page_shell.setup("Vault", "🔐")

import streamlit as st

import broker_ledger
from broker_agent_models import PIIField

st.title("🔐 Vault")
st.caption("Identity profiles the agent searches brokers on behalf of. "
           "Encrypted at rest under your master password.")

FIELD_TYPES = [
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("middle_name", "Middle name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("city", "City"),
    ("state", "State"),
    ("zip_code", "ZIP"),
    ("age", "Age"),
]

profiles = broker_ledger.get_profiles(db_path)

left, right = st.columns([1, 2])

with left:
    st.subheader("Profiles")
    if profiles:
        for profile in profiles:
            st.markdown(f"**{profile['name']}**")
            st.caption(f"id {profile['id']} · updated {str(profile['updated_at'])[:19]}")
    else:
        st.caption("None yet.")

    with st.form("new_profile", clear_on_submit=True):
        new_name = st.text_input("New profile name")
        if st.form_submit_button("Create", type="primary"):
            if new_name.strip():
                broker_ledger.create_profile(db_path, new_name.strip())
                st.rerun()
            else:
                st.error("A profile needs a name.")

with right:
    st.subheader("Identity fields")
    if not profiles:
        page_shell.empty_state("Create a profile to add identity fields.")
    else:
        labels = {p["id"]: p["name"] for p in profiles}
        pid = st.selectbox("Profile", list(labels), format_func=lambda i: labels[i],
                           key="vault_profile")
        current = {f.field_type: f.value for f in broker_ledger.get_pii_fields(db_path, pid)}

        with st.form("pii_fields"):
            entered = {}
            cols = st.columns(2)
            for index, (field_type, label) in enumerate(FIELD_TYPES):
                with cols[index % 2]:
                    entered[field_type] = st.text_input(
                        label, value=current.get(field_type, ""), key=f"pii_{field_type}")
            if st.form_submit_button("Save", type="primary"):
                saved = 0
                for field_type, value in entered.items():
                    if value.strip() or field_type in current:
                        broker_ledger.save_pii_field(db_path, pid, field_type, value.strip())
                        saved += 1
                st.success(f"Saved {saved} field(s).")
                st.rerun()

        st.divider()
        with st.expander("Delete this profile"):
            st.warning("Deletes the profile and every encrypted field on it. "
                       "Scans and removals already recorded are kept.")
            if st.button("Delete permanently", type="secondary"):
                broker_ledger.delete_profile(db_path, pid)
                st.rerun()
