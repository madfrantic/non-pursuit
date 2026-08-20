"""CourtListener RECAP: query public federal and state docket records."""
import asyncio
import aiohttp
from typing import Any, Dict, List

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY

_log = get_logger("osint_courtlistener")

COURTLISTENER_API = "https://www.courtlistener.com/api/rest/v3"


async def _search_dockets(name: str) -> List[Dict[str, Any]]:
    """Search CourtListener dockets for a given full name."""
    if not name:
        return []

    params = {"q": name, "type": "r"}  # r = docket type
    url = f"{COURTLISTENER_API}/dockets/"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("results", [])
                else:
                    _log.warning("CourtListener API returned %d", r.status)
                    return []
    except asyncio.TimeoutError:
        _log.info("CourtListener API timeout")
    except Exception as exc:
        _log.info("CourtListener API error: %s", exc)
    return []


def _normalize_docket_records(raw: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract key fields from CourtListener API response."""
    normalized = []
    for record in raw:
        normalized.append({
            "case_name": record.get("case_name", ""),
            "court": record.get("court_display", ""),
            "date_filed": record.get("date_filed", ""),
            "docket_number": record.get("docket_number", ""),
            "court_url": record.get("resource_uri", ""),
            "nature_of_suit": record.get("nature_of_suit", ""),
        })
    return normalized


async def scan_courtlistener(name: str) -> Dict[str, Any]:
    """Scan CourtListener dockets for public case records matching name."""
    if not name:
        return {
            "status": STATUS_EMPTY,
            "module": "courtlistener",
            "records": [],
            "error": "No name provided",
        }

    dockets = await _search_dockets(name)
    if not dockets:
        return {
            "status": STATUS_EMPTY,
            "module": "courtlistener",
            "records": [],
        }

    normalized = _normalize_docket_records(dockets)
    return {
        "status": STATUS_SUCCESS,
        "module": "courtlistener",
        "records": normalized,
        "count": len(normalized),
    }
