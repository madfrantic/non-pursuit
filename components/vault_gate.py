"""
The master-password gate.

The vault (utils/database.py VaultManager, ported from chino) derives the
Fernet key from a password nobody stores, so the key only exists once
somebody types it. This is where that happens: the app renders this and
nothing else until the ledger is unlocked.

WHY DEMO MODE IS EXEMPT

runtime_mode.is_demo_mode() routes every store to a throwaway per-session
file seeded with synthetic data (demo_data.DEMO_PROFILE -- "John Doe"), and
render.yaml pins demo mode on for the hosted build. There is no real PII
behind the gate there, and a shared demo host that demands a master password
from every visitor would be asking them to invent a secret to protect
nothing. So demo mode skips the gate; local mode does not.

WHY THE PASSWORD IS NEVER PUT IN SESSION STATE

st.session_state is per-session server memory that survives reruns, and
anything in it is one debug write away from a log. The password is used to
derive the key and then dropped; what persists is the derived cipher inside
the database module, and a boolean here saying this session got past the
gate. There is no code path that can read the password back out.
"""
import streamlit as st

import config
import database
import runtime_mode

UNLOCKED_KEY = "_vault_unlocked"


def is_required() -> bool:
    """True when this run must be gated."""
    return not runtime_mode.is_demo_mode()


def require_unlock() -> bool:
    """Render the gate if needed. True when the app may proceed."""
    if not is_required():
        return True
    if st.session_state.get(UNLOCKED_KEY) and database.vault_is_unlocked():
        return True
    _render_gate()
    return False


def _render_gate() -> None:
    db_path = runtime_mode.db_path()
    first_run = not database.vault_exists(db_path)

    st.title("🔒 Non-Pursuit")
    if first_run:
        st.subheader("Set a master password")
        st.info(
            "This password derives the key that encrypts identity data at rest. "
            "It is not stored anywhere and cannot be recovered — if you lose it, "
            "the records encrypted under it stay encrypted."
        )
    else:
        st.subheader("Unlock the ledger")
        st.caption("Enter the master password for this install.")

    with st.form("vault_unlock"):
        password = st.text_input("Master password", type="password")
        confirm = (st.text_input("Confirm password", type="password")
                   if first_run else None)
        submitted = st.form_submit_button("Unlock", type="primary")

    if not submitted:
        _render_footer()
        return

    if not password:
        st.error("Enter a password.")
    elif first_run and password != confirm:
        st.error("The two passwords do not match.")
    elif first_run and len(password) < 8:
        st.error("Use at least 8 characters.")
    else:
        try:
            database.unlock_vault(password, db_path)
            st.session_state[UNLOCKED_KEY] = True
            st.rerun()
        except ValueError as exc:
            # Wrong password. Refusing here is the point: unlocking with the
            # wrong key would write rows the right key can never read back.
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not unlock the vault: {exc}")

    _render_footer()


def _render_footer() -> None:
    st.caption(
        "Everything stays on this machine. Records encrypted before this "
        "vault existed remain readable — see utils/database.py."
    )


def render_lock_control() -> None:
    """A sidebar control to drop the key without ending the session."""
    if not is_required() or not database.vault_is_unlocked():
        return
    if st.sidebar.button("🔒 Lock vault", width="stretch",
                         help="Forget the decryption key until the password is re-entered."):
        database.lock_vault()
        st.session_state[UNLOCKED_KEY] = False
        st.rerun()
