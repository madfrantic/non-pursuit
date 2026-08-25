"""
Passive OSINT engine: multi-vector, async, unauthenticated/free-tier API lookups.

All modules fail gracefully (set status to UNAVAILABLE on API error) and never
raise exceptions. Results are normalized into a unified exposure report dict.
Nothing is persisted to disk — findings live in session_state only.
"""
from typing import Any, Dict

# Status indicators for each OSINT module
STATUS_SUCCESS = "success"
STATUS_UNAVAILABLE = "unavailable"  # API down, rate limited, or network error
STATUS_EMPTY = "empty"  # API responded OK but found no records
# The module was never run, by policy rather than by failure -- currently
# only the handle sweep under passive_only. Distinct from STATUS_EMPTY on
# purpose: "we looked and found nothing" and "we never looked" are
# different answers, and reporting the second as the first is a false
# negative about a scan that did not happen.
STATUS_SKIPPED = "skipped"

__all__ = [
    "STATUS_SUCCESS",
    "STATUS_UNAVAILABLE",
    "STATUS_EMPTY",
    "STATUS_SKIPPED",
]
