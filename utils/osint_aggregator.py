"""OSINT Aggregator: orchestrate all passive reconnaissance modules concurrently."""
import asyncio
from typing import Any, Dict

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY, STATUS_SKIPPED
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
                confirmed = footprint_scanner.discoveries(result, confident_only=False)
                # `probed` is how many platforms were actually contacted.
                # Without it the UI can report "0 findings" but not
                # whether that came from 52 checks or 678, which is the
                # difference between a thin answer and a thorough one.
                return {"module": module, "status": STATUS_SUCCESS if confirmed else STATUS_EMPTY,
                        "records": confirmed, "count": len(confirmed),
                        "probed": len(result)}
            if module == "email":
                findings = email_scanner.exposure_findings(result)
                return {
                    "module": module,
                    "status": STATUS_SUCCESS if findings else STATUS_EMPTY,
                    "records": findings,
                    "count": len(findings),
                    "checks": result,
                }
            return {"module": module, "status": STATUS_SUCCESS if result else STATUS_EMPTY, "records": result, "count": len(result)}
        return _unavailable(module, TypeError("scanner returned unexpected type"))

    normalized = dict(result)
    normalized.setdefault("module", module)
    normalized.setdefault("status", STATUS_UNAVAILABLE)
    if module == "infrastructure":
        normalized.setdefault("certificates", [])
        normalized.setdefault("domain_info", {})
        normalized["cert_count"] = len(normalized["certificates"])
        normalized["count"] = normalized["cert_count"]
    else:
        normalized.setdefault("records", [])
        normalized["count"] = len(normalized["records"])
    return normalized


async def _run_footprint(handle: str, deep: bool = True) -> list:
    """Scan handle across WhatsMyName dataset with resilient error handling.

    `deep` picks the site list: the full catalogue (678 platforms, NSFW
    excluded) or the curated fast subset (52). Both are real scans that
    return real findings -- the subset is the same probe against fewer
    platforms, not a weaker or simulated one. The online runtime takes
    the subset so that it returns actual results within a few seconds
    instead of returning nothing at all.
    """
    if not handle:
        return []
    try:
        dataset, _ = await asyncio.to_thread(wmn_dataset.ensure_dataset, config.WMN_DATASET_PATH, False, config.FOOTPRINT_TIMEOUT_SECONDS)
        sites = wmn_dataset.select_sites(dataset, deep=deep)
        results = await footprint_scanner._scan(handle, sites, config.FOOTPRINT_CONCURRENCY, config.FOOTPRINT_TIMEOUT_SECONDS, None, config.FOOTPRINT_PER_HOST_CONCURRENCY)
        # Ensure we always return a list
        return results if isinstance(results, list) else []
    except asyncio.TimeoutError:
        _log.warning("Footprint scan timed out for %s", handle)
        return []  # Return empty list, not exception
    except Exception as exc:
        _log.error("Footprint scan failed: %s", exc)
        return []  # Return empty list instead of exception object


async def _skipped_footprint(reason: str = "live_scanning_disabled") -> Dict[str, Any]:
    """The handle sweep as reported when it was never run.

    Returned instead of a bare [] so that the "we never looked" case
    survives all the way to the UI: an empty list normalises to
    STATUS_EMPTY, which is indistinguishable from a handle that was swept
    and came back clean. Callers must not treat this as a clean result --
    there is no result.

    Reached only on the hosted demo build, where live_scanning_enabled()
    is False: that host serves many visitors from one IP, so scanning on
    behalf of a stranger is the thing it must not do. A local install
    always scans, at one depth or the other.
    """
    return {
        "module": "footprint",
        "status": STATUS_SKIPPED,
        "records": [],
        "count": 0,
        "checks": [],
        "probed": 0,
        "skipped_reason": reason,
    }


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


async def run_full_osint_sweep(
    profile_data: Dict[str, Any],
    passive_only: bool = False,
    footprint_enabled: bool = True,
) -> Dict[str, Any]:
    """
    Execute all passive OSINT modules concurrently.

    Two separate footprint knobs, because they used to be one and that
    conflated "scan less" with "do not scan":

    - `passive_only` picks the *depth*. True runs the curated 52-site
      list, False the full 678. Both are real scans returning real hits.
    - `footprint_enabled` is the on/off switch, and False is the only
      thing that produces STATUS_SKIPPED. It exists for the hosted demo
      build alone (runtime_mode.live_scanning_enabled()), where probing
      on behalf of an anonymous visitor from a shared IP is the actual
      hazard. Previously `passive_only` did both jobs, so every local
      install sitting in the default online runtime got no footprint
      results at all -- for a restriction that only ever applied to the
      shared host.
    """
    full_name = profile_data.get("name", "").strip()
    handle = profile_data.get("handle", "").strip()
    domain = profile_data.get("domain", "").strip()
    state = profile_data.get("state", "").strip()
    email = profile_data.get("email", "").strip()
    # FEC contributor search is fuzzy; these let scan_fec confirm that a
    # same-named donor is actually the subject. See utils/osint/fec.py.
    city = (profile_data.get("city") or "").strip()
    zip_code = (profile_data.get("zip_code") or "").strip()

    sec_task = scan_sec(full_name)
    courtlistener_task = scan_courtlistener(full_name)
    fec_task = scan_fec(
        full_name,
        state if state else None,
        city=city or None,
        zip_code=zip_code or None,
    )
    github_task = scan_github(handle)
    infrastructure_task = scan_infrastructure(domain)
    
    # Off only where scanning for a stranger is the hazard (the shared
    # demo host). Everywhere else it runs for real, at the depth
    # passive_only selects. See _skipped_footprint for why the off case
    # is a status rather than an empty list.
    if not footprint_enabled:
        footprint_task = _skipped_footprint()
    else:
        footprint_task = _run_footprint(handle, deep=not passive_only)
        
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

    # Which site list actually ran, recorded next to the findings. The
    # panel says so on screen: "12 findings across 678 platforms" is a
    # result a reader can weigh, "12 findings" is not.
    fp_result["depth"] = "fast" if passive_only else "deep"

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
                "probed": fp_result.get("probed", 0),
                "depth": fp_result.get("depth"),
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
