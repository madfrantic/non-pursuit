"""
The work only a person can finish.

Non-Pursuit already refuses to act unsupervised in three places: the
remediation API drafts letters and cannot post one, the opt-out engine fills
a broker's form and stops before submitting, and unsigned templates come back
marked `requires_human_signoff`. Each of those correctly declines to act --
and then drops the thread. Nothing recorded that a human still owed the task,
so a dry-run fill that nobody went back to finish looked exactly like a
completed removal.

This is that missing half: a durable list of the steps automation deliberately
stopped short of. Ported from the Privacy Agent coursework scaffold
(Documents/PURSUIT/deepseek.py), which had the idea as an in-memory list.

TWO CHANGES FROM THE SCAFFOLD, BOTH OF THEM BUGS THERE
------------------------------------------------------

1. Items are addressed by row id, never by position. The scaffold's UI listed
   *pending* items and then passed the selected row number into a function
   that indexed the *full* list, so completing anything made every later
   click resolve the wrong task -- silently, and in the direction of marking
   undone work done.

2. The store is opened per call and closed again, like utils/tracker.py.
   The scaffold held one sqlite3 connection on the object and read it from a
   QThread and an APScheduler thread, which sqlite3 refuses by default.

WHY ITS OWN DATABASE

data/tracker.db is a Human Validation Zone under docs/SAFETY_BOUNDARIES.md -- a schema change
there needs manual sign-off before it is written. A queue of outstanding
chores is also a different lifetime from a statutory campaign: it churns
daily and carries no deadline math. So it lives beside data/usage_metrics.db
as its own file, on the same reasoning that put that one there.

PII: a `note` is free text and a user will eventually paste a name into one.
The file is gitignored for that reason. `reason` is not free text -- it comes
from REASONS, so the *why* of a queued item can never become a dumping
ground, the same constraint utils/usage_metrics.py puts on event names.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Why automation stopped. A closed vocabulary; add_item() rejects anything
# else, so this column stays aggregatable and free of typed-in text.
CAPTCHA = "captcha"
TOS_PROHIBITS = "tos_prohibits_automation"
IDENTITY_VERIFICATION = "identity_verification_required"
NO_AUTOMATOR = "no_automator_for_broker"
SUBMIT_REQUIRES_SIGNOFF = "submit_requires_signoff"
SITE_UNREACHABLE = "site_unreachable"

REASONS = {
    CAPTCHA: "A CAPTCHA or bot check blocks automation. Solve it yourself; "
             "do not attempt to bypass it.",
    TOS_PROHIBITS: "The site's terms prohibit automated access. Complete this "
                   "one by hand.",
    IDENTITY_VERIFICATION: "The broker wants ID or a notarised request before "
                           "it will act.",
    NO_AUTOMATOR: "No opt-out automator exists for this broker yet.",
    SUBMIT_REQUIRES_SIGNOFF: "The form is filled and waiting. A person reviews "
                             "it and presses submit.",
    SITE_UNREACHABLE: "The opt-out page did not load. Retry, then check "
                      "whether it moved.",
}

PENDING = "pending"
RESOLVED = "resolved"
SKIPPED = "skipped"
OUTCOMES = (RESOLVED, SKIPPED)

# Lower sorts first. SUBMIT_REQUIRES_SIGNOFF outranks everything because that
# work is already done and is one click from finished; leaving it queued
# wastes a completed form.
PRIORITY = {
    SUBMIT_REQUIRES_SIGNOFF: 1,
    IDENTITY_VERIFICATION: 2,
    CAPTCHA: 3,
    TOS_PROHIBITS: 4,
    SITE_UNREACHABLE: 5,
    NO_AUTOMATOR: 6,
}

MAX_NOTE_CHARS = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect(db_path: str):
    """One place that opens, creates, and always closes the connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            task_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """
    )
    # Migration: chino's manual_queue carried two columns this table did
    # not -- where the blocked task stopped, and the screenshot taken at
    # that moment. Merging that queue into this one (rather than keeping
    # two) means bringing those across. Older databases predate them, so
    # they are added only when PRAGMA table_info says they are missing.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(review_items)")}
    for column in ("current_url", "screenshot_path"):
        if column not in existing:
            conn.execute(f"ALTER TABLE review_items ADD COLUMN {column} TEXT")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    item = dict(row)
    item["priority"] = PRIORITY.get(item["reason"], 99)
    item["guidance"] = REASONS.get(item["reason"], "")
    return item


def add_item(db_path: str, target: str, task_type: str, reason: str,
             note: str = "", current_url: str = "",
             screenshot_path: str = "") -> int:
    """Queue one piece of human work. Returns its id.

    `target` is whatever the task is about -- a broker id, a platform name.
    `reason` must come from REASONS; an unknown one raises rather than being
    stored, so this column stays countable.
    """
    if reason not in REASONS:
        raise ValueError(
            f"Unknown reason {reason!r}; expected one of {sorted(REASONS)}")
    if not str(target).strip():
        raise ValueError("target is required: a queued task nobody can locate "
                         "is not actionable")

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_items
                (target, task_type, reason, note, status, created_at,
                 current_url, screenshot_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(target).strip(), str(task_type).strip() or "other", reason,
             str(note)[:MAX_NOTE_CHARS], PENDING, _now(),
             str(current_url).strip(), str(screenshot_path).strip()),
        )
        return cursor.lastrowid


def pending_items(db_path: str) -> list[dict]:
    """Outstanding work, most urgent first, then oldest first.

    Each row carries its `id`. Resolve by that id -- never by the position of
    a row in this list, which is the bug the module docstring describes.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM review_items WHERE status = ? ORDER BY id",
            (PENDING,),
        ).fetchall()
    items = [_row_to_dict(row) for row in rows]
    return sorted(items, key=lambda item: (item["priority"], item["id"]))


def all_items(db_path: str) -> list[dict]:
    """Every item, pending or not, newest last."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM review_items ORDER BY id").fetchall()
    return [_row_to_dict(row) for row in rows]


def resolve_item(db_path: str, item_id: int, outcome: str = RESOLVED,
                 note: str = "") -> bool:
    """Close one item by id. Returns False if it was already closed.

    Re-resolving is a no-op rather than an error: two clicks on the same row
    should not raise, but the first resolution's timestamp is the true one
    and is not overwritten.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome {outcome!r}; expected one of {OUTCOMES}")

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, note FROM review_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No review item with id {item_id}")
        if row["status"] != PENDING:
            return False

        merged = str(note)[:MAX_NOTE_CHARS] if note else row["note"]
        conn.execute(
            "UPDATE review_items SET status = ?, resolved_at = ?, note = ? "
            "WHERE id = ?",
            (outcome, _now(), merged, item_id),
        )
    return True


def stats(db_path: str) -> dict:
    """Counts by status, plus what is blocking the pending work."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, reason, COUNT(*) AS n FROM review_items "
            "GROUP BY status, reason"
        ).fetchall()

    counts = {PENDING: 0, RESOLVED: 0, SKIPPED: 0}
    blocking = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + row["n"]
        if row["status"] == PENDING:
            blocking[row["reason"]] = blocking.get(row["reason"], 0) + row["n"]

    counts["total"] = sum(counts[key] for key in (PENDING, RESOLVED, SKIPPED))
    counts["blocking"] = dict(sorted(blocking.items(), key=lambda kv: -kv[1]))
    return counts


def clear_closed(db_path: str) -> int:
    """Drop resolved and skipped rows. Returns how many went."""
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM review_items WHERE status IN (?, ?)", (RESOLVED, SKIPPED))
        return cursor.rowcount
