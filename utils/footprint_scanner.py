"""
Async account-existence scanner over the WhatsMyName dataset.

Checks a handle against N platforms concurrently and sorts each response
into a three-tier confidence verdict. Everything here is local: HTTP
straight from this machine to the platform, no API keys, no third-party
service, nothing about the handle leaves except the requests that have to
be made to answer the question.

Why three tiers instead of WhatsMyName's binary found/not-found: real
responses are messier than the dataset's model. A Cloudflare challenge, a
JavaScript-rendered profile page, and a genuine miss can all come back as
"no match", and collapsing those into NOT_FOUND hides accounts behind a
WAF. POSSIBLE exists so the user reviews those instead of never seeing
them.

Two traps in the dataset that a naive port gets wrong, both load-bearing
enough to be worth naming:

  * 34 sites define m_string as "". `"" in content` is always True, so
    testing it unguarded marks those sites NOT_FOUND on every single
    scan -- a permanent false negative that looks like a clean result.
  * 173 sites have m_code == e_code (usually both 200), where the status
    code alone can't distinguish hit from miss. The missing-code rule has
    to be skipped for those, leaving m_string as the only negative
    signal, or every one of them reads as NOT_FOUND.

TLS verification is deliberately left on, unlike the tool this borrows
from. A handful of sites with broken certificate chains will surface as
ERROR, which is the honest outcome -- a tool built around privacy
shouldn't quietly accept unverified connections to make its numbers look
better.
"""
import asyncio
import random

import aiohttp

from applog import get_logger

_log = get_logger("footprint_scanner")

CONFIRMED = "CONFIRMED"
POSSIBLE = "POSSIBLE"
NOT_FOUND = "NOT_FOUND"
ERROR = "ERROR"

# Status codes that mean "an edge firewall answered, not the site". The
# account may well exist behind the gate, so these are POSSIBLE rather
# than a miss.
WAF_STATUS_CODES = frozenset({403, 503})

DEFAULT_CONCURRENCY = 15
DEFAULT_TIMEOUT = 15
# Small random pause before each request. Not stealth -- it just keeps a
# 700-site sweep from arriving as one synchronised burst that edge
# firewalls treat as an attack.
JITTER_RANGE_MS = (20, 50)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def build_request(site: dict, account: str) -> dict:
    """Turn a dataset entry plus a handle into everything needed to make
    the request. Honors the dataset's optional fields: strip_bad_char
    (chars a platform forbids in handles), headers, post_body (23 sites
    are POST-only), and uri_pretty (a human-facing URL that differs from
    the one actually probed -- Docker Hub checks an API endpoint but
    should link the profile page)."""
    cleaned = account
    for bad_char in site.get("strip_bad_char") or "":
        cleaned = cleaned.replace(bad_char, "")

    check_url = site["uri_check"].replace("{account}", cleaned)
    pretty = site.get("uri_pretty")
    display_url = pretty.replace("{account}", cleaned) if pretty else check_url

    headers = {"User-Agent": USER_AGENT}
    headers.update(site.get("headers") or {})

    body = site.get("post_body")
    if body:
        body = body.replace("{account}", cleaned)

    return {
        "method": "POST" if body else "GET",
        "url": check_url,
        "display_url": display_url,
        "headers": headers,
        "body": body,
    }


def classify(site: dict, status_code: int | None, content: str) -> tuple[str, str]:
    """Sort one response into (verdict, reason).

    Precedence is deliberate and order-sensitive:
      1. no response          -> ERROR
      2. 403/503              -> POSSIBLE (WAF gate; may hide a real account)
      3. m_string present     -> NOT_FOUND (guarded against the empty-string trap)
      4. m_code == status     -> NOT_FOUND (skipped when m_code == e_code)
      5. e_code + e_string    -> CONFIRMED
      6. bare 200             -> POSSIBLE (soft 200 / JS-rendered page)
      7. anything else        -> NOT_FOUND

    A site that defines no e_string (2 of them) can only ever match on
    status code, which is thin evidence for asserting someone owns an
    account -- those resolve to POSSIBLE, not CONFIRMED.
    """
    if status_code is None:
        return ERROR, "no response"

    if status_code in WAF_STATUS_CODES:
        return POSSIBLE, f"blocked by WAF/CAPTCHA (HTTP {status_code})"

    e_code = site.get("e_code")
    m_code = site.get("m_code")
    e_string = site.get("e_string") or ""
    m_string = site.get("m_string") or ""

    if m_string and m_string in content:
        return NOT_FOUND, "missing-string matched"

    if m_code is not None and m_code == status_code and m_code != e_code:
        return NOT_FOUND, f"missing-code matched (HTTP {status_code})"

    if status_code == e_code:
        if e_string and e_string in content:
            return CONFIRMED, "exists-string and status matched"
        if not e_string:
            return POSSIBLE, "status matched; site defines no exists-string"

    if status_code == 200:
        return POSSIBLE, "HTTP 200 without a definitive match"

    return NOT_FOUND, f"no match (HTTP {status_code})"


def _protection_note(site: dict) -> str:
    protections = site.get("protection") or []
    return ", ".join(protections)


async def _check_site(session, site, account, semaphore, timeout):
    request = build_request(site, account)
    result = {
        "platform": site["name"],
        "category": site.get("cat", ""),
        "profile_url": request["display_url"],
        "target_identifier": account,
        "protection": _protection_note(site),
    }

    async with semaphore:
        await asyncio.sleep(random.uniform(*JITTER_RANGE_MS) / 1000)
        try:
            async with session.request(
                request["method"],
                request["url"],
                headers=request["headers"],
                data=request["body"],
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
            ) as response:
                content = await response.text(errors="ignore")
                verdict, reason = classify(site, response.status, content)
        except asyncio.TimeoutError:
            verdict, reason = ERROR, "timed out"
        except Exception as exc:
            _log.info("Check failed for %s: %s", site["name"], exc)
            verdict, reason = ERROR, "request failed"

    result["confidence"] = verdict
    result["reason"] = reason
    return result


async def _scan(account, sites, concurrency, timeout, on_progress):
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, ttl_dns_cache=300)
    results = []

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            asyncio.ensure_future(_check_site(session, site, account, semaphore, timeout))
            for site in sites
        ]
        completed = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            completed += 1
            if on_progress:
                on_progress(completed, len(tasks), result)

    return results


def scan_account(account: str, sites: list, concurrency: int = DEFAULT_CONCURRENCY,
                 timeout: int = DEFAULT_TIMEOUT, on_progress=None) -> list:
    """Scan one handle across `sites` and return every result, including
    misses -- filtering is the caller's decision, and the miss/error
    counts are what make a clean scan distinguishable from a broken one.

    Synchronous on purpose: Streamlit runs each script pass on its own
    thread with no event loop, so asyncio.run() owns the loop for the
    duration and on_progress fires on that same thread, which is what
    makes it safe to draw Streamlit widgets from the callback.
    """
    account = (account or "").strip()
    if not account or not sites:
        return []
    return asyncio.run(_scan(account, sites, concurrency, timeout, on_progress))


def summarize(results: list) -> dict:
    """Counts per verdict, so the UI can say '3 confirmed, 5 to review,
    2 unreachable' instead of just showing a table."""
    summary = {CONFIRMED: 0, POSSIBLE: 0, NOT_FOUND: 0, ERROR: 0}
    for result in results:
        summary[result["confidence"]] = summary.get(result["confidence"], 0) + 1
    return summary


def discoveries(results: list) -> list:
    """Just the rows worth showing a human: confirmed hits first, then
    the ones needing review. NOT_FOUND and ERROR are dropped."""
    keep = [r for r in results if r["confidence"] in (CONFIRMED, POSSIBLE)]
    order = {CONFIRMED: 0, POSSIBLE: 1}
    return sorted(keep, key=lambda r: (order[r["confidence"]], r["platform"].lower()))
