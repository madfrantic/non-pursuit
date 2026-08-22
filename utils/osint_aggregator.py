"""OSINT Aggregator: orchestrate all passive reconnaissance modules concurrently."""
import asyncio
from typing import Any, Dict

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY
from osint.sec import scan_sec
from osint.courtlistener import scan_courtlistener
from osint.fec import scan_fec
from osint.github import scan_github
from osint.infrastructure import scan_infrastructure

import wmn_dataset
import footprint_scanner
import email_scanner
import config

_log = get_logger("osint_aggregator")


def _unavailable(module: str, exc: Exception) -> Dict[str, Any]:
    """Return a stable result when a scanner or endpoint fails unexpectedly."""
    _log.warning("OSINT %s scan failed: %s", module, exc)
    result = {
        "status": STATUS_UNAVAILABLE,
        "module": module,
        "records": [],
        "count": 0,
        "error": str(exc),
    }
    if module == "infrastructure":
        result.update({"certificates": [], "domain_info": {}, "cert_count": 0})
    return result


def _normalize_result(module: str, result: Any) -> Dict[str, Any]:
    if isinstance(result, Exception):
        return _unavailable(module, result)
    if not isinstance(result, dict):
        if isinstance(result, list):
            # Footprint results include every verdict (CONFIRMED, POSSIBLE,
            # NOT_FOUND, ERROR).  Only confirmed discoveries should appear
            # in the dossier — the full list with its ambiguous rows stays
            # available in the dedicated Footprint page.
            if module == "footprint":
                confirmed = footprint_scanner.discoveries(result, confident_only=True)
                return {"module": module, "status": STATUS_SUCCESS if confirmed else STATUS_EMPTY,
                        "records": confirmed, "count": len(confirmed)}
            return {"module": module, "status": STATUS_SUCCESS if result else STATUS_EMPTY, "records": result, "count": len(result)}
        return _unavailable(module, TypeError("scanner returned unexpected type"))

    normalized = dict(result)
    normalized.setdefault("module", module)
    normalized.setdefault("status", STATUS_UNAVAILABLE)
    if module == "infrastructure":
        normalized.setdefault("certificates", [])
        normalized.setdefault("domain_info", {})
        normalized.setdefault("cert_count", len(normalized["certificates"]))
        normalized.setdefault("count", normalized["cert_count"])
    else:
        normalized.setdefault("records", [])
        normalized.setdefault("count", len(normalized["records"]))
    return normalized


async def _run_footprint(handle: str) -> list:
    """Scan handle across WhatsMyName dataset with resilient error handling."""
    if not handle:
        return []
    try:
        dataset, _ = await asyncio.to_thread(wmn_dataset.ensure_dataset, config.WMN_DATASET_PATH, False, config.FOOTPRINT_TIMEOUT_SECONDS)
        sites = wmn_dataset.select_sites(dataset, deep=True)
        results = await footprint_scanner._scan(handle, sites, config.FOOTPRINT_CONCURRENCY, config.FOOTPRINT_TIMEOUT_SECONDS, None, config.FOOTPRINT_PER_HOST_CONCURRENCY)
        # Ensure we always return a list
        return results if isinstance(results, list) else []
    except asyncio.TimeoutError:
        _log.warning("Footprint scan timed out for %s", handle)
        return []  # Return empty list, not exception
    except Exception as exc:
        _log.error("Footprint scan failed: %s", exc)
        return []  # Return empty list instead of exception object

async def _run_email(email: str) -> Dict[str, Any]:
    """Scan email through passive OSINT vectors with resilient error handling.

    Returns a result dict rather than a bare list so that "what we found"
    and "what we checked" stay separate. scan_email() emits one row per
    check including the misses; counting those as records reported four
    findings for every address, whether or not anything was actually
    found. `records` is now the real hits only, and the full check log
    rides along under `checks` for the UI to show its work.
    """
    empty = {"module": "email", "status": STATUS_EMPTY, "records": [], "count": 0, "checks": []}
    if not email or "@" not in email:
        return empty
    try:
        results = await asyncio.to_thread(email_scanner.scan_email, email)
        if not isinstance(results, list):
            return empty
        findings = email_scanner.exposure_findings(results)
        return {
            "module": "email",
            "status": STATUS_SUCCESS if findings else STATUS_EMPTY,
            "records": findings,
            "count": len(findings),
            "checks": results,
        }
    except asyncio.TimeoutError:
        _log.warning("Email scan timed out")
        return {**empty, "status": STATUS_UNAVAILABLE}
    except Exception as exc:
        _log.error("Email scan failed: %s", exc)
        return {**empty, "status": STATUS_UNAVAILABLE}


async def run_full_osint_sweep(profile_data: Dict[str, Any], passive_only: bool = False) -> Dict[str, Any]:
    """
    Execute all passive OSINT modules concurrently.
    """
    full_name = profile_data.get("name", "").strip()
    handle = profile_data.get("handle", "").strip()
    domain = profile_data.get("domain", "").strip()
    state = profile_data.get("state", "").strip()
    email = profile_data.get("email", "").strip()

    sec_task = scan_sec(full_name)
    courtlistener_task = scan_courtlistener(full_name)
    fec_task = scan_fec(full_name, state if state else None)
    github_task = scan_github(handle)
    infrastructure_task = scan_infrastructure(domain)
    
    # In passive-only mode (cloud), we skip the heavy WhatsMyName footprint scan
    if passive_only:
        async def _mock_footprint():
            return []
        footprint_task = _mock_footprint()
    else:
        footprint_task = _run_footprint(handle)
        
    email_task = _run_email(email)

    raw_results = await asyncio.gather(
        sec_task, courtlistener_task, fec_task, github_task, infrastructure_task,
        footprint_task, email_task,
        return_exceptions=True,
    )
    
    sec_result, cl_result, fec_result, gh_result, infra_result, fp_result, em_result = (
        _normalize_result(module, result)
        for module, result in zip(
            ("sec", "courtlistener", "fec", "github", "infrastructure", "footprint", "email"),
            raw_results,
        )
    )

    results = {
        "sec": sec_result,
        "courtlistener": cl_result,
        "fec": fec_result,
        "github": gh_result,
        "infrastructure": infra_result,
        "footprint": fp_result,
        "email": em_result,
    }

    total_exposures = sum([
        sec_result.get("count", 0),
        cl_result.get("count", 0),
        fec_result.get("count", 0),
        gh_result.get("count", 0),
        infra_result.get("cert_count", 0),
        fp_result.get("count", 0),
        em_result.get("count", 0),
    ])

    return {
        "timestamp": None,
        "profile": {
            "name": full_name,
            "handle": handle,
            "domain": domain,
            "state": state,
            "email": email,
        },
        "vectors": {
            "corporate": {
                "module": "sec",
                "status": sec_result.get("status", STATUS_UNAVAILABLE),
                "records": sec_result.get("records", []),
                "count": sec_result.get("count", 0),
            },
            "legal": {
                "module": "courtlistener",
                "status": cl_result.get("status", STATUS_UNAVAILABLE),
                "records": cl_result.get("records", []),
                "count": cl_result.get("count", 0),
            },
            "finance": {
                "module": "fec",
                "status": fec_result.get("status", STATUS_UNAVAILABLE),
                "records": fec_result.get("records", []),
                "count": fec_result.get("count", 0),
            },
            "developer": {
                "module": "github",
                "status": gh_result.get("status", STATUS_UNAVAILABLE),
                "records": gh_result.get("records", []),
                "count": gh_result.get("count", 0),
            },
            "infrastructure": {
                "module": "infrastructure",
                "status": infra_result.get("status", STATUS_UNAVAILABLE),
                "certificates": infra_result.get("certificates", []),
                "domain_info": infra_result.get("domain_info", {}),
                "count": infra_result.get("cert_count", 0),
            },
            "footprint": {
                "module": "footprint",
                "status": fp_result.get("status", STATUS_UNAVAILABLE),
                "records": fp_result.get("records", []),
                "count": fp_result.get("count", 0),
            },
            "email": {
                "module": "email",
                "status": em_result.get("status", STATUS_UNAVAILABLE),
                "records": em_result.get("records", []),
                "count": em_result.get("count", 0),
                "checks": em_result.get("checks", []),
            },
        },
        "summary": {
            "total_exposures": total_exposures,
            "vectors_available": sum([
                1 if sec_result.get("status") == STATUS_SUCCESS else 0,
                1 if cl_result.get("status") == STATUS_SUCCESS else 0,
                1 if fec_result.get("status") == STATUS_SUCCESS else 0,
                1 if gh_result.get("status") == STATUS_SUCCESS else 0,
                1 if infra_result.get("status") == STATUS_SUCCESS else 0,
                1 if fp_result.get("status") == STATUS_SUCCESS else 0,
                1 if em_result.get("status") == STATUS_SUCCESS else 0,
            ]),
        },
        **results,
    }
