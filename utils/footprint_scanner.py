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
import ipaddress
import random
import socket
from urllib.parse import urlsplit

import aiohttp

from applog import get_logger

_log = get_logger("footprint_scanner")

CONFIRMED = "CONFIRMED"
POSSIBLE = "POSSIBLE"
NOT_FOUND = "NOT_FOUND"
ERROR = "ERROR"

# Status codes that mean "an edge firewall answered, not the site". The
# account may well exist behind the gate, so these are POSSIBLE rather
# than a miss. 999 is LinkedIn's own non-standard refusal code; 429 is a
# rate limit, which says nothing about whether the account exists.
WAF_STATUS_CODES = frozenset({403, 429, 503, 999})

DEFAULT_CONCURRENCY = 50
DEFAULT_PER_HOST = 4
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


def validate_target_url(url: str) -> str | None:
    """Return a rejection reason for URLs that should never be requested."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
            return "unsafe URL scheme or authority"
        port = parsed.port or 443
    except ValueError:
        return "malformed URL"

    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_reserved,
                ip.is_multicast, ip.is_unspecified)):
            return "target resolves to a private or reserved address"
    return None


def classify(site: dict, status_code: int | None, content: str,
             *, final_url: str = "") -> tuple[str, str]:
    """Sort one response into (verdict, reason).

    Precedence is deliberate and order-sensitive:
      1. no response          -> ERROR
      2. 403/503              -> POSSIBLE (WAF gate; may hide a real account)
      3. m_string present     -> NOT_FOUND (guarded against the empty-string trap)
      4. m_code == status     -> NOT_FOUND (skipped when m_code == e_code)
      5. e_code + e_string    -> CONFIRMED
      6. bare 200             -> NOT_FOUND if page is too short or redirected
                                  to a generic route; POSSIBLE otherwise
      7. anything else        -> NOT_FOUND

    A site that defines no e_string (2 of them) can only ever match on
    status code, which is thin evidence for asserting someone owns an
    account -- those resolve to POSSIBLE, not CONFIRMED.

    ``final_url`` is the URL after following redirects. When the request
    followed a redirect and landed on a generic route (login, signup, 404,
    homepage), the response is a miss, not an ambiguous hit.
    """
    if status_code is None:
        return ERROR, "no response"

    if status_code in WAF_STATUS_CODES:
        return POSSIBLE, f"manual review required — blocked by WAF/CAPTCHA (HTTP {status_code})"

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
        # Redirect landed on a generic page (login, signup, 404 route,
        # homepage) -- that's a miss, not an ambiguous hit.
        if final_url and _is_generic_landing(final_url):
            return NOT_FOUND, f"redirected to generic page ({final_url})"
        # Very short responses almost always mean an empty/error page, not
        # a real profile.  1 KB is well below any real profile page.
        if len(content) < 1024:
            return NOT_FOUND, "HTTP 200 but response too short to contain a profile"
        return POSSIBLE, "HTTP 200 without a definitive match"

    return NOT_FOUND, f"no match (HTTP {status_code})"


# Segments that indicate a generic landing page rather than a real profile.
_GENERIC_SEGMENTS = frozenset({
    "/login", "/signin", "/sign-in", "/signup", "/sign-up", "/register",
    "/404", "/not-found", "/notfound", "/home", "/explore", "/search",
    "/join", "/auth",
})


def _is_generic_landing(url: str) -> bool:
    """True when *url* looks like a login, signup, 404, or homepage --
    destinations that sites redirect to when a profile doesn't exist."""
    try:
        path = urlsplit(url).path.rstrip("/").lower()
    except ValueError:
        return False
    # Exact match or prefix match ("/login/next?..." still counts).
    return any(path == seg or path.startswith(seg + "/") for seg in _GENERIC_SEGMENTS)


def _protection_note(site: dict) -> str:
    protections = site.get("protection") or []
    return ", ".join(protections)


MANUAL_REVIEW_REASON = "manual review required"


def is_manual_review(result: dict) -> bool:
    """True for rows a human has to check by hand because an edge firewall
    answered instead of the platform. The UI labels these differently from
    an ordinary ambiguous response -- 'go look yourself' is a different
    instruction than 'this response was unclear'."""
    return MANUAL_REVIEW_REASON in (result.get("reason") or "")


def _degrade_waf_error(site: dict, verdict: str, reason: str) -> tuple[str, str]:
    """Route any non-definitive answer from a WAF-protected site to
    manual review.

    LinkedIn is the case this exists for, and it is worse than a simple
    block. It answers automated GETs with HTTP 999 or 403 from some
    networks, drops the connection from others, and from a third set
    returns a perfectly ordinary 200 -- an auth wall or interstitial that
    contains no profile and proves nothing. All three are the same fact:
    this platform cannot be resolved automatically. Reporting the 200 as
    an ordinary ambiguous row would imply a future rescan might settle
    it, which it never will.

    NOT_FOUND survives untouched -- a real 404 is a genuine signal that
    the handle isn't there -- as does CONFIRMED, on the rare protected
    site that still returns its own exists-string.
    """
    if verdict in (POSSIBLE, ERROR) and "waf" in _protection_note(site).lower():
        return POSSIBLE, f"{MANUAL_REVIEW_REASON} — not resolvable automatically ({reason})"
    return verdict, reason


async def _check_site(session, site, account, semaphore, timeout):
    result = {
        "platform": site.get("name", "Unknown platform"),
        "category": site.get("cat", ""),
        "profile_url": "",
        "target_identifier": account,
        "protection": _protection_note(site),
    }

    async with semaphore:
        await asyncio.sleep(random.uniform(*JITTER_RANGE_MS) / 1000)
        try:
            request = build_request(site, account)
            result["profile_url"] = request["display_url"]
            rejection = await asyncio.to_thread(validate_target_url, request["url"])
            if rejection:
                verdict, reason = ERROR, rejection
            else:
                async with session.request(
                    request["method"],
                    request["url"],
                    headers={key: value for key, value in request["headers"].items()
                             if key.lower() not in {"host", "content-length"}},
                    data=request["body"],
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=True,
                    max_redirects=5,
                ) as response:
                    content = await response.text(errors="ignore")
                    final_url = str(response.url) if response.url else ""
                    verdict, reason = classify(
                        site, response.status, content, final_url=final_url,
                    )
        except asyncio.TimeoutError:
            verdict, reason = ERROR, "timed out"
        except Exception as exc:
            _log.info("Check failed for %s: %s", site.get("name", "unknown"), exc)
            verdict, reason = ERROR, "request failed"

    verdict, reason = _degrade_waf_error(site, verdict, reason)
    result["confidence"] = verdict
    result["reason"] = reason
    return result


async def _scan(account, sites, concurrency, timeout, on_progress, per_host):
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(
        limit=concurrency, limit_per_host=per_host, ttl_dns_cache=300
    )
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
                 timeout: int = DEFAULT_TIMEOUT, on_progress=None,
                 per_host: int = DEFAULT_PER_HOST) -> list:
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
    return asyncio.run(_scan(account, sites, concurrency, timeout, on_progress, per_host))


def summarize(results: list) -> dict:
    """Counts per verdict, so the UI can say '3 confirmed, 5 to review,
    2 unreachable' instead of just showing a table."""
    summary = {CONFIRMED: 0, POSSIBLE: 0, NOT_FOUND: 0, ERROR: 0}
    for result in results:
        summary[result["confidence"]] = summary.get(result["confidence"], 0) + 1
    return summary


def _extract_social_avatar_url(platform: str, handle: str) -> str:
    """Build avatar URLs for platforms where they're accessible without auth.

    Most social platforms don't expose avatars for unauthenticated lookups,
    but a few have public CDN paths. This list is conservative: only include
    platforms where the avatar is reliably at a predictable URL.
    """
    handle_clean = (handle or "").lower().strip()
    if not handle_clean:
        return ""

    avatar_builders = {
        "X": lambda h: f"https://twitter.com/{h}/photo",
        "Instagram": lambda h: f"https://www.instagram.com/{h}/",
        "GitHub (User)": lambda h: f"https://api.github.com/users/{h}",
        "YouTube Channel": lambda h: f"https://www.youtube.com/@{h}",
    }
    builder = avatar_builders.get(platform)
    return builder(handle_clean) if builder else ""


def discoveries(results: list, confident_only: bool = True) -> list:
    """Just the rows worth showing a human: confirmed hits by default,
    with ambiguous results available when ``confident_only`` is False.
    """
    if not results or not isinstance(results, list):
        return []
    if confident_only:
        keep_verdicts = {CONFIRMED}
    else:
        keep_verdicts = {CONFIRMED, POSSIBLE}

    keep = []
    for r in results:
        if not isinstance(r, dict):
            continue
        if r.get("confidence") in keep_verdicts:
            if "avatar_url" not in r and "target_identifier" in r:
                r["avatar_url"] = _extract_social_avatar_url(r.get("platform", ""), r["target_identifier"])
            keep.append(r)
    order = {CONFIRMED: 0, POSSIBLE: 1}
    return sorted(keep, key=lambda r: (order.get(r.get("confidence"), 2), str(r.get("platform", "")).lower()))


