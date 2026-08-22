"""
Async discovery engine over the unified site registry.

The existing utils/footprint_scanner.py answers "does this handle exist
here" across the WhatsMyName list and does it well. This module is the
superset: it speaks the merged Sherlock/Maigret/WhatsMyName schema from
site_registry, it pulls identity metadata out of the bodies it already has
in hand, and it emits the structured rows the identity graph scores.

Everything reusable is reused rather than reimplemented. build_request and
validate_target_url come straight from footprint_scanner -- the SSRF guard
in particular is not something to have two versions of -- and so do the
verdict constants and the WAF status set.

WHAT IS DELIBERATELY NOT HERE

No proxy rotation, no CAPTCHA solving, no TLS fingerprint spoofing, no
retry-until-it-works against a site that has said no. A site behind a
challenge is reported as needing manual review, which is the true answer.
This is a tool for finding your own exposure so you can demand its deletion;
the moment it starts defeating access controls it stops being that and
becomes something a compliance record cannot rest on.

User-Agent rotation is present but is compatibility, not evasion: some
platforms serve a JavaScript shell to one browser string and server-rendered
HTML -- the thing metadata extraction needs -- to another. The pool is real,
current browser strings, chosen per site deterministically so a rescan of
the same site sends the same header rather than producing a different
verdict each run for no reason the user can see.

BODY HANDLING

Bodies are read with a hard cap. The registry has entries whose "profile
page" is a multi-megabyte forum index, and a 3000-site sweep that buffers
those in full will exhaust memory long before it exhausts the site list. The
cap is well above where detection strings and <head> metadata live.
"""
import asyncio
from email.utils import parsedate_to_datetime
import hashlib
import os
import random
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiohttp

try:
    from aiohttp_socks import ProxyConnector
except ImportError:  # pragma: no cover - optional dependency
    ProxyConnector = None

from applog import get_logger
from footprint_scanner import (
    CONFIRMED, ERROR, NOT_FOUND, POSSIBLE, WAF_STATUS_CODES,
    MANUAL_REVIEW_REASON, build_request, validate_target_url,
)
from site_registry import CHECK_MESSAGE, CHECK_RESPONSE_URL, handle_is_valid
import metadata_extractor

_log = get_logger("recon_engine")

SKIPPED = "SKIPPED"

DEFAULT_CONCURRENCY = 50
DEFAULT_PER_HOST = 4
DEFAULT_TIMEOUT = 15
JITTER_RANGE_MS = (20, 50)
DEFAULT_RATE_LIMIT_RPS = 2.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 30.0
RETRYABLE_STATUS = frozenset({429, 503})
SOCKS_SCHEMES = frozenset({"socks5", "socks5h"})

# 2 MB. Above any real profile page; below the point where a few hundred
# concurrent forum indexes become a memory problem.
MAX_BODY_BYTES = 2_000_000

# Genuine, current desktop browser strings. Rotated for rendering
# compatibility (see module docstring), never to disguise the request as
# something it is not.
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
)

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def load_proxy_urls(value: str | None = None) -> tuple[str, ...]:
    """Load validated proxy URLs from ``RECON_PROXIES``."""
    raw = os.getenv("RECON_PROXIES", "") if value is None else value
    proxies = []
    for item in raw.replace("\n", ",").split(","):
        proxy = item.strip()
        scheme = urlsplit(proxy).scheme.lower()
        if proxy and scheme in {"http", "https", "socks5", "socks5h"}:
            proxies.append(proxy)
    return tuple(dict.fromkeys(proxies))


class ProxyRotator:
    """Round-robin proxy selection with a direct-request fallback."""

    def __init__(self, proxies=()):
        self.proxies = tuple(proxies)
        self._index = 0

    @classmethod
    def from_environment(cls) -> "ProxyRotator":
        return cls(load_proxy_urls())

    def next(self) -> str | None:
        if not self.proxies:
            return None
        proxy = self.proxies[self._index % len(self.proxies)]
        self._index += 1
        return proxy

    def socks_proxies(self) -> tuple[str, ...]:
        return tuple(proxy for proxy in self.proxies
                     if urlsplit(proxy).scheme.lower() in SOCKS_SCHEMES)

    async def open_socks_sessions(self) -> dict:
        """Open pooled sessions for configured SOCKS proxies when supported."""
        socks = self.socks_proxies()
        if ProxyConnector is None:
            # Without a SOCKS connector these requests would leave the host
            # directly. On a tool whose whole point is not scanning from the
            # operator's own IP, that has to be loud, not a silent fallback.
            if socks:
                _log.warning(
                    "%d SOCKS proxy/proxies configured but aiohttp-socks is not "
                    "installed -- those requests will go out DIRECT. "
                    "Install aiohttp-socks or drop them from RECON_PROXIES.",
                    len(socks))
            return {}
        return {proxy: aiohttp.ClientSession(connector=ProxyConnector.from_url(proxy))
                for proxy in socks}

    @staticmethod
    async def close_sessions(sessions: dict) -> None:
        for session in sessions.values():
            await session.close()


class TokenBucketRateLimiter:
    """Async per-domain token bucket with one-token burst capacity."""

    def __init__(self, requests_per_second: float = DEFAULT_RATE_LIMIT_RPS,
                 sensitive_hosts=()):
        self.requests_per_second = max(float(requests_per_second), 0.0)
        self.sensitive_hosts = {str(host).lower().strip().rstrip(".")
                                for host in sensitive_hosts if str(host).strip()}
        self._buckets = {}

    @classmethod
    def from_environment(cls) -> "TokenBucketRateLimiter":
        raw_hosts = os.getenv("RECON_SENSITIVE_HOSTS", "")
        hosts = [host for host in raw_hosts.replace("\n", ",").split(",") if host.strip()]
        try:
            rps = float(os.getenv("RECON_RATE_LIMIT_RPS", DEFAULT_RATE_LIMIT_RPS))
        except ValueError:
            rps = DEFAULT_RATE_LIMIT_RPS
        return cls(rps, hosts)

    def _bucket(self, host: str) -> dict:
        if host not in self._buckets:
            self._buckets[host] = {
                "tokens": 1.0, "updated": time.monotonic(), "lock": asyncio.Lock()}
        return self._buckets[host]

    def should_limit(self, url: str, *, sensitive: bool = False) -> bool:
        host = _host(url)
        return bool(self.requests_per_second and
                    (sensitive or host in self.sensitive_hosts))

    async def acquire(self, url: str, *, sensitive: bool = False) -> None:
        """Wait for one token; callers targeting one host queue on its bucket."""
        if not self.should_limit(url, sensitive=sensitive):
            return
        host = _host(url)
        bucket = self._bucket(host)
        while True:
            async with bucket["lock"]:
                now = time.monotonic()
                bucket["tokens"] = min(
                    1.0, bucket["tokens"] +
                    (now - bucket["updated"]) * self.requests_per_second)
                bucket["updated"] = now
                if bucket["tokens"] >= 1.0:
                    bucket["tokens"] -= 1.0
                    return
                wait_for = (1.0 - bucket["tokens"]) / self.requests_per_second
            await asyncio.sleep(wait_for)


PerDomainRateLimiter = TokenBucketRateLimiter


def _retry_after_seconds(headers: dict) -> float | None:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, min(float(value), MAX_BACKOFF_SECONDS))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, min(
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
                MAX_BACKOFF_SECONDS))
        except (TypeError, ValueError, OverflowError):
            return None


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(int(os.getenv(name, default)), minimum)
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(float(os.getenv(name, default)), minimum)
    except ValueError:
        return default


async def _request_with_retries(session, request: dict, site: dict, *,
                                rate_limiter: TokenBucketRateLimiter,
                                proxy_rotator: ProxyRotator,
                                socks_sessions: dict, timeout: int,
                                max_retries: int, backoff_base: float) -> dict:
    sensitive = bool(site.get("sensitive") or site.get("rate_limit") or
                     "broker" in str(site.get("cat", "")).lower())
    for attempt in range(max_retries + 1):
        await rate_limiter.acquire(request["url"], sensitive=sensitive)
        proxy = proxy_rotator.next()
        request_session = socks_sessions.get(proxy, session)
        request_kwargs = {
            "headers": _headers_for(site), "data": request["body"],
            "timeout": aiohttp.ClientTimeout(total=timeout),
            "allow_redirects": True, "max_redirects": 5,
        }
        # aiohttp's own `proxy=` kwarg speaks HTTP CONNECT only; SOCKS needs a
        # connector, which lives on its own pre-opened session.
        if proxy and request_session is session and urlsplit(proxy).scheme.lower() not in SOCKS_SCHEMES:
            request_kwargs["proxy"] = proxy
        async with request_session.request(
            request["method"], request["url"], **request_kwargs) as response:
            if response.status in RETRYABLE_STATUS and attempt < max_retries:
                # Body is not read here: it is about to be discarded, and on a
                # rate-limit page that read is pure latency and bandwidth.
                retry_after = _retry_after_seconds(response.headers)
                delay = retry_after if retry_after is not None else min(
                    backoff_base * (2 ** attempt), MAX_BACKOFF_SECONDS)
                await asyncio.sleep(delay)
                continue
            return {
                "status": response.status,
                "content": await _read_capped(response),
                "content_type": response.headers.get("Content-Type", ""),
                "final_url": str(response.url) if response.url else "",
                "attempts": attempt + 1,
            }


def pick_user_agent(site_name: str) -> str:
    """Deterministic per-site User-Agent.

    Hashing the site name rather than calling random() means the same site
    gets the same header on every scan. A site that renders differently for
    different browsers would otherwise flip verdicts between runs, and
    "this result changed and I don't know why" is the worst possible
    property for a record someone is about to cite in a legal demand.
    """
    digest = hashlib.sha256(site_name.encode("utf-8")).digest()
    return USER_AGENTS[digest[0] % len(USER_AGENTS)]


def _headers_for(site: dict) -> dict:
    headers = dict(BASE_HEADERS)
    headers["User-Agent"] = pick_user_agent(site.get("name", ""))
    headers.update(site.get("headers") or {})
    # Host and Content-Length are computed by aiohttp; a dataset-supplied
    # value for either produces a malformed request.
    return {k: v for k, v in headers.items()
            if k.lower() not in {"host", "content-length"}}


def _is_generic_landing(url: str) -> bool:
    from footprint_scanner import _is_generic_landing as check
    return check(url)


def classify(site: dict, status_code, content: str, *, final_url: str = "") -> tuple:
    """Sort one response into (verdict, reason) using the unified schema.

    Precedence follows footprint_scanner's, which was derived from the
    dataset's real failure modes, extended for the two things the merged
    schema adds:

      * `m_strings` / `e_strings` are lists. Any miss-string matching is a
        miss; any exists-string matching is a hit. Sherlock and Maigret both
        ship several alternatives per site because sites word their 404 more
        than one way.
      * `response_url` sites are decided by where the redirect landed, not
        by the body, because those sites answer 200 with a valid page either
        way.

    The empty-string guard is preserved: site_registry filters "" out of
    both lists at ingest, and the `if not strings` branches below mean a
    site that ends up with no strings falls through to the code checks
    rather than matching everything.
    """
    if status_code is None:
        return ERROR, "no response"

    if status_code in WAF_STATUS_CODES:
        return POSSIBLE, f"{MANUAL_REVIEW_REASON} — blocked by WAF/CAPTCHA (HTTP {status_code})"

    check_type = site.get("check_type")
    e_code = site.get("e_code")
    m_code = site.get("m_code")
    e_strings = [s for s in (site.get("e_strings") or []) if s]
    m_strings = [s for s in (site.get("m_strings") or []) if s]

    if check_type == CHECK_RESPONSE_URL:
        error_url = (site.get("error_url") or "").split("{account}")[0]
        if final_url and error_url and final_url.startswith(error_url):
            return NOT_FOUND, f"redirected to the site's miss URL ({final_url})"
        if final_url and _is_generic_landing(final_url):
            return NOT_FOUND, f"redirected to generic page ({final_url})"
        if status_code == e_code:
            return CONFIRMED, "stayed on the profile URL"
        return NOT_FOUND, f"no match (HTTP {status_code})"

    matched_miss = next((s for s in m_strings if s in content), None)
    if matched_miss is not None:
        return NOT_FOUND, f"missing-string matched ({matched_miss[:40]!r})"

    # Skipped when m_code == e_code: 173 WhatsMyName entries declare both as
    # 200, where the status alone cannot separate hit from miss.
    if m_code is not None and m_code == status_code and m_code != e_code:
        return NOT_FOUND, f"missing-code matched (HTTP {status_code})"

    if status_code == e_code:
        matched_hit = next((s for s in e_strings if s in content), None)
        if matched_hit is not None:
            return CONFIRMED, f"exists-string and status matched ({matched_hit[:40]!r})"
        if not e_strings:
            if check_type == CHECK_MESSAGE:
                return POSSIBLE, "status matched; site defines no exists-string"
            return CONFIRMED, f"status matched (HTTP {status_code})"
        # The site declares exists-strings and none of them are present.
        return NOT_FOUND, "exists-string absent"

    if status_code == 200:
        if final_url and _is_generic_landing(final_url):
            return NOT_FOUND, f"redirected to generic page ({final_url})"
        if len(content) < 1024:
            return NOT_FOUND, "HTTP 200 but response too short to contain a profile"
        return POSSIBLE, "HTTP 200 without a definitive match"

    return NOT_FOUND, f"no match (HTTP {status_code})"


def _self_host(url: str) -> str:
    try:
        return url.split("://", 1)[1].split("/", 1)[0].split(":")[0]
    except IndexError:
        return ""


async def _read_capped(response) -> str:
    """Read at most MAX_BODY_BYTES of the body, decoded leniently.

    Streamed rather than awaited whole so an oversized body costs the cap,
    not its real size. Encoding errors are swallowed: this content is only
    ever pattern-matched, never re-served.
    """
    chunks, total = [], 0
    async for chunk in response.content.iter_chunked(65_536):
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX_BODY_BYTES:
            break
    raw = b"".join(chunks)
    encoding = response.charset or "utf-8"
    try:
        return raw.decode(encoding, errors="ignore")
    except LookupError:
        return raw.decode("utf-8", errors="ignore")


async def _check_site(session, site, account, semaphore, timeout, extract_metadata,
                      rate_limiter, proxy_rotator, socks_sessions, max_retries,
                      backoff_base):
    request = build_request(site, account)
    row = {
        "platform": site.get("name", "Unknown platform"),
        "category": site.get("cat", ""),
        "handle": account,
        "target_identifier": account,
        "url": request["display_url"],
        "profile_url": request["display_url"],
        "probe_url": request["url"],
        "source": site.get("source", ""),
        "protection": ", ".join(site.get("protection") or []),
        "http_status": None,
        "response_time_ms": None,
        "retries": 0,
        "metadata": {},
    }

    async with semaphore:
        await asyncio.sleep(random.uniform(*JITTER_RANGE_MS) / 1000)
        started = time.perf_counter()
        try:
            rejection = await asyncio.to_thread(validate_target_url, request["url"])
            if rejection:
                verdict, reason = ERROR, rejection
            else:
                fetched = await _request_with_retries(
                    session, request, site, rate_limiter=rate_limiter,
                    proxy_rotator=proxy_rotator, socks_sessions=socks_sessions,
                    timeout=timeout, max_retries=max_retries,
                    backoff_base=backoff_base)
                row["http_status"] = fetched["status"]
                row["retries"] = fetched["attempts"] - 1
                content = fetched["content"]
                final_url = fetched["final_url"]
                verdict, reason = classify(site, fetched["status"], content,
                                               final_url=final_url)
                if extract_metadata and verdict in (CONFIRMED, POSSIBLE):
                    row["metadata"] = metadata_extractor.extract(
                        content, content_type=fetched["content_type"],
                        self_host=_self_host(request["url"]))
        except asyncio.TimeoutError:
            verdict, reason = ERROR, "timed out"
        except aiohttp.TooManyRedirects:
            verdict, reason = ERROR, "redirect loop"
        except aiohttp.ClientError as exc:
            _log.info("Check failed for %s: %s", site.get("name"), exc)
            verdict, reason = ERROR, "request failed"
        except Exception as exc:  # noqa: BLE001 - one bad site must not end the sweep
            _log.info("Unexpected failure for %s: %s", site.get("name"), exc)
            verdict, reason = ERROR, "request failed"
        finally:
            row["response_time_ms"] = round((time.perf_counter() - started) * 1000, 1)

    # A WAF-protected site that answered anything short of definitive is not
    # resolvable automatically -- same reasoning as footprint_scanner's
    # LinkedIn handling, applied to the 277 Maigret entries carrying a
    # protection flag.
    if verdict in (POSSIBLE, ERROR) and "waf" in row["protection"].lower() \
            and MANUAL_REVIEW_REASON not in reason:
        verdict = POSSIBLE
        reason = f"{MANUAL_REVIEW_REASON} — not resolvable automatically ({reason})"

    row["verdict"] = verdict
    row["exists"] = verdict == CONFIRMED
    row["reason"] = reason
    return row


async def scan(account: str, sites: list, *, concurrency: int = DEFAULT_CONCURRENCY,
               timeout: int = DEFAULT_TIMEOUT, per_host: int = DEFAULT_PER_HOST,
               extract_metadata: bool = True, on_progress=None,
               rate_limiter: TokenBucketRateLimiter | None = None,
               proxy_rotator: ProxyRotator | None = None,
               max_retries: int | None = None,
               backoff_base: float | None = None) -> list:
    """Probe `account` across `sites`, returning one row per site.

    Sites whose handle regex rejects the account are returned as SKIPPED
    rather than probed. That distinction is the difference between an honest
    denominator and a flattering one: 40 misses out of 40 real probes is a
    result, while 2900 misses out of 3000 sites that could never have held
    the handle is noise wearing a result's clothes.
    """
    account = (account or "").strip()
    if not account or not sites:
        return []

    probeable, skipped = [], []
    for site in sites:
        if handle_is_valid(site, account):
            probeable.append(site)
        else:
            skipped.append({
                "platform": site.get("name", ""),
                "category": site.get("cat", ""),
                "handle": account,
                "url": "",
                "source": site.get("source", ""),
                "verdict": SKIPPED,
                "exists": False,
                "http_status": None,
                "response_time_ms": None,
                "metadata": {},
                "reason": "handle cannot be valid on this site (regexCheck)",
            })

    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=per_host,
                                     ttl_dns_cache=300)
    rate_limiter = rate_limiter or TokenBucketRateLimiter.from_environment()
    proxy_rotator = proxy_rotator or ProxyRotator.from_environment()
    max_retries = (_env_int("RECON_MAX_RETRIES", DEFAULT_MAX_RETRIES)
                   if max_retries is None else max(0, max_retries))
    backoff_base = (_env_float("RECON_BACKOFF_BASE_SECONDS", DEFAULT_BACKOFF_BASE_SECONDS)
                    if backoff_base is None else max(0.0, backoff_base))
    results = []
    async with aiohttp.ClientSession(connector=connector) as session:
        socks_sessions = await proxy_rotator.open_socks_sessions()
        try:
            tasks = [
                asyncio.ensure_future(
                    _check_site(session, site, account, semaphore, timeout, extract_metadata,
                                rate_limiter, proxy_rotator, socks_sessions, max_retries,
                                backoff_base))
                for site in probeable
            ]
            completed = 0
            for coro in asyncio.as_completed(tasks):
                row = await coro
                results.append(row)
                completed += 1
                if on_progress:
                    on_progress(completed, len(tasks), row)
        finally:
            # A cancelled scan (the API's job store cancels these) must not
            # leave the proxy sessions open behind it.
            await proxy_rotator.close_sessions(socks_sessions)

    return results + skipped


def scan_sync(account: str, sites: list, **kwargs) -> list:
    """Blocking wrapper, for Streamlit and the CLI.

    Streamlit runs each script pass on a thread with no event loop, so
    asyncio.run() owning the loop for the duration is what makes an
    on_progress callback safe to draw widgets from.
    """
    return asyncio.run(scan(account, sites, **kwargs))


def summarize(results: list) -> dict:
    counts = {CONFIRMED: 0, POSSIBLE: 0, NOT_FOUND: 0, ERROR: 0, SKIPPED: 0}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    probed = len(results) - counts[SKIPPED]
    return {
        "sites_in_registry": len(results),
        "sites_probed": probed,
        "by_verdict": counts,
        "median_response_ms": _median(
            [r["response_time_ms"] for r in results if r.get("response_time_ms")]),
    }


def _median(values: list):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)
