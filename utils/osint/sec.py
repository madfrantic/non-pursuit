"""SEC EDGAR: query full-text search for officer/director filings."""
import asyncio
import aiohttp
from typing import Any, Dict, List

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY

_log = get_logger("osint_sec")

SEC_API = "https://efts.sec.gov/LATEST/search-index"


async def _search_filings(name: str) -> List[Dict[str, Any]]:
    """
    Query SEC EDGAR full-text search for filings mentioning the name.
    Returns officer/director filings with CIK, entity name, and filing dates.
    """
    if not name:
        return []

    params = {"q": f'"{name}"', "forms": "4"}  # Form 4 = officer/director filings
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SEC_API, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("hits", {}).get("hits", [])
                else:
                    _log.warning("SEC API returned %d", r.status)
                    return []
    except asyncio.TimeoutError:
        _log.info("SEC API timeout")
    except Exception as exc:
        _log.info("SEC API error: %s", exc)
    return []


def _normalize_sec_records(raw: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract key fields from SEC API response."""
    normalized = []
    for hit in raw:
        source = hit.get("_source", {})
        normalized.append({
            "entity_name": source.get("entity", {}).get("name", ""),
            "cik": source.get("ciks", [""])[0],
            "filing_type": source.get("file-type", ""),
            "filing_date": source.get("filed", ""),
            "accession_number": source.get("accession-number", ""),
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&accession_number={source.get('accession-number','')}"
            if source.get("accession-number") else "",
        })
    return normalized


async def scan_sec(name: str) -> Dict[str, Any]:
    """Scan SEC EDGAR for officer/director filings matching name."""
    if not name:
        return {
            "status": STATUS_EMPTY,
            "module": "sec",
            "records": [],
            "error": "No name provided",
        }

    filings = await _search_filings(name)
    if not filings:
        return {
            "status": STATUS_EMPTY,
            "module": "sec",
            "records": [],
        }

    normalized = _normalize_sec_records(filings)
    return {
        "status": STATUS_SUCCESS,
        "module": "sec",
        "records": normalized,
        "count": len(normalized),
    }
