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

__all__ = [
    "STATUS_SUCCESS",
    "STATUS_UNAVAILABLE",
    "STATUS_EMPTY",
]
