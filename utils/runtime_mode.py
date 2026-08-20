"""
Which build is this -- the local production utility, or the hosted demo?

Non-Pursuit ships in two shapes from one codebase. Locally it's the real
tool: everything persists to data/tracker.db, browser automation drives a
real Chrome, and scans hit real platforms. Hosted (a class demo, a
container on someone else's server) none of that is safe -- a shared
server has no business holding one visitor's PII where the next visitor
can reach it, headed Chrome can't launch in a container at all, and a
live scan would send the audience's handles out from one shared IP that
gets rate-limited for the trouble.

The switch is the NON_PURSUIT_DEMO_MODE environment variable, read once
per call so a test can flip it without reimporting anything.

Why per-session temp files rather than the ":memory:" the obvious
implementation reaches for: every store module opens a fresh
sqlite3.connect() per operation, and each connection to ":memory:" gets
its own private empty database. Writes appear to succeed and then vanish
-- verified, not assumed. A process-wide shared in-memory database would
fix the vanishing but leak records between visitors, which is worse than
the bug. A uniquely-named temp file per Streamlit session gets real
isolation, survives the sequential connections the stores actually make,
and needs no changes to the stores themselves -- they already take
db_path as an argument.
"""
import os
import tempfile
import uuid
from pathlib import Path

import config

DEMO_ENV_VAR = "NON_PURSUIT_DEMO_MODE"
DEPLOYMENT_ENV_VAR = "DEPLOYMENT_ENV"
_TRUTHY = {"1", "true", "yes", "on"}

SESSION_DB_PREFIX = "np_session_"


def is_demo_mode() -> bool:
    """True when this process is serving the hosted demo build."""
    return os.getenv(DEMO_ENV_VAR, "false").strip().lower() in _TRUTHY


def is_cloud_deployment() -> bool:
    """True when running in cloud/web environment (Streamlit Cloud, Docker, etc.)."""
    deployment = os.getenv(DEPLOYMENT_ENV_VAR, "local").strip().lower()
    # Detect cloud environment via environment variables or Streamlit indicators
    is_streamlit_cloud = "STREAMLIT_SHARING_MODE" in os.environ
    is_cloud_var = deployment in {"cloud", "web", "production"}
    return is_streamlit_cloud or is_cloud_var


def is_local_mode() -> bool:
    """True when running locally on the user's machine."""
    return not is_cloud_deployment() and not is_demo_mode()


def _session_state():
    """Streamlit's session_state, or None when running outside a script
    run (pytest, a headless export job). Importing streamlit is cheap and
    already a hard dependency; what isn't safe is assuming a runtime
    exists, so callers get None and fall back."""
    try:
        import streamlit as st

        _ = st.session_state  # raises if there's no script run context
        return st.session_state
    except Exception:
        return None


def new_session_db_path() -> str:
    """A fresh, uniquely-named temp database path. Public so tests can
    exercise the demo path without a Streamlit runtime."""
    name = f"{SESSION_DB_PREFIX}{uuid.uuid4().hex[:8]}.db"
    return str(Path(tempfile.gettempdir()) / name)


def db_path() -> str:
    """The SQLite file every store should read and write for this
    request.

    Local mode returns the single durable database. All four of config's
    *_DB_PATH names point at that same file by design (tracker, exposure,
    profile and discovered accounts are tables, not separate databases),
    so one accessor covers every call site.

    Demo mode returns a per-visitor temp file, created once per Streamlit
    session and remembered in session_state. Without a session (tests,
    scripts) each call would otherwise hand back a different empty
    database, so the path is cached on the module instead -- correct for
    a single-threaded script, and never reached by the multi-visitor
    server path.
    """
    if not is_demo_mode():
        return config.TRACKER_DB_PATH

    state = _session_state()
    if state is not None:
        if "session_db_path" not in state:
            state["session_db_path"] = new_session_db_path()
        return state["session_db_path"]

    global _fallback_db_path
    if _fallback_db_path is None:
        _fallback_db_path = new_session_db_path()
    return _fallback_db_path


_fallback_db_path = None


def reset_fallback_db_path() -> None:
    """Drop the no-runtime cache. Only tests need this."""
    global _fallback_db_path
    _fallback_db_path = None


# --- feature gates ----------------------------------------------------
# Each of these is a thing that is genuinely unsafe or broken on a shared
# host, rather than merely different. Kept here so the reason lives in one
# place and the components just ask.

def browser_automation_enabled() -> bool:
    """Spokeo's auto-search launches a headed Chrome via Playwright. In a
    container there's no display to launch it into, so demo mode shows the
    sequence as a narrated preview instead of failing mid-presentation."""
    return not is_demo_mode()


def live_scanning_enabled() -> bool:
    """A real footprint scan sends ~50 outbound requests. From a shared
    demo host that's one IP scanning on behalf of strangers -- rate-limited
    quickly, and it puts an audience member's handle on the wire. Demo mode
    serves canned matches instead."""
    return not is_demo_mode()


def persistent_storage_enabled() -> bool:
    """Whether writes survive past this session. Drives the UI copy that
    tells a demo visitor their data disappears when they close the tab."""
    return not is_demo_mode()


def facial_recognition_enabled() -> bool:
    """Biometric face matching is off on the hosted build regardless of
    whether the DeepFace dependency happens to be installed there. A face
    photo is a more sensitive category of PII than anything else this app
    touches, and a shared demo host processing one visitor's uploaded face
    -- even transiently, even without persisting it -- is a risk this app
    takes nowhere else. Local mode is a single person's own machine
    running against their own photo; that consent boundary doesn't exist
    on a hosted container."""
    return not is_demo_mode()


def mode_badge() -> tuple[str, str]:
    """(label, help text) for the sidebar indicator."""
    if is_demo_mode():
        return (
            "🟡 Demo sandbox mode",
            "Records live only in your browser session and are discarded when you "
            "close the tab. Browser automation and live scanning are disabled on "
            "the hosted build.",
        )
    elif is_cloud_deployment():
        return (
            "☁️ Cloud/Web mode (restricted)",
            "Running on cloud infrastructure. Heavy OSINT scans are disabled to prevent "
            "IP bans and timeouts. Use Presentation Mode for safe demos.",
        )
    return (
        "🟢 Local active mode",
        "Full local build. Everything is stored on this machine in data/tracker.db "
        "and nothing is uploaded anywhere.",
    )
