"""FEC campaign finance: query contributor disclosure records."""
import asyncio
import aiohttp
from typing import Any, Dict, List

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY

_log = get_logger("osint_fec")

FEC_API = "https://api.open.fec.gov/v1"
FEC_DEMO_KEY = "DEMO_KEY"  # Free tier key documented at FEC site


async def _search_contributions(name: str, state: str = None) -> List[Dict[str, Any]]:
    """
    Query FEC Schedule A contributions (itemized donations) matching
    contributor name. Returns contributor records with address and employer.
    """
    if not name:
        return []

    params = {
        "api_key": FEC_DEMO_KEY,
        "contributor_name": name,
        "per_page": 100,  # Reasonable limit for this query
    }
    if state:
        params["contributor_state"] = state.upper()

    url = f"{FEC_API}/schedules/schedule_a/"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=45)) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("results", [])
                else:
                    _log.warning("FEC API returned %d", r.status)
                    return []
    except asyncio.TimeoutError:
        _log.info("FEC API timeout")
    except Exception as exc:
        _log.info("FEC API error: %s", exc)
    return []


def _normalize_fec_records(raw: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract key fields from FEC API response."""
    normalized = []
    for record in raw:
        normalized.append({
            "contributor_name": record.get("contributor_name", ""),
            "contributor_address": (record.get("contributor_city", "") + ", " +
                                   record.get("contributor_state", "") + " " +
                                   record.get("contributor_zip", "")).strip(),
            "employer": record.get("employer", ""),
            "occupation": record.get("occupation", ""),
            "contribution_receipt_date": record.get("contribution_receipt_date", ""),
            "contribution_amount": record.get("contribution_amount", 0),
            "recipient_committee": record.get("committee", {}).get("name", ""),
        })
    return normalized


async def scan_fec(name: str, state: str = None) -> Dict[str, Any]:
    """Scan FEC contribution records matching contributor name."""
    if not name:
        return {
            "status": STATUS_EMPTY,
            "module": "fec",
            "records": [],
            "error": "No name provided",
        }

    records = await _search_contributions(name, state)
    if not records:
        return {
            "status": STATUS_EMPTY,
            "module": "fec",
            "records": [],
        }

    normalized = _normalize_fec_records(records)
    return {
        "status": STATUS_SUCCESS,
        "module": "fec",
        "records": normalized,
        "count": len(normalized),
    }
