"""
Find where to serve a deletion demand for a given platform.

The remediation step needs one thing the discovery step never produces: an
address. A confirmed account on some forum is not actionable until somebody
knows whether that forum has a privacy@ mailbox, a DSAR portal, or nothing
at all. With ~3000 platforms in the registry, hand-maintaining that mapping
is not a plan.

So contacts resolve through a three-link chain, and every result records
which link produced it:

  1. A curated file (data/platform_privacy_contacts.json), human-verified,
     with a verification date per entry. Highest trust, smallest coverage.
  2. Runtime discovery against the platform's own domain -- security.txt
     per RFC 9116, then the usual privacy-policy paths. Medium trust, and
     the trust is in the *method*: a Contact: line in a file the operator
     published at a standardised location is not a guess.
  3. Nothing. Reported as nothing.

WHY THERE IS NO FOURTH LINK

The tempting one is to construct privacy@<domain> and move on. It is wrong
and it is worse than useless: a constructed address is indistinguishable in
the output from a verified one, so a user would post a statutory demand,
start a 45-day clock against it, and record a non-response from a mailbox
that never existed. Under CCPA the response window only means anything if
the request was actually delivered. An unresolved contact is a smaller
problem than a fabricated one, and it is a problem the user can see.

Discovery is cached per host for the lifetime of a run: 40 confirmed
accounts on 40 platforms is 40 domains, and re-fetching security.txt for
each account on the same domain is pure waste.

CHANNEL CLASSIFICATION AND ESCALATION ORDER

A resolved contact is not just "an email" or "a URL" -- it is one of five
kinds, and the kind determines whether a statutory demand should go there
at all:

  1. dpo_email          -- a Data Protection Officer mailbox (dpo@,
                            datenschutz@, ...). The strongest channel: a
                            DPO is a role GDPR Art. 37 defines.
  2. privacy_email       -- a general privacy/legal/compliance mailbox.
  3. dsar_portal         -- a web form for rights requests.
  4. abuse_fallback      -- the RDAP/WHOIS abuse contact for the domain,
                            used only when nothing above resolved anything.
                            It reaches the registrant, not a privacy team,
                            so it is a last resort, not a first choice.
  5. vulnerability_bounty -- a security-vulnerability channel: a bug-bounty
                            platform (HackerOne, Bugcrowd, Synack,
                            Intigriti) or a bare security.txt contact that
                            does not otherwise read as a privacy mailbox.
                            This is surfaced rather than discarded -- the
                            user should see that *something* was found --
                            but it is never treated as sendable. A CCPA
                            deletion demand filed through a vulnerability-
                            disclosure program is not a demand anyone there
                            is positioned to act on.

best_channel() picks one candidate using that order (1 > 2 > 3 > 4 > 5) and
reports whether the result is fit to send. The RDAP abuse fallback is why
this module now imports infra_checker: discover() calls
infra_checker.lookup_registration() only after security.txt and the privacy
pages have both come back empty, which is the escalation order this
function exists to guarantee -- not a parallel path that could race the
privacy-page checks and occasionally win by being faster.
"""
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from applog import get_logger
from footprint_scanner import validate_target_url
from recon_engine import BASE_HEADERS, pick_user_agent
import infra_checker

_log = get_logger("privacy_contacts")

CONTACTS_JSON = "data/platform_privacy_contacts.json"

SOURCE_CURATED = "curated"
SOURCE_SECURITY_TXT = "security.txt"
SOURCE_PRIVACY_PAGE = "privacy_page"
SOURCE_ABUSE_FALLBACK = "abuse_fallback"
SOURCE_NONE = "unresolved"

# Trust ordering for how a contact was *found*. Kept distinct from the
# delivery-channel priority below, which is about where a demand should be
# *sent* -- a curated entry that only yields a web form still outranks any
# amount of discovery, but a discovered DPO mailbox still beats a discovered
# web form.
SOURCE_TRUST = {
    SOURCE_CURATED: 4,
    SOURCE_SECURITY_TXT: 3,
    SOURCE_PRIVACY_PAGE: 2,
    SOURCE_ABUSE_FALLBACK: 1,
    SOURCE_NONE: 0,
}

# Delivery channels, in escalation order (highest priority first). See the
# module docstring for what each one means and why the order is what it is.
CHANNEL_DPO_EMAIL = "dpo_email"
CHANNEL_PRIVACY_EMAIL = "privacy_email"
CHANNEL_DSAR_PORTAL = "dsar_portal"
CHANNEL_ABUSE_FALLBACK = "abuse_fallback"
CHANNEL_VULNERABILITY_BOUNTY = "vulnerability_bounty"
CHANNEL_NONE = "none"

_CHANNEL_PRIORITY = {
    CHANNEL_DPO_EMAIL: 5,
    CHANNEL_PRIVACY_EMAIL: 4,
    CHANNEL_DSAR_PORTAL: 3,
    CHANNEL_ABUSE_FALLBACK: 2,
    CHANNEL_VULNERABILITY_BOUNTY: 1,
    CHANNEL_NONE: 0,
}

# Channels a statutory demand can actually be sent through. Bounty platforms
# are excluded on purpose -- see CHANNEL_VULNERABILITY_BOUNTY above.
_SENDABLE_CHANNELS = frozenset({
    CHANNEL_DPO_EMAIL, CHANNEL_PRIVACY_EMAIL, CHANNEL_DSAR_PORTAL,
    CHANNEL_ABUSE_FALLBACK,
})

# Bug-bounty platforms named in the spec. A Contact: line in security.txt
# pointing at one of these is a vulnerability-disclosure program, not a
# privacy contact, no matter how confidently it resolves.
_BOUNTY_HOSTS = frozenset({
    "hackerone.com", "bugcrowd.com", "synack.com", "intigriti.com",
})

# RFC 9116 puts security.txt at /.well-known/; the bare path is the legacy
# location and is still what a surprising number of sites serve.
SECURITY_TXT_PATHS = ("/.well-known/security.txt", "/security.txt")
PRIVACY_PATHS = (
    "/privacy", "/privacy-policy", "/legal/privacy", "/privacy.html",
    "/policies/privacy", "/about/privacy",
)

DISCOVERY_TIMEOUT = 10
DISCOVERY_BODY_CAP = 400_000

# Local parts that indicate a mailbox meant for exactly this kind of
# request. Ordered strongest first -- a dpo@ address is a Data Protection
# Officer, which is a statutory role, not a generic inbox. _DPO_LOCALPARTS
# is the subset that earns the higher CHANNEL_DPO_EMAIL classification;
# the rest of _PRIVACY_LOCALPARTS still counts as a privacy mailbox, just
# not a statutory role.
_DPO_LOCALPARTS = ("dpo", "datenschutz", "dataprotection", "data-protection")
_PRIVACY_LOCALPARTS = (
    *_DPO_LOCALPARTS, "privacy", "gdpr", "ccpa", "dsar", "privacyoffice", "legal",
)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}")
_SECURITY_CONTACT_RE = re.compile(r"^Contact:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
# Links whose text or href advertises a rights-request form. These are worth
# more than an email for platforms that only accept requests through a portal.
_DSAR_LINK_RE = re.compile(
    r'href=["\'](https?://[^"\']{5,300}?(?:privacy[-_]?request|data[-_]?request|'
    r'dsar|subject[-_]?access|delete[-_]?(?:my[-_]?)?(?:data|account)|'
    r'ccpa|gdpr|opt[-_]?out)[^"\']{0,100})["\']',
    re.IGNORECASE,
)


def load_curated(path: str = CONTACTS_JSON) -> dict:
    """Curated contacts keyed by lowercase platform name."""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("Unreadable curated contacts at %s: %s", path, exc)
        return {}
    return {str(k).lower(): v for k, v in (payload.get("platforms") or {}).items()}


def _rank_emails(candidates) -> list:
    """Order addresses by how likely they are to be the right mailbox."""
    scored = []
    for email in candidates:
        local = email.split("@", 1)[0].lower().replace(".", "")
        rank = next((len(_PRIVACY_LOCALPARTS) - i
                     for i, part in enumerate(_PRIVACY_LOCALPARTS) if part in local), 0)
        if rank:
            scored.append((rank, email))
    return [email for _, email in sorted(scored, key=lambda pair: -pair[0])]


def parse_security_txt(body: str) -> dict:
    """Contacts from an RFC 9116 security.txt.

    security.txt is a vulnerability-reporting channel, not a privacy one, so
    an address found here is recorded as a *reachable* contact rather than a
    privacy contact. It is a real, operator-published address at a
    standardised location -- which is exactly the property a fabricated one
    lacks -- and for a small forum with no privacy page it is frequently the
    only way to reach a human at all.
    """
    emails, urls = [], []
    for value in _SECURITY_CONTACT_RE.findall(body or ""):
        value = value.strip().rstrip(",;")
        if value.lower().startswith("mailto:"):
            emails.append(value[7:])
        elif "@" in value and "://" not in value:
            emails.append(value)
        elif value.lower().startswith(("http://", "https://")):
            urls.append(value)
    return {"emails": emails, "urls": urls}


def parse_privacy_page(body: str) -> dict:
    """Privacy addresses and rights-request links from a policy page."""
    window = (body or "")[:DISCOVERY_BODY_CAP]
    emails = _rank_emails(dict.fromkeys(_EMAIL_RE.findall(window)))
    urls = list(dict.fromkeys(_DSAR_LINK_RE.findall(window)))[:5]
    return {"emails": emails[:5], "urls": urls}


def _bounty_host(value: str) -> str:
    """The host a bounty-platform check should look at.

    An email's host is what follows the @; a URL's host is host_of(). Same
    helper for both because a Contact: line in security.txt can be either.
    """
    value = (value or "").strip().lower()
    if "@" in value and "://" not in value:
        return value.rsplit("@", 1)[-1]
    return host_of(value)


def is_bounty_platform(value: str) -> bool:
    """True when `value` (an email or a URL) resolves to a named bug-bounty
    platform rather than the operator's own domain."""
    host = _bounty_host(value)
    return any(host == platform or host.endswith("." + platform)
               for platform in _BOUNTY_HOSTS)


def _local_part(email: str) -> str:
    return email.split("@", 1)[0].lower().replace(".", "").replace("_", "")


def _is_dpo_localpart(email: str) -> bool:
    local = _local_part(email)
    return any(part.replace("-", "") in local for part in _DPO_LOCALPARTS)


def _is_privacy_localpart(email: str) -> bool:
    local = _local_part(email)
    return any(part.replace("-", "") in local for part in _PRIVACY_LOCALPARTS)


def classify_contact(source: str, emails, urls) -> list:
    """Every (channel, target) a resolved contact offers, unordered.

    Source-aware because the same-looking address means different things
    depending on where it came from:

      * SOURCE_ABUSE_FALLBACK entries are RDAP/WHOIS abuse mailboxes by
        construction -- classified as such regardless of local part, never
        re-derived as if they were a privacy address.
      * SOURCE_SECURITY_TXT entries default to CHANNEL_VULNERABILITY_BOUNTY
        unless the local part itself reads as a privacy mailbox. RFC 9116's
        Contact: field is a vulnerability-reporting channel; an address
        found there earns a stronger classification only by explicitly
        looking like one (dpo@, privacy@, ...), never by default.
      * Everything else (curated entries, privacy-page hits) is a mailbox
        or form the platform published for exactly this purpose, so it
        defaults to a privacy channel rather than needing to prove itself.

    A bounty-platform host always wins the vulnerability_bounty label
    first, regardless of source -- a curated entry could theoretically be
    stale and point at a HackerOne program, and that should never be
    reported as a privacy_email just because a human typed it into the
    curated file.
    """
    candidates = []

    for email in emails or []:
        if is_bounty_platform(email):
            candidates.append((CHANNEL_VULNERABILITY_BOUNTY, email))
        elif source == SOURCE_ABUSE_FALLBACK:
            candidates.append((CHANNEL_ABUSE_FALLBACK, email))
        elif source == SOURCE_SECURITY_TXT and not _is_privacy_localpart(email):
            candidates.append((CHANNEL_VULNERABILITY_BOUNTY, email))
        elif _is_dpo_localpart(email):
            candidates.append((CHANNEL_DPO_EMAIL, email))
        else:
            candidates.append((CHANNEL_PRIVACY_EMAIL, email))

    for url in urls or []:
        if is_bounty_platform(url):
            candidates.append((CHANNEL_VULNERABILITY_BOUNTY, url))
        else:
            candidates.append((CHANNEL_DSAR_PORTAL, url))

    return candidates


def _blank(platform: str, reason: str, home: str = "") -> dict:
    return {
        "platform": platform,
        "source": SOURCE_NONE,
        "emails": [],
        "urls": [],
        "verified": False,
        "verified_on": "",
        "home": home,
        "reason": reason,
    }


async def _get(session, url: str, timeout: int) -> tuple:
    rejection = await asyncio.to_thread(validate_target_url, url)
    if rejection:
        return None, ""
    headers = dict(BASE_HEADERS)
    headers["User-Agent"] = pick_user_agent(url)
    async with session.get(url, headers=headers, allow_redirects=True,
                           max_redirects=3,
                           timeout=aiohttp.ClientTimeout(total=timeout)) as response:
        if response.status != 200:
            return response.status, ""
        raw = await response.content.read(DISCOVERY_BODY_CAP)
        return response.status, raw.decode(response.charset or "utf-8", errors="ignore")


def _yields_privacy_channel(contact: dict) -> bool:
    """True when at least one candidate on `contact` is actually sendable
    toward a privacy request -- i.e. not only a vulnerability_bounty hit."""
    candidates = classify_contact(contact.get("source", ""),
                                  contact.get("emails"), contact.get("urls"))
    return any(channel != CHANNEL_VULNERABILITY_BOUNTY for channel, _ in candidates)


async def discover(session, host: str, *, timeout: int = DISCOVERY_TIMEOUT,
                   include_abuse_fallback: bool = True) -> dict:
    """Probe one host for a published privacy or security contact.

    Escalation order: security.txt, then privacy pages, then -- only as a
    last resort -- the RDAP/WHOIS abuse contact for the domain. A step
    "answering" is not enough to stop here: a security.txt Contact: line
    that resolves only to a bug-bounty platform is not a privacy channel
    (see the module docstring), so discovery keeps looking rather than
    reporting a vulnerability-disclosure link as though it were the answer.
    It is kept as a fallback in case nothing better turns up, so a
    bounty-only host still reports *something* rather than SOURCE_NONE.
    """
    base = f"https://{host}"
    fallback = None  # the best non-actionable hit, used only if nothing beats it

    for path in SECURITY_TXT_PATHS:
        try:
            status, body = await _get(session, base + path, timeout)
        except Exception:  # noqa: BLE001 - a missing file is the normal case
            continue
        if status != 200 or not body:
            continue
        parsed = parse_security_txt(body)
        if not (parsed["emails"] or parsed["urls"]):
            continue
        found = {"source": SOURCE_SECURITY_TXT, **parsed, "reason": f"published at {path}"}
        if _yields_privacy_channel(found):
            return found
        fallback = fallback or found
        break  # a security.txt file exists here; don't also probe the legacy path

    for path in PRIVACY_PATHS:
        try:
            status, body = await _get(session, base + path, timeout)
        except Exception:  # noqa: BLE001
            continue
        if status == 200 and body:
            parsed = parse_privacy_page(body)
            if parsed["emails"] or parsed["urls"]:
                return {"source": SOURCE_PRIVACY_PAGE, **parsed,
                        "reason": f"found on {path}"}

    if include_abuse_fallback:
        try:
            registration = await infra_checker.lookup_registration(session, host, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - RDAP is a bonus, not a requirement
            _log.info("Abuse-contact fallback for %s failed: %s", host, exc)
            registration = {}
        abuse_emails = registration.get("abuse_emails") or []
        if abuse_emails:
            return {"source": SOURCE_ABUSE_FALLBACK, "emails": abuse_emails, "urls": [],
                    "reason": "no privacy contact published; RDAP abuse contact "
                             f"via {registration.get('source') or 'rdap'}"}

    if fallback is not None:
        return fallback

    return {"source": SOURCE_NONE, "emails": [], "urls": [],
            "reason": "no security.txt, no privacy page, and no RDAP abuse contact"}


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


async def resolve_many(exposures: list, *, curated: dict | None = None,
                       discover_missing: bool = True,
                       abuse_fallback: bool = True,
                       timeout: int = DISCOVERY_TIMEOUT,
                       concurrency: int = 8) -> dict:
    """Resolve contacts for a list of exposures, keyed by platform name.

    Discovery is deduplicated by host and run once per host, then shared
    across every exposure on it -- gist.github.com and github.com resolve
    separately, which is correct, but forty accounts on one forum resolve
    once.
    """
    curated = load_curated() if curated is None else curated
    resolved: dict[str, dict] = {}
    to_discover: dict[str, list] = {}

    for exposure in exposures:
        platform = exposure.get("platform", "")
        if not platform or platform in resolved:
            continue
        entry = curated.get(platform.lower())
        if entry:
            resolved[platform] = {
                "platform": platform,
                "source": SOURCE_CURATED,
                "emails": entry.get("emails") or ([entry["email"]] if entry.get("email") else []),
                "urls": entry.get("urls") or ([entry["optout_url"]] if entry.get("optout_url") else []),
                "verified": bool(entry.get("verified")),
                "verified_on": entry.get("verified_on", ""),
                "home": entry.get("home", ""),
                "reason": "curated entry",
            }
            continue
        host = host_of(exposure.get("url", ""))
        if host and discover_missing:
            to_discover.setdefault(host, []).append(platform)
        else:
            resolved[platform] = _blank(platform, "no curated entry and no URL to probe")

    if to_discover:
        semaphore = asyncio.Semaphore(concurrency)
        connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=2)

        async def _one(host):
            async with semaphore:
                try:
                    return host, await discover(session, host, timeout=timeout,
                                                include_abuse_fallback=abuse_fallback)
                except Exception as exc:  # noqa: BLE001
                    _log.info("Contact discovery failed for %s: %s", host, exc)
                    return host, {"source": SOURCE_NONE, "emails": [], "urls": [],
                                  "reason": "discovery failed"}

        async with aiohttp.ClientSession(connector=connector) as session:
            for host, found in await asyncio.gather(*(_one(h) for h in to_discover)):
                for platform in to_discover[host]:
                    resolved[platform] = {
                        "platform": platform,
                        "verified": False,
                        "verified_on": "",
                        "home": f"https://{host}",
                        **found,
                    }

    return resolved


def best_channel(contact: dict) -> dict:
    """Pick the delivery channel for a resolved contact.

    Every candidate the contact record offers is classified via
    classify_contact(), then the highest-priority one wins under the
    escalation order in the module docstring (dpo_email > privacy_email >
    dsar_portal > abuse_fallback > vulnerability_bounty > none). `ready` is
    False for CHANNEL_NONE and for CHANNEL_VULNERABILITY_BOUNTY -- the
    latter is surfaced because finding *something* is worth reporting, but
    a vulnerability-disclosure channel is never where a CCPA/GDPR demand
    belongs.
    """
    candidates = classify_contact(contact.get("source", ""),
                                  contact.get("emails"), contact.get("urls"))
    if not candidates:
        return {"channel": CHANNEL_NONE, "target": "", "ready": False}

    channel, target = max(candidates, key=lambda pair: _CHANNEL_PRIORITY.get(pair[0], -1))
    return {"channel": channel, "target": target, "ready": channel in _SENDABLE_CHANNELS}
