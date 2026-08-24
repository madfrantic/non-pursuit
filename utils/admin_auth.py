"""
The owner-only gate on the admin dashboard.

The admin dashboard shows every record this install holds -- profiles,
demands, discovered accounts, exposure checks, the agent ledger -- in one
place with no filtering. That is exactly the view the rest of the app is
careful never to build, so it needs a lock of its own rather than riding
on the vault gate: the vault password protects the *encryption key*, and
in demo mode there is no vault gate at all.

HOW IT AUTHENTICATES

A secret in the environment (NON_PURSUIT_ADMIN_KEY, or the same name in
st.secrets so a Streamlit Community Cloud deployment can set it), compared
against what was typed with hmac.compare_digest over SHA-256 digests.
Digests rather than the raw strings so the comparison is over fixed-length
input, and compare_digest rather than == so it does not leak the length of
the matching prefix through timing.

WHY THE PAGE DISAPPEARS WHEN NO KEY IS SET

An unconfigured install has no secret to check against, so there is no
answer that should open the dashboard. It could render a prompt that can
never be satisfied, but that advertises the page to everyone who can see
the sidebar. Instead `is_configured()` is false, nav drops the link, and
`require_admin()` refuses -- the feature is simply not present until its
owner turns it on.

WHY THE KEY NEVER REACHES SESSION STATE

Same reason as the vault password (components/vault_gate.py): session
state is server memory that survives reruns and is one debug write away
from a log. What persists after a successful unlock is a boolean.
"""
import hashlib
import hmac
import os

ADMIN_KEY_ENV = "NON_PURSUIT_ADMIN_KEY"
AUTHENTICATED_KEY = "_admin_authenticated"

# Below this, a key is short enough to be worth guessing against a page
# that will hand back every record in the database.
MIN_KEY_LENGTH = 12


def _secret(name: str) -> str | None:
    """st.secrets lookup that tolerates there being no secrets file.

    Mirrors runtime_mode._secret -- reading st.secrets with no
    secrets.toml present raises rather than returning empty.
    """
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except Exception:
        return None
    return None if value is None else str(value)


def configured_key() -> str | None:
    """The configured admin secret, or None when there isn't one.

    Environment first so a container or a test can set it without a
    secrets file; st.secrets second for Community Cloud, which has no
    environment-variable UI.
    """
    raw = os.getenv(ADMIN_KEY_ENV)
    if raw is None:
        raw = _secret(ADMIN_KEY_ENV)
    raw = (raw or "").strip()
    return raw or None


def is_configured() -> bool:
    """True when this install has an admin secret set."""
    return configured_key() is not None


def key_is_weak() -> bool:
    """True when a key is set but too short to be worth trusting.

    Reported in the UI rather than enforced: refusing to accept a
    too-short key would lock the owner out of their own install over a
    policy they can fix in one line of .env.
    """
    key = configured_key()
    return key is not None and len(key) < MIN_KEY_LENGTH


def verify(candidate: str) -> bool:
    """Constant-time check of a typed key against the configured one."""
    key = configured_key()
    if not key or not candidate:
        return False
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode("utf-8")).digest(),
        hashlib.sha256(key.encode("utf-8")).digest(),
    )


def is_authenticated(session_state) -> bool:
    """True when this session has already unlocked the dashboard.

    Re-checks is_configured() rather than trusting the flag alone: if the
    key is removed from the environment and the server restarts, a session
    that had unlocked must not keep its access.
    """
    return bool(session_state.get(AUTHENTICATED_KEY)) and is_configured()


def sign_out(session_state) -> None:
    session_state.pop(AUTHENTICATED_KEY, None)
