"""
Shared setup for the pages/ multi-page app.

Streamlit runs each page in pages/ as its own top-level script: the module
globals app.py set up are not there, sys.path does not have utils/ on it,
and the vault gate has not run. Every page therefore starts with
`page_shell.setup(...)`, which is the one place that work is written.

Skipping it on a page would produce a page that renders identity data
without ever asking for the password -- the gate is only as good as its
least careful call site, so it lives in the same call as the page config.
"""
import os
import sys

import streamlit as st

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTILS_DIR = os.path.join(ROOT_DIR, "utils")
for _path in (ROOT_DIR, UTILS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import config  # noqa: E402
import debug_view  # noqa: E402
import runtime_mode  # noqa: E402
from components import nav  # noqa: E402
from components import vault_gate  # noqa: E402


def setup(title: str, icon: str = "🛡️") -> str:
    """Configure the page, enforce the vault gate, and return the db path.

    Calls st.stop() when the vault is locked, so anything after this line
    in a page body only runs for an unlocked session.
    """
    st.set_page_config(
        page_title=f"{title} — {config.APP_TITLE}",
        page_icon=icon,
        layout=config.APP_LAYOUT,
        initial_sidebar_state="auto",
    )
    debug_view.inject()
    if not vault_gate.require_unlock():
        st.stop()
    # After the gate, matching app.py: a locked session gets the password
    # form and nothing else. include_home is what keeps a page from being a
    # dead end -- app.py's sidebar isn't running here, so without this link
    # there is no route back to the main app and a refresh just reloads the
    # page you are stuck on.
    nav.render_page_links(include_home=True)
    vault_gate.render_lock_control()
    return runtime_mode.db_path()


def ledger_paths() -> tuple:
    """(ledger_db_path, review_queue_db_path) for this run."""
    return runtime_mode.db_path(), config.REVIEW_QUEUE_DB_PATH


def profile_picker(db_path: str, key: str = "agent_profile"):
    """A selectbox over vault profiles. Returns the chosen row, or None.

    Every agent page needs one and they must agree on the selection, so it
    is written once here rather than four times slightly differently.
    """
    import broker_ledger

    profiles = broker_ledger.get_profiles(db_path)
    if not profiles:
        st.info("No profiles yet. Create one on the **Vault** page first.")
        return None
    labels = {p["id"]: p["name"] for p in profiles}
    chosen = st.selectbox(
        "Profile", options=list(labels), format_func=lambda i: labels[i], key=key)
    return next((p for p in profiles if p["id"] == chosen), None)


def empty_state(message: str, hint: str = "") -> None:
    st.info(message)
    if hint:
        st.caption(hint)
