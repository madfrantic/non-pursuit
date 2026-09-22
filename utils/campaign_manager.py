"""
Delisting campaign ledger -- the lifecycle state machine behind the
compliance pipeline.

`tracker.py` answers "what did I send, and when is it due?". This module
answers the longer question: for a given broker record, has it actually
come down yet? A campaign starts at DISCOVERED (an OSINT hit), moves to
DISPATCHED the day a statutory demand goes out, sits under a running
45-day clock, and ends either DELISTED (verified gone) or NON_COMPLIANT
(window blown, escalation warranted).

Lives as a `broker_campaigns` table inside the same local SQLite file as
the tracker, exposure checks and profile -- so it inherits runtime_mode's
per-session isolation in demo mode for free, rather than pinning a second
database file that a hosted visitor would share with the next visitor.

NON_COMPLIANT is *derived on read*, never written by a background job.
A campaign whose deadline passed is overdue whether or not the recon
worker happened to run that day, so the timeline stays correct on a
machine that has been closed for a week. Only an explicit escalation
persists the status. This mirrors how tracker.get_all_requests() computes
`is_overdue` rather than storing it.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import config

DATE_FORMAT = "%Y-%m-%d"

STATUS_DISCOVERED = "DISCOVERED"
STATUS_DISPATCHED = "DISPATCHED"
STATUS_CLOCK_ACTIVE = "CLOCK_ACTIVE"
STATUS_DELISTED = "DELISTED"
STATUS_NON_COMPLIANT = "NON_COMPLIANT"

STATUS_OPTIONS = [
    STATUS_DISCOVERED,
    STATUS_DISPATCHED,
    STATUS_CLOCK_ACTIVE,
    STATUS_DELISTED,
    STATUS_NON_COMPLIANT,
]

# Statuses where a statutory clock is running, so the deadline is what
# decides whether the row is still pending or already blown.
_CLOCK_RUNNING = {STATUS_DISPATCHED, STATUS_CLOCK_ACTIVE}

# A campaign is "closed" once the record is verified gone. NON_COMPLIANT is
# deliberately not closed -- that is the state that still needs work done.
_CLOSED = {STATUS_DELISTED}

DEFAULT_STATUTE = "CCPA § 1798.105"


def _today() -> "datetime.date":
    return datetime.now().date()


def _parse(value: str | None):
    """A stored YYYY-MM-DD string as a date, or None if absent/malformed.

    Malformed is treated as absent rather than raising: a hand-edited row
    should degrade to "no clock known" in the timeline, not take down the
    whole page render.
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), DATE_FORMAT).date()
    except ValueError:
        return None


@contextmanager
def _connect(db_path: str):
    """One place that opens, migrates, and always closes the connection.

    Same shape as tracker._connect on purpose -- both tables live in the
    same file, and a second connection idiom here would be one more thing
    to keep in sync for no benefit.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_name TEXT NOT NULL,
            domain TEXT NOT NULL,
            profile_url TEXT,
            status TEXT NOT NULL DEFAULT 'DISCOVERED',
            date_discovered TEXT NOT NULL,
            date_dispatched TEXT,
            statutory_deadline TEXT,
            date_verified_removed TEXT,
            last_recon_check TEXT,
            statute_invoked TEXT DEFAULT 'CCPA § 1798.105',
            evidence_log TEXT,
            UNIQUE(broker_name, domain)
        )
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """Create the broker_campaigns table if it doesn't already exist."""
    with _connect(db_path):
        pass


# --- deadline math ----------------------------------------------------
# Pure functions, no database. The 45-day window is read from config
# rather than written literally, so the statutory number stays single-
# sourced with the tracker and the letter templates.


def calculate_deadline(dispatch_date: str, window_days: int | None = None) -> str | None:
    """The statutory deadline for a demand dispatched on `dispatch_date`.

    Day 0 is the dispatch date itself, so a 45-day window dispatched on
    the 1st is due on the 46th of that count -- the broker gets all 45
    days, not 44.
    """
    dispatched = _parse(dispatch_date)
    if dispatched is None:
        return None
    if window_days is None:
        window_days = config.CCPA_RESPONSE_WINDOW_DAYS
    return (dispatched + timedelta(days=window_days)).strftime(DATE_FORMAT)


def calculate_days_remaining(statutory_deadline: str | None, today=None) -> int | None:
    """Days left before the window closes; negative once it has closed.

    None when there is no deadline yet (nothing dispatched). Zero means
    the deadline is today and has *not* yet been missed -- consistent
    with tracker.py, which only calls a request overdue below zero.
    """
    deadline = _parse(statutory_deadline)
    if deadline is None:
        return None
    reference = today or _today()
    return (deadline - reference).days


def is_overdue(status: str, statutory_deadline: str | None, today=None) -> bool:
    """True when a running clock has passed its deadline."""
    if status not in _CLOCK_RUNNING:
        return False
    days = calculate_days_remaining(statutory_deadline, today=today)
    return days is not None and days < 0


def derive_status(record: dict, today=None) -> str:
    """The status a row should *display*, given the calendar.

    Stored terminal states (DELISTED, an escalated NON_COMPLIANT) are
    returned untouched. A running clock is promoted to CLOCK_ACTIVE once
    at least a day has passed since dispatch, and to NON_COMPLIANT once
    the deadline is behind us.
    """
    stored = record.get("status") or STATUS_DISCOVERED
    if stored not in _CLOCK_RUNNING:
        return stored

    deadline = record.get("statutory_deadline")
    if is_overdue(stored, deadline, today=today):
        return STATUS_NON_COMPLIANT

    dispatched = _parse(record.get("date_dispatched"))
    reference = today or _today()
    if dispatched is not None and reference > dispatched:
        return STATUS_CLOCK_ACTIVE
    return STATUS_DISPATCHED


def _enrich(row, today=None) -> dict:
    """A raw row plus the computed fields the timeline renders."""
    record = dict(row)
    record["effective_status"] = derive_status(record, today=today)
    record["days_remaining"] = calculate_days_remaining(
        record.get("statutory_deadline"), today=today
    )
    record["is_overdue"] = is_overdue(
        record.get("status"), record.get("statutory_deadline"), today=today
    )
    return record


# --- state transitions ------------------------------------------------


def create_or_update_campaign(db_path: str, broker_name: str, domain: str,
                              profile_url: str | None = None,
                              statute_invoked: str | None = None) -> int:
    """Register a discovered broker record, or refresh one already known.

    Re-running discovery is the normal case -- a sweep finds the same
    Spokeo profile every week -- so this upserts on (broker_name, domain)
    and only ever fills in the descriptive columns. Lifecycle state
    (status, dispatch date, deadline) is never rewound by a rediscovery;
    finding a profile again does not undo a demand already sent.
    """
    if not broker_name or not domain:
        raise ValueError("broker_name and domain are both required")

    today = _today().strftime(DATE_FORMAT)
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM broker_campaigns WHERE broker_name = ? AND domain = ?",
            (broker_name, domain),
        ).fetchone()

        if existing:
            # COALESCE so a discovery that didn't capture a profile URL
            # doesn't blank one an earlier sweep did capture.
            conn.execute(
                "UPDATE broker_campaigns SET profile_url = COALESCE(?, profile_url), "
                "statute_invoked = COALESCE(?, statute_invoked) WHERE id = ?",
                (profile_url, statute_invoked, existing["id"]),
            )
            conn.commit()
            return existing["id"]

        cursor = conn.execute(
            "INSERT INTO broker_campaigns "
            "(broker_name, domain, profile_url, status, date_discovered, statute_invoked) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                broker_name,
                domain,
                profile_url,
                STATUS_DISCOVERED,
                today,
                statute_invoked or DEFAULT_STATUTE,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def mark_dispatched(db_path: str, campaign_id: int, dispatch_date: str | None = None,
                    window_days: int | None = None) -> None:
    """Record that the statutory demand went out, starting the clock.

    Sets the deadline at the same time rather than computing it on every
    read, so the deadline a user was shown on day 1 is still the deadline
    on day 44 even if the configured window is later changed.
    """
    dispatched = dispatch_date or _today().strftime(DATE_FORMAT)
    if _parse(dispatched) is None:
        raise ValueError(f"dispatch_date must be {DATE_FORMAT}: {dispatch_date!r}")

    deadline = calculate_deadline(dispatched, window_days=window_days)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE broker_campaigns SET status = ?, date_dispatched = ?, "
            "statutory_deadline = ? WHERE id = ?",
            (STATUS_DISPATCHED, dispatched, deadline, campaign_id),
        )
        conn.commit()


def record_recon_check(db_path: str, campaign_id: int, still_listed: bool,
                       evidence: str | None = None, check_date: str | None = None) -> None:
    """Log the outcome of a verification sweep.

    A check that finds the record gone closes the campaign; one that finds
    it still up only stamps last_recon_check, leaving the clock to decide
    whether the row has gone overdue.
    """
    checked = check_date or _today().strftime(DATE_FORMAT)
    if not still_listed:
        record_delisting(db_path, campaign_id, evidence=evidence, removed_date=checked)
        return

    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE broker_campaigns SET last_recon_check = ? WHERE id = ?",
            (checked, campaign_id),
        )
        conn.commit()
    if evidence:
        _append_evidence(db_path, campaign_id, evidence, checked)


def record_delisting(db_path: str, campaign_id: int, evidence: str | None = None,
                     removed_date: str | None = None) -> None:
    """Close a campaign: the record is verified gone."""
    removed = removed_date or _today().strftime(DATE_FORMAT)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE broker_campaigns SET status = ?, date_verified_removed = ?, "
            "last_recon_check = ? WHERE id = ?",
            (STATUS_DELISTED, removed, removed, campaign_id),
        )
        conn.commit()
    if evidence:
        _append_evidence(db_path, campaign_id, evidence, removed)


def mark_non_compliant(db_path: str, campaign_id: int, evidence: str | None = None) -> None:
    """Persist an escalation. Only needed to make the state stick past a
    re-dispatch -- an ordinary blown deadline already reads as
    NON_COMPLIANT without anything being written."""
    stamped = _today().strftime(DATE_FORMAT)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE broker_campaigns SET status = ? WHERE id = ?",
            (STATUS_NON_COMPLIANT, campaign_id),
        )
        conn.commit()
    if evidence:
        _append_evidence(db_path, campaign_id, evidence, stamped)


def _append_evidence(db_path: str, campaign_id: int, evidence: str, stamped: str) -> None:
    """Append one dated line to the evidence log.

    Append rather than overwrite: the log is the proof trail a regulatory
    complaint would rest on, so an escalation must not erase the recon
    checks that justified it.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT evidence_log FROM broker_campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if row is None:
            return
        existing = row["evidence_log"] or ""
        line = f"[{stamped}] {evidence}"
        conn.execute(
            "UPDATE broker_campaigns SET evidence_log = ? WHERE id = ?",
            (f"{existing}\n{line}".strip(), campaign_id),
        )
        conn.commit()


# --- reads ------------------------------------------------------------


def get_campaign(db_path: str, campaign_id: int) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM broker_campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
    return _enrich(row) if row else None


def get_all_campaigns(db_path: str, today=None) -> list[dict]:
    """Every campaign, newest discovery first, with computed fields."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM broker_campaigns ORDER BY date_discovered DESC, id DESC"
        ).fetchall()
    return [_enrich(row, today=today) for row in rows]


def get_active_campaigns(db_path: str, today=None) -> list[dict]:
    """Campaigns still needing attention -- everything not verified gone.

    Overdue rows sort first: the whole point of the pipeline view is that
    a blown statutory window is the thing you act on today.
    """
    active = [
        record for record in get_all_campaigns(db_path, today=today)
        if record["status"] not in _CLOSED
    ]
    # days_remaining is None for undispatched rows; those sort last rather
    # than crashing the comparison against real integers.
    return sorted(
        active,
        key=lambda r: (r["days_remaining"] is None, r["days_remaining"] or 0),
    )


def delete_campaign(db_path: str, campaign_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM broker_campaigns WHERE id = ?", (campaign_id,))
        conn.commit()


def summarize(db_path: str, today=None) -> dict:
    """Counts for the pipeline header, by effective (displayed) status."""
    records = get_all_campaigns(db_path, today=today)
    counts = {status: 0 for status in STATUS_OPTIONS}
    for record in records:
        counts[record["effective_status"]] = counts.get(record["effective_status"], 0) + 1
    counts["TOTAL"] = len(records)
    return counts
