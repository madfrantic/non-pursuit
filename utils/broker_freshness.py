"""
Staleness check for brokers.csv itself, not just the user's own exposure
checks. Each row's `last_verified` (e.g. "2026-08") records the last time
someone actually confirmed the compliance email / opt-out URL / notes for
that broker still work. Broker contact info and opt-out flows do drift
(companies change emails, forms move) but far slower than a user's own
exposure answers, so this uses its own longer threshold rather than reusing
exposure_store's RECHECK_STALE_DAYS.

last_verified is a "YYYY-MM" string, not a full date (nobody tracks broker
info to the day), so this can't reuse exposure_store.is_stale directly --
it parses month granularity and treats every stale row as verified on the
1st of that month, which only ever under-counts how stale a row is, never
over-counts it.
"""
from datetime import datetime


def days_since_verified(last_verified: str) -> int | None:
    """None if last_verified is missing or not parseable as YYYY-MM."""
    if not last_verified:
        return None
    try:
        verified_date = datetime.strptime(last_verified.strip(), "%Y-%m").date()
    except ValueError:
        return None
    return (datetime.now().date() - verified_date).days


def is_broker_stale(last_verified: str, stale_days: int) -> bool:
    """True if last_verified is old enough to need a fresh check, or if
    it's missing/unparseable -- either way there's nothing to trust yet."""
    days = days_since_verified(last_verified)
    if days is None:
        return True
    return days >= stale_days
