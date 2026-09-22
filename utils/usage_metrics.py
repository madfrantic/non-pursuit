"""
Counts how often each feature gets used. Nothing else.

The point of this module is what it structurally cannot do. Off-the-shelf
Streamlit trackers work by monkeypatching st.text_input and friends and
storing the value the user typed as a dict key -- which in this app would
mean retaining home addresses, landline numbers, an ex-partner's name, and
docket number + Penal Law section + offense description. For a tool whose
entire premise is getting personal data deleted, holding onto that would be
a contradiction, and holding it in a process-global dict shared across every
visitor to a hosted demo would be a disclosure.

So this records events, not inputs. record_event() takes a name from
EVENTS and raises on anything else, which means there is no code path --
including a future careless one -- that can route a user-supplied string
into this table. The schema has no free-text column to put one in. That
constraint is the feature; keep it when extending this.

Metrics live in their own SQLite file rather than the per-session tracker
database, because a count is only interesting in aggregate: "41 letters
generated" is the number worth showing, and a per-session store would reset
it to 1 for every visitor. Sharing this file across sessions is safe
precisely because it holds event names and integers and never held anything
else.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# The complete vocabulary. record_event() rejects anything absent from this
# tuple, so adding telemetry is a deliberate edit here rather than something
# that can happen accidentally at a call site.
SESSION_STARTED = "session_started"
PROFILE_SAVED = "profile_saved"
FOOTPRINT_SCAN_RUN = "footprint_scan_run"
EMAIL_SCAN_RUN = "email_scan_run"
LETTER_GENERATED = "letter_generated"
REQUEST_LOGGED = "request_logged"
AUDIT_PACKAGE_BUILT = "audit_package_built"
EXPORT_DOWNLOADED = "export_downloaded"
SEALING_SCREENED = "sealing_screened"
DEMO_SEEDED = "demo_seeded"

EVENTS = (
    SESSION_STARTED,
    PROFILE_SAVED,
    FOOTPRINT_SCAN_RUN,
    EMAIL_SCAN_RUN,
    LETTER_GENERATED,
    REQUEST_LOGGED,
    AUDIT_PACKAGE_BUILT,
    EXPORT_DOWNLOADED,
    SEALING_SCREENED,
    DEMO_SEEDED,
)

# Human-facing labels for the metrics panel, kept next to the names they
# describe so a new event can't be added without one.
LABELS = {
    SESSION_STARTED: "Sessions",
    PROFILE_SAVED: "Profiles saved",
    FOOTPRINT_SCAN_RUN: "Footprint scans",
    EMAIL_SCAN_RUN: "Email scans",
    LETTER_GENERATED: "Letters generated",
    REQUEST_LOGGED: "Requests logged",
    AUDIT_PACKAGE_BUILT: "Audit packages built",
    EXPORT_DOWNLOADED: "Exports downloaded",
    SEALING_SCREENED: "Sealing screenings",
    DEMO_SEEDED: "Demo seeds",
}


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            event TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def record_event(db_path: str, event: str, count: int = 1) -> None:
    """Increment one known event. Raises ValueError on an unknown name --
    loudly, because a silently-ignored metric reads as "nobody used this"
    rather than "this was never wired up"."""
    if event not in EVENTS:
        raise ValueError(
            f"Unknown event {event!r}. Add it to usage_metrics.EVENTS and LABELS first."
        )
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO usage_events (event, count, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(event) DO UPDATE SET "
            "count = count + excluded.count, last_seen = excluded.last_seen",
            (event, count, today, today),
        )
        conn.commit()


def get_counts(db_path: str) -> dict:
    """event name -> count, for every event that has fired at least once."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT event, count FROM usage_events").fetchall()
    return {row["event"]: row["count"] for row in rows}


def summary(db_path: str) -> list:
    """Every known event as (label, count), including never-fired ones at
    zero, ordered as declared in EVENTS. A metrics panel that hides the
    zeroes overstates coverage."""
    counts = get_counts(db_path)
    return [(LABELS[event], counts.get(event, 0)) for event in EVENTS]


def reset(db_path: str) -> None:
    """Clear all counters -- for wiping figures accumulated while testing
    before a real run."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM usage_events")
        conn.commit()
