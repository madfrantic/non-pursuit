"""
Digital Footprint Matrix -- offline platform cross-reference.

Takes an email or a handle, cross-references it against the static
registry in data/footprint_map.json, and persists the hits to a
`footprint_results` table in the same tracker.db every other store uses.

This module makes **no network calls of any kind** and has no code path
that can. There is no HTTP client imported here, so the conftest socket
block isn't what keeps it offline -- the absence of a caller is. The test
suite asserts that property directly rather than trusting this docstring.

Why a simulated matrix and not a real sweep: the app already has a real
one. utils/footprint_scanner.py sweeps ~700 platforms live off the
WhatsMyName dataset, and utils/email_scanner.py does passive email
recon. This module exists for the case those two can't serve -- a
demo/showcase render that must be instant, reproducible, and safe to run
from a shared host against an audience member's address. Every row it
produces is therefore stamped simulated=1, and callers that surface
these rows are expected to say so. A simulated hit rendered as a real
finding is the one failure mode that matters here, because this app's
whole output is a legally binding demand letter.

## holehe compatibility

Field names on the way out (`exists`, `emailrecovery`, `phoneNumber`,
`others`, `rateLimit`) mirror the per-module result dict that holehe
(github.com/megadose/holehe) returns, and the registry records the
matching upstream module name where one exists. That means a holehe
result and a matrix result deserialise into the same shape and land in
the same table without a translation layer.

What is deliberately NOT ported is holehe's probe mechanism. holehe
determines account existence by submitting a target's address to
password-reset, registration and forgot-password endpoints on ~120
sites. Three reasons that stays out of this module:

  * It is live egress, which this component was specified not to do.
  * utils/email_scanner.py already draws this exact boundary in its own
    docstring -- "no form probes, password resets, registration hits, or
    side effects" -- and it is a design constraint of the app, not an
    oversight.
  * A password-reset probe can deliver mail to the target's inbox. For
    an app whose premise is mapping someone's exposure without alerting
    them, a scan that emails the subject is self-defeating.

`register_prober()` is the seam if that tradeoff is ever accepted
deliberately. Nothing is registered by default, ships, or is reachable
from the UI; see the note on the function itself.
"""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from applog import get_logger

_log = get_logger("footprint_matrix")

FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"
UNSUPPORTED = "UNSUPPORTED"

IDENTIFIER_EMAIL = "email"
IDENTIFIER_HANDLE = "handle"

SOURCE_OFFLINE = "offline_matrix"

DEFAULT_MAP_PATH = "data/footprint_map.json"

# Deliberately loose. This decides which *lookup rules* apply, not
# whether an address is deliverable -- rejecting an unusual-but-valid
# address would silently drop platforms from the matrix, which is a worse
# failure than accepting a malformed one and finding nothing.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 8 hex chars -> 32 bits. Enough spread for a 15-row matrix and small
# enough that the bucket maths stays exact in a float64.
_HASH_HEX_CHARS = 8
_HASH_MAX = float(0xFFFFFFFF)


# --- identifier handling ----------------------------------------------

def classify_identifier(identifier: str) -> str:
    """email vs handle. Drives which platforms are even applicable --
    Adobe has no public profile namespace so it accepts email only, while
    Reddit and Mastodon are handle-only."""
    return IDENTIFIER_EMAIL if _EMAIL_RE.match((identifier or "").strip()) else IDENTIFIER_HANDLE


def normalize_identifier(identifier: str) -> str:
    """Lowercase and trim, and strip a leading @ from handles.

    Normalisation happens once, here, and the result is what gets hashed,
    stored, and used as the upsert key. If '@Nonpursuit' and 'nonpursuit'
    normalised differently they would hash to different buckets and land
    as two rows for one account, so every entry point routes through
    this.
    """
    cleaned = (identifier or "").strip().lower()
    return cleaned[1:] if cleaned.startswith("@") else cleaned


# --- registry ---------------------------------------------------------

def load_map(map_path: str = DEFAULT_MAP_PATH) -> dict:
    """Read the registry. Not cached here -- Streamlit's @st.cache_data
    belongs at the component boundary, and a module-level cache would
    make the file untestable without a reload dance."""
    with open(map_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def platforms(footprint_map: dict) -> list:
    return footprint_map.get("platforms", [])


# --- mock status check logic ------------------------------------------

def _bucket(normalized: str, platform_key: str) -> float:
    """Stable float in [0, 1) for one (identifier, platform) pair.

    SHA-256 rather than Python's hash() on purpose: hash() is salted per
    process (PYTHONHASHSEED), so the same demo would produce a different
    matrix on every launch. This is reproducible across processes,
    machines and runs, which is what makes the showcase rehearsable.
    """
    digest = hashlib.sha256(f"{normalized}|{platform_key}".encode("utf-8")).hexdigest()
    return int(digest[:_HASH_HEX_CHARS], 16) / _HASH_MAX


def check_platform(normalized: str, identifier_type: str, platform: dict,
                   footprint_map: dict) -> dict:
    """Resolve one platform for one identifier. Pure, offline, total.

    Precedence:
      1. platform doesn't accept this identifier type -> UNSUPPORTED
      2. identifier is seeded -> FOUND iff listed (the demo-control path)
      3. otherwise -> deterministic hash bucket vs the platform hit_rate

    UNSUPPORTED is kept distinct from NOT_FOUND because they mean
    different things to a user reading the grid: "we looked and found
    nothing" versus "this platform can't be queried by the thing you
    typed". Collapsing them would imply a clean result where no lookup
    happened at all.
    """
    if identifier_type not in platform.get("accepts", []):
        verdict = UNSUPPORTED
    else:
        seeded = footprint_map.get("seeded_identifiers", {}).get(normalized)
        if seeded is not None:
            verdict = FOUND if platform["key"] in seeded.get("found", []) else NOT_FOUND
        else:
            verdict = FOUND if _bucket(normalized, platform["key"]) < platform.get("hit_rate", 0) else NOT_FOUND

    recovery = platform.get("recovery") or {}
    found = verdict == FOUND

    return {
        "platform": platform["name"],
        "platform_key": platform["key"],
        "domain": platform.get("domain", ""),
        "category": platform.get("category", ""),
        "identifier": normalized,
        "identifier_type": identifier_type,
        "verdict": verdict,
        "profile_url": platform.get("profile_url", "").replace("{account}", normalized) if found else "",
        "holehe_module": platform.get("holehe_module"),
        # holehe-schema fields. Only populated on a hit -- holehe itself
        # returns these as None for a module that reports exists=False.
        "exists": found,
        "emailrecovery": recovery.get("emailrecovery") if found else None,
        "phoneNumber": recovery.get("phoneNumber") if found else None,
        "others": recovery.get("others") if found else None,
        "rateLimit": bool(platform.get("rate_limit", False)),
        "source": SOURCE_OFFLINE,
        "simulated": True,
    }


def scan(identifier: str, map_path: str = DEFAULT_MAP_PATH,
         footprint_map: dict | None = None) -> list:
    """Cross-reference one identifier against every platform in the
    registry. Returns every row including misses -- the UI grid renders
    the full matrix, and filtering to hits is the caller's call.

    An empty identifier returns [] rather than raising: the component
    calls this on every Streamlit rerun, including the first one where
    the input box is still empty.
    """
    normalized = normalize_identifier(identifier)
    if not normalized:
        return []

    footprint_map = footprint_map if footprint_map is not None else load_map(map_path)
    identifier_type = classify_identifier(normalized)

    return [
        check_platform(normalized, identifier_type, platform, footprint_map)
        for platform in platforms(footprint_map)
    ]


def hits(results: list) -> list:
    """Just the FOUND rows, alphabetical."""
    return sorted((r for r in results if r["verdict"] == FOUND),
                  key=lambda r: r["platform"].lower())


def summarize(results: list) -> dict:
    summary = {FOUND: 0, NOT_FOUND: 0, UNSUPPORTED: 0}
    for result in results:
        summary[result["verdict"]] = summary.get(result["verdict"], 0) + 1
    return summary


# --- persistence ------------------------------------------------------

@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS footprint_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier TEXT NOT NULL,
            identifier_type TEXT NOT NULL,
            platform TEXT NOT NULL,
            platform_key TEXT NOT NULL,
            domain TEXT,
            category TEXT,
            profile_url TEXT,
            holehe_module TEXT,
            exists_flag INTEGER NOT NULL,
            email_recovery TEXT,
            phone_number TEXT,
            others TEXT,
            rate_limit INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            simulated INTEGER NOT NULL DEFAULT 1,
            scanned_at TEXT NOT NULL,
            UNIQUE(identifier, platform_key)
        )
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    with _connect(db_path):
        pass


def save_results(db_path: str, results: list, include_misses: bool = False) -> int:
    """Persist a matrix. Returns rows written.

    Misses are dropped by default. A table named footprint_results that
    also stores every non-result would inflate the audit export's counts
    with 15 rows per scan of things that were never found -- the same
    reasoning discovered_accounts.py applies to NOT_FOUND.

    `exists` is a reserved word in SQL, hence exists_flag on the column
    while the dict key stays holehe-shaped.
    """
    rows = results if include_misses else [r for r in results if r["verdict"] == FOUND]
    if not rows:
        return 0

    stamped = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = [
        (
            row["identifier"],
            row["identifier_type"],
            row["platform"],
            row["platform_key"],
            row.get("domain", ""),
            row.get("category", ""),
            row.get("profile_url", ""),
            row.get("holehe_module"),
            int(bool(row.get("exists"))),
            row.get("emailrecovery"),
            row.get("phoneNumber"),
            json.dumps(row["others"]) if row.get("others") is not None else None,
            int(bool(row.get("rateLimit"))),
            row.get("source", SOURCE_OFFLINE),
            int(bool(row.get("simulated", True))),
            stamped,
        )
        for row in rows
    ]

    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO footprint_results
                (identifier, identifier_type, platform, platform_key, domain,
                 category, profile_url, holehe_module, exists_flag, email_recovery,
                 phone_number, others, rate_limit, source, simulated, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identifier, platform_key) DO UPDATE SET
                exists_flag = excluded.exists_flag,
                profile_url = excluded.profile_url,
                email_recovery = excluded.email_recovery,
                phone_number = excluded.phone_number,
                others = excluded.others,
                rate_limit = excluded.rate_limit,
                source = excluded.source,
                simulated = excluded.simulated,
                scanned_at = excluded.scanned_at
            """,
            payload,
        )
        conn.commit()
    _log.info("Stored %d footprint row(s) from %s", len(payload), SOURCE_OFFLINE)
    return len(payload)


def get_results(db_path: str, identifier: str | None = None) -> list:
    """Stored rows, newest scan first. Optionally scoped to one identifier."""
    with _connect(db_path) as conn:
        if identifier:
            cursor = conn.execute(
                "SELECT * FROM footprint_results WHERE identifier = ? "
                "ORDER BY scanned_at DESC, platform ASC",
                (normalize_identifier(identifier),),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM footprint_results ORDER BY scanned_at DESC, platform ASC"
            )
        return [dict(row) for row in cursor.fetchall()]


def delete_results(db_path: str, identifier: str) -> int:
    """Purge every row for one identifier. These rows are PII (an address
    plus the platforms it resolves to), so the user needs a way to remove
    them that doesn't involve deleting the whole database."""
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM footprint_results WHERE identifier = ?",
            (normalize_identifier(identifier),),
        )
        conn.commit()
        return cursor.rowcount


def to_discovered_rows(results: list) -> list:
    """Reshape FOUND rows into discovered_accounts payloads, so a matrix
    hit can join the existing triage worklist instead of living in a
    parallel universe with its own UI.

    Confidence is POSSIBLE, never CONFIRMED. These rows are simulated;
    promoting one to CONFIRMED would let a mock hit flow into the audit
    package as an established fact.
    """
    return [
        {
            "platform": row["platform"],
            "category": row.get("category", ""),
            "target_identifier": row["identifier"],
            "profile_url": row.get("profile_url", ""),
            "avatar_url": "",
            "confidence": "POSSIBLE",
            "reason": "simulated matrix hit (offline dataset) — verify before acting",
        }
        for row in hits(results)
    ]


# --- live-prober seam (nothing registered by default) -----------------

_PROBERS: dict = {}


def register_prober(name: str, prober) -> None:
    """Register a live prober -- e.g. an adapter around holehe.

    Nothing is registered by default and nothing in this repo calls this.
    It exists so that adopting live probing later is an explicit, visible
    act in a diff rather than a quiet edit to scan().

    Before wiring holehe in behind this, three things need a decision
    that isn't this module's to make:
      * conftest.py blocks sockets suite-wide, so any live prober must be
        mocked in tests or the suite goes red.
      * holehe's probes hit password-reset and registration endpoints,
        which can deliver mail to the subject's inbox.
      * running it against an address that isn't the operator's own is a
        consent question, and on a shared demo host it is also a
        rate-limit question.
    """
    _PROBERS[name] = prober


def available_probers() -> list:
    """Registered live probers. Empty in a default install -- the UI reads
    this to state plainly that the matrix is offline-only."""
    return sorted(_PROBERS)
