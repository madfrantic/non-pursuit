"""
Verification sweep -- the background half of the delisting lifecycle.

A demand letter is only half the job; the part that actually closes a case
is going back and confirming the record came down. This module does that
check: it asks whether a broker's profile URL still resolves, and hands the
answer to campaign_manager so a verified removal flips the campaign to
DELISTED with a dated evidence line.

Deliberately dependency-injected rather than calling requests directly. The
test suite blocks all outbound sockets (tests/conftest.py) because a privacy
tool's own test run has no business phoning data brokers, so every function
here takes a `fetcher` and the real HTTP call is only reached when one isn't
supplied.

What counts as gone: a 404/410 is the unambiguous signal, and it's the only
one treated as proof. A 200 means still listed. Anything else -- a timeout,
a 403 from Cloudflare (several brokers in brokers.csv sit behind it), a 5xx
-- is explicitly *inconclusive* rather than "removed". Guessing "removed"
from a blocked request would close a case that is still live, which is the
worst failure this module could have.
"""
import logging
from urllib.parse import urlparse

import campaign_manager
import runtime_mode

_log = logging.getLogger("recon_worker")

# Outcomes of a single endpoint check.
RESULT_STILL_LISTED = "STILL_LISTED"
RESULT_REMOVED = "REMOVED"
RESULT_INCONCLUSIVE = "INCONCLUSIVE"

REMOVED_STATUS_CODES = {404, 410}
LISTED_STATUS_CODES = {200}

DEFAULT_TIMEOUT_SECONDS = 15


def _default_fetcher(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> int | None:
    """Return the HTTP status code for `url`, or None if unreachable.

    Imported lazily so the module stays importable (and testable) in an
    environment without requests, and so nothing network-shaped happens at
    import time.
    """
    import requests

    response = requests.head(
        url, timeout=timeout, allow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NonPursuit/1.0)"},
    )
    return response.status_code


def classify_status_code(status_code: int | None) -> str:
    """Map an HTTP status to a lifecycle outcome. See the module note on
    why only 404/410 are allowed to mean REMOVED."""
    if status_code is None:
        return RESULT_INCONCLUSIVE
    if status_code in REMOVED_STATUS_CODES:
        return RESULT_REMOVED
    if status_code in LISTED_STATUS_CODES:
        return RESULT_STILL_LISTED
    return RESULT_INCONCLUSIVE


def check_endpoint(url: str, fetcher=None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Probe one broker profile URL.

    Never raises on a network failure -- an unreachable broker is an
    ordinary outcome of a sweep, not an error worth aborting the run over,
    and a sweep that dies on the first timeout would leave the rest of the
    campaign list unchecked.
    """
    if not url or not urlparse(url).scheme:
        return {"url": url, "status_code": None, "result": RESULT_INCONCLUSIVE,
                "detail": "No usable profile URL on record"}

    fetch = fetcher or _default_fetcher
    try:
        status_code = fetch(url, timeout)
    except Exception as exc:
        _log.warning("Recon check failed for %s: %s", url, exc)
        return {"url": url, "status_code": None, "result": RESULT_INCONCLUSIVE,
                "detail": f"Request failed: {type(exc).__name__}"}

    result = classify_status_code(status_code)
    return {"url": url, "status_code": status_code, "result": result,
            "detail": f"HTTP {status_code}"}


def build_dork_url(campaign: dict, name: str, location: str = "") -> str:
    """The Google search that shows, by eye, whether this broker still
    lists the person. Surfaced alongside the endpoint check because a
    broker can 404 one profile URL while still serving the record from a
    new one -- the search is what catches that."""
    import google_dork

    domain = campaign.get("domain") or google_dork.domain_from_url(
        campaign.get("profile_url") or ""
    )
    return google_dork.build_broker_dork_url(name, location, domain)


def verify_campaign(db_path: str, campaign: dict, fetcher=None,
                    timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Check one campaign and write the outcome to the ledger.

    Returns the check result dict so a caller (the UI, a cron run) can
    report what happened without re-reading the database.
    """
    campaign_id = campaign["id"]
    outcome = check_endpoint(campaign.get("profile_url"), fetcher=fetcher, timeout=timeout)

    if outcome["result"] == RESULT_REMOVED:
        campaign_manager.record_delisting(
            db_path, campaign_id,
            evidence=f"Recon verified removal: {outcome['detail']} for {outcome['url']}",
        )
    elif outcome["result"] == RESULT_STILL_LISTED:
        campaign_manager.record_recon_check(
            db_path, campaign_id, still_listed=True,
            evidence=f"Recon found record still live: {outcome['detail']}",
        )
    else:
        # Inconclusive still stamps last_recon_check -- the sweep did run,
        # and the UI needs to show when, so a blocked broker doesn't look
        # like one nobody has checked in weeks.
        campaign_manager.record_recon_check(
            db_path, campaign_id, still_listed=True,
            evidence=f"Recon inconclusive: {outcome['detail']}",
        )

    return outcome


def run_sweep(db_path: str, fetcher=None, timeout: int = DEFAULT_TIMEOUT_SECONDS,
              max_checks: int | None = None) -> list[dict]:
    """Verify every active campaign. The 24-hour cron entry point.

    Refuses to run where live scanning is gated off (the hosted demo
    build), for the same reason the footprint scanner is gated: a shared
    host has no business making outbound requests on a visitor's behalf.
    """
    if fetcher is None and not runtime_mode.live_scanning_enabled():
        _log.info("Recon sweep skipped: live scanning disabled in this runtime.")
        return []

    campaigns = campaign_manager.get_active_campaigns(db_path)
    if max_checks is not None:
        campaigns = campaigns[:max_checks]

    results = []
    for campaign in campaigns:
        outcome = verify_campaign(db_path, campaign, fetcher=fetcher, timeout=timeout)
        outcome["campaign_id"] = campaign["id"]
        outcome["broker_name"] = campaign["broker_name"]
        results.append(outcome)
    return results


# The CLI entry point lives in agent_recon.py at the repo root, not here:
# config.py sits at the root, so a module inside utils/ can't be run
# directly as a script without the same sys.path bootstrap app.py does.
