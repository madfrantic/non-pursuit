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
        return _unavailable(module, TypeError("scanner returned a non-dict result"))

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


async def run_full_osint_sweep(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute all 5 passive OSINT modules concurrently.
    Returns results keyed by vector type: corporate, legal, finance, developer, infrastructure.
    """
    full_name = profile_data.get("name", "").strip()
    handle = profile_data.get("handle", "").strip()
    domain = profile_data.get("domain", "").strip()
    state = profile_data.get("state", "").strip()

    # Run all 5 scans concurrently
    sec_task = scan_sec(full_name)
    courtlistener_task = scan_courtlistener(full_name)
    fec_task = scan_fec(full_name, state if state else None)
    github_task = scan_github(handle)
    infrastructure_task = scan_infrastructure(domain)

    raw_results = await asyncio.gather(
        sec_task, courtlistener_task, fec_task, github_task, infrastructure_task,
        return_exceptions=True,
    )
    sec_result, cl_result, fec_result, gh_result, infra_result = (
        _normalize_result(module, result)
        for module, result in zip(
            ("sec", "courtlistener", "fec", "github", "infrastructure"),
            raw_results,
        )
    )

    # Aggregate into unified response
    results = {
        "sec": sec_result,
        "courtlistener": cl_result,
        "fec": fec_result,
        "github": gh_result,
        "infrastructure": infra_result,
    }

    return {
        "timestamp": None,  # Caller can set this
        "profile": {
            "name": full_name,
            "handle": handle,
            "domain": domain,
            "state": state,
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
        },
        "summary": {
            "total_exposures": sum([
                sec_result.get("count", 0),
                cl_result.get("count", 0),
                fec_result.get("count", 0),
                gh_result.get("count", 0),
                infra_result.get("cert_count", 0),
            ]),
            "vectors_available": sum([
                1 if sec_result.get("status") == STATUS_SUCCESS else 0,
                1 if cl_result.get("status") == STATUS_SUCCESS else 0,
                1 if fec_result.get("status") == STATUS_SUCCESS else 0,
                1 if gh_result.get("status") == STATUS_SUCCESS else 0,
                1 if infra_result.get("status") == STATUS_SUCCESS else 0,
            ]),
        },
        **results,
    }
