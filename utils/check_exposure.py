"""
Exposure checking utilities for searching online presence.
"""
import hashlib
from typing import Dict


def check_online_exposure(search_terms: Dict[str, str]) -> Dict:
    """
    Check if personal information appears in search results.
    This is a simulation - in production you'd use actual search APIs.
    """
    results = {}

    for category, value in search_terms.items():
        if not value:
            results[category] = {"found": False, "count": 0, "risk": "low"}
            continue

        value_hash = hashlib.md5(value.encode()).hexdigest()
        count = int(value_hash[:2], 16) % 10

        if count > 7:
            risk = "critical"
        elif count > 4:
            risk = "high"
        elif count > 0:
            risk = "medium"
        else:
            risk = "low"

        results[category] = {
            "found": count > 0,
            "count": count,
            "risk": risk,
        }

    return results
