"""
Infrastructure checks for the hosts an exposure lives on.

recon_engine finds *that* a profile exists; privacy_contacts finds *who to
write to*. This module answers the questions that sit between them, all of
them about the host rather than the person:

  * Is this domain still live, and does it accept mail? A statutory demand
    emailed to a domain with no MX has not been served on anyone. That is
    worth knowing before a 45-day clock is started against it.
  * Who is on record as operating it? Registrar, registration dates, and
    the RDAP abuse contact are the escalation path when a site publishes no
    privacy address at all -- the fallback privacy_contacts leaves open.
  * Do two exposures actually belong to the same operator? Shared
    nameservers, a shared mail exchanger, a shared certificate, or a shared
    registrar are the observable evidence for that, and one demand to one
    operator beats four demands to four brands that share a backend.

SCOPE

Everything here is a lookup against public registration and DNS data plus a
single TLS handshake -- the same traffic a browser makes to load the site.
There is no port scanning, no vulnerability probing, no subdomain
brute-forcing, no attempt to enumerate anything the operator has not
published. The output is a compliance record about a company, not a map of
its attack surface.

DEPENDENCIES AND DEGRADATION

dnspython and python-whois are both optional. Without dnspython the DNS
check degrades to A/AAAA records via the stdlib resolver and says so in the
record's `partial` flag; without python-whois registration data comes from
RDAP alone, which covers every gTLD and most ccTLDs. Nothing here raises
because an optional package is missing -- a missing check is reported as
unknown, never as a negative finding, because "no abuse contact found"
and "we could not look" lead to very different next actions.
"""
import asyncio
import re
import socket
import ssl
from datetime import datetime, timezone

import aiohttp

from applog import get_logger
from footprint_scanner import validate_target_url
from recon_engine import BASE_HEADERS, pick_user_agent

_log = get_logger("infra_checker")

try:  # optional; see module docstring
    import dns.resolver as _dns_resolver
    import dns.exception as _dns_exception
except ImportError:  # pragma: no cover - exercised by the degradation test
    _dns_resolver = None
    _dns_exception = None

try:  # optional; RDAP covers most of what this would tell us
    import whois as _whois
except ImportError:  # pragma: no cover
    _whois = None

from cryptography import x509
from cryptography.hazmat.primitives import hashes

# Status vocabulary. Deliberately distinct from the scan verdicts in
# footprint_scanner: an infrastructure check is not evidence about a person,
# and reusing CONFIRMED/POSSIBLE here would let one leak into the other.
OK = "OK"
UNKNOWN = "UNKNOWN"          # we could not look; not a negative finding
ABSENT = "ABSENT"            # we looked and the record genuinely is not there

DEFAULT_DNS_TIMEOUT = 5
DEFAULT_TLS_TIMEOUT = 8
DEFAULT_RDAP_TIMEOUT = 15
DEFAULT_CONCURRENCY = 8

RECORD_TYPES = ("A", "AAAA", "MX", "NS", "TXT", "CNAME")

RDAP_BOOTSTRAP = "https://rdap.org/domain/"
RDAP_BODY_CAP = 1_000_000

# Multi-part public suffixes common enough to matter for the domains this
# tool actually sees. This is a heuristic, not the Public Suffix List: it is
# used to group hosts by operator, and a wrong grouping is visible in the
# report rather than silently wrong in a letter.
_MULTI_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "com.au", "net.au",
    "org.au", "co.nz", "co.za", "co.jp", "or.jp", "ne.jp", "com.br",
    "com.mx", "com.ar", "co.in", "com.sg", "com.hk", "com.tr", "co.kr",
})

# Registrars and privacy services whose presence says nothing about who the
# operator is -- they are the redaction, not the answer.
_PRIVACY_PROXY_MARKERS = (
    "privacy", "redacted", "whoisguard", "domains by proxy", "withheld",
    "protection service", "proxy service", "not disclosed", "data protected",
    "identity shield", "contact privacy",
)

_ABUSE_ROLE = "abuse"
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}")


def registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 for `host`.

    Used only for grouping and for the RDAP query, both of which tolerate
    the occasional miss on an exotic suffix. See _MULTI_SUFFIXES.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host or _is_ip_literal(host):
        return host
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in _MULTI_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _is_ip_literal(host: str) -> bool:
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return ":" in host


def is_privacy_proxy(value: str) -> bool:
    """True when a registrant field is a redaction rather than a name."""
    lowered = (value or "").lower()
    return any(marker in lowered for marker in _PRIVACY_PROXY_MARKERS)


def _blank_dns(host: str, reason: str) -> dict:
    return {
        "host": host, "status": UNKNOWN, "records": {}, "resolves": False,
        "accepts_mail": False, "partial": True, "reason": reason,
    }


def resolve_dns(host: str, *, timeout: int = DEFAULT_DNS_TIMEOUT,
                record_types: tuple = RECORD_TYPES) -> dict:
    """Look up `record_types` for `host`. Blocking; call via asyncio.to_thread.

    NXDOMAIN and an empty answer are reported as ABSENT (we looked, nothing
    is there); a timeout or a resolver failure is UNKNOWN. A demand routed
    on the strength of "this domain does not resolve" needs that distinction
    to be real, because the two cases justify opposite actions.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return _blank_dns(host, "no host given")

    if _dns_resolver is None:
        return _stdlib_dns(host)

    resolver = _dns_resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout

    records, failures, absent = {}, [], []
    for rtype in record_types:
        try:
            answer = resolver.resolve(host, rtype)
        except (_dns_resolver.NXDOMAIN, _dns_resolver.NoAnswer):
            absent.append(rtype)
            continue
        except (_dns_exception.Timeout, _dns_resolver.NoNameservers) as exc:
            _log.info("DNS %s for %s unavailable: %s", rtype, host, exc)
            failures.append(rtype)
            continue
        except Exception as exc:  # noqa: BLE001 - one record type must not end the check
            _log.info("DNS %s for %s failed: %s", rtype, host, exc)
            failures.append(rtype)
            continue
        values = sorted(_render_rdata(rtype, item) for item in answer)
        if values:
            records[rtype] = values

    resolves = bool(records.get("A") or records.get("AAAA") or records.get("CNAME"))
    if not records and failures:
        status = UNKNOWN
    elif not records:
        status = ABSENT
    else:
        status = OK

    return {
        "host": host,
        "status": status,
        "records": records,
        "resolves": resolves,
        "accepts_mail": accepts_mail(records.get("MX")),
        "partial": bool(failures),
        "reason": ("unavailable: " + ", ".join(failures)) if failures else "",
    }


def accepts_mail(mx_records) -> bool:
    """Whether an MX set means mail can actually be delivered.

    A single "0 ." is RFC 7505's null MX: the operator is stating that the
    domain receives no mail at all. Counting that as deliverable is the
    exact error this module exists to catch -- a demand emailed there is
    never served, and the 45-day clock would be started against nobody.
    """
    hosts = [str(r).split()[-1].rstrip(".") for r in (mx_records or []) if str(r).strip()]
    return any(host for host in hosts)


def _render_rdata(rtype: str, item) -> str:
    """One DNS answer as a stable string.

    MX keeps its preference because two brands sharing "10 mx.provider.net"
    is stronger evidence of a shared operator than sharing a hostname at
    different preferences.
    """
    text = item.to_text().strip()
    if rtype == "TXT":
        return text.strip('"')
    return text.rstrip(".") if rtype in ("NS", "CNAME") else text


def _stdlib_dns(host: str) -> dict:
    """A/AAAA only, via getaddrinfo. The no-dnspython fallback."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return {"host": host, "status": ABSENT, "records": {}, "resolves": False,
                "accepts_mail": False, "partial": True,
                "reason": "does not resolve (stdlib resolver; MX/NS not checked)"}
    except OSError as exc:
        return _blank_dns(host, f"resolver error: {exc}")

    records = {}
    for info in infos:
        address = info[4][0]
        key = "AAAA" if ":" in address else "A"
        records.setdefault(key, [])
        if address not in records[key]:
            records[key].append(address)
    for key in records:
        records[key].sort()

    return {"host": host, "status": OK, "records": records, "resolves": True,
            "accepts_mail": False, "partial": True,
            "reason": "dnspython not installed; MX/NS/TXT not checked"}


def parse_certificate(der: bytes) -> dict:
    """Pull the operator-identifying fields out of a DER certificate."""
    cert = x509.load_der_x509_certificate(der)

    def _attr(name, oid):
        try:
            values = name.get_attributes_for_oid(oid)
        except Exception:  # noqa: BLE001
            return ""
        return values[0].value if values else ""

    try:
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        san = []

    not_after_raw = getattr(cert, "not_valid_after_utc", None) or getattr(cert, "not_valid_after", None)
    not_before_raw = getattr(cert, "not_valid_before_utc", None) or getattr(cert, "not_valid_before", None)
    not_after = _as_utc(not_after_raw)
    not_before = _as_utc(not_before_raw)
    now = datetime.now(timezone.utc)

    subject_org = _attr(cert.subject, x509.oid.NameOID.ORGANIZATION_NAME)
    issuer_org = _attr(cert.issuer, x509.oid.NameOID.ORGANIZATION_NAME)

    return {
        "subject_cn": _attr(cert.subject, x509.oid.NameOID.COMMON_NAME),
        "subject_org": subject_org,
        "issuer_cn": _attr(cert.issuer, x509.oid.NameOID.COMMON_NAME),
        "issuer_org": issuer_org,
        "sans": sorted(san),
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "days_remaining": (not_after - now).days,
        "expired": not_after < now,
        "not_yet_valid": not_before > now,
        "self_signed": cert.subject == cert.issuer,
    }


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fetch_certificate(host: str, *, port: int = 443,
                      timeout: int = DEFAULT_TLS_TIMEOUT) -> dict:
    """One TLS handshake against `host`, reported as a dict.

    Verification failures do not abort the check. An expired or mismatched
    certificate still names an operator, and that name is the reason we are
    here; the failure is recorded in `verified`/`verify_error` rather than
    thrown away. The unverified retry is a read of what the host presented,
    not a decision to trust it -- nothing is sent over that connection.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return {"host": host, "status": UNKNOWN, "reason": "no host given"}

    rejection = validate_target_url(f"https://{host}:{port}/")
    if rejection:
        return {"host": host, "status": UNKNOWN, "reason": rejection}

    verified, verify_error = True, ""
    try:
        der = _handshake(host, port, timeout, verify=True)
    except ssl.SSLCertVerificationError as exc:
        # verify_message is only populated when the error comes from the ssl
        # module's own verification path; it is absent on a re-raise.
        verified, verify_error = False, getattr(exc, "verify_message", "") or str(exc)
        try:
            der = _handshake(host, port, timeout, verify=False)
        except (OSError, ssl.SSLError) as retry_exc:
            return {"host": host, "status": UNKNOWN,
                    "reason": f"TLS handshake failed: {retry_exc}"}
    except (socket.timeout, TimeoutError):
        return {"host": host, "status": UNKNOWN, "reason": "TLS handshake timed out"}
    except (OSError, ssl.SSLError) as exc:
        return {"host": host, "status": UNKNOWN, "reason": f"TLS handshake failed: {exc}"}

    try:
        parsed = parse_certificate(der)
    except Exception as exc:  # noqa: BLE001 - malformed cert is a finding, not a crash
        return {"host": host, "status": UNKNOWN, "reason": f"unparseable certificate: {exc}"}

    parsed.update({"host": host, "port": port, "status": OK,
                   "verified": verified, "verify_error": verify_error, "reason": ""})
    return parsed


def _handshake(host: str, port: int, timeout: int, *, verify: bool) -> bytes:
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


def _vcard_values(entity: dict, key: str) -> list:
    """Values for `key` out of an RDAP jCard.

    jCard is [["vcard", [[name, params, type, value], ...]] -- positional,
    loosely followed in the wild, and worth exactly one defensive walk.
    """
    values = []
    vcard = entity.get("vcardArray") or []
    if len(vcard) < 2 or not isinstance(vcard[1], list):
        return values
    for field in vcard[1]:
        if not isinstance(field, list) or len(field) < 4:
            continue
        if str(field[0]).lower() != key:
            continue
        value = field[3]
        if isinstance(value, list):
            value = " ".join(str(part) for part in value if part)
        if value:
            values.append(str(value).strip())
    return values


def _walk_entities(entities, depth: int = 0):
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        yield entity
        if depth < 4:
            yield from _walk_entities(entity.get("entities"), depth + 1)


def parse_rdap(payload: dict) -> dict:
    """Registrar, dates, and the abuse channel out of an RDAP response."""
    events = {}
    for event in payload.get("events") or []:
        action = str(event.get("eventAction", "")).lower()
        date = event.get("eventDate")
        if action and date:
            events[action] = date

    registrar, registrant, abuse_emails = "", "", []
    for entity in _walk_entities(payload.get("entities")):
        roles = [str(role).lower() for role in entity.get("roles") or []]
        names = _vcard_values(entity, "fn")
        if "registrar" in roles and not registrar:
            registrar = names[0] if names else ""
        if "registrant" in roles and not registrant:
            registrant = names[0] if names else ""
        if _ABUSE_ROLE in roles:
            abuse_emails.extend(_vcard_values(entity, "email"))
        # Some registrars attach the abuse mailbox to the registrar entity
        # itself with no abuse-role child. Take it, but only that address.
        if "registrar" in roles and not abuse_emails:
            abuse_emails.extend(
                addr for addr in _vcard_values(entity, "email")
                if "abuse" in addr.lower())

    seen, ordered = set(), []
    for addr in abuse_emails:
        match = _EMAIL_RE.search(addr)
        clean = match.group(0).lower() if match else ""
        if clean and clean not in seen:
            seen.add(clean)
            ordered.append(clean)

    return {
        "domain": str(payload.get("ldhName", "")).lower(),
        "registrar": registrar,
        "registrant": registrant,
        "registrant_redacted": is_privacy_proxy(registrant) or not registrant,
        "abuse_emails": ordered,
        "created": events.get("registration", ""),
        "updated": events.get("last changed", ""),
        "expires": events.get("expiration", ""),
        "statuses": sorted(str(s) for s in payload.get("status") or []),
        "nameservers": sorted(
            str(ns.get("ldhName", "")).lower().rstrip(".")
            for ns in payload.get("nameservers") or [] if ns.get("ldhName")),
        "source": "rdap",
    }


def _whois_statuses(value) -> list:
    """WHOIS `status` arrives as a string, a list, or nothing at all."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return sorted({str(item) for item in value if item})


def _from_whois(record) -> dict:
    """python-whois record -> the same shape parse_rdap returns."""
    def _one(value):
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if isinstance(value, datetime):
            return _as_utc(value).isoformat()
        return str(value or "").strip()

    registrant = _one(getattr(record, "org", "")) or _one(getattr(record, "name", ""))
    emails = getattr(record, "emails", []) or []
    if isinstance(emails, str):
        emails = [emails]

    return {
        "domain": _one(getattr(record, "domain_name", "")).lower(),
        "registrar": _one(getattr(record, "registrar", "")),
        "registrant": registrant,
        "registrant_redacted": is_privacy_proxy(registrant) or not registrant,
        "abuse_emails": [e.lower() for e in emails if "abuse" in str(e).lower()],
        "created": _one(getattr(record, "creation_date", "")),
        "updated": _one(getattr(record, "updated_date", "")),
        "expires": _one(getattr(record, "expiration_date", "")),
        "statuses": _whois_statuses(getattr(record, "status", None)),
        "nameservers": sorted({str(ns).lower().rstrip(".")
                               for ns in (getattr(record, "name_servers", None) or [])}),
        "source": "whois",
    }


async def lookup_registration(session, host: str, *,
                              timeout: int = DEFAULT_RDAP_TIMEOUT,
                              allow_whois: bool = True) -> dict:
    """Registration data for `host`, RDAP first and WHOIS only as a fallback.

    RDAP is preferred wherever it exists: it is JSON with defined fields,
    where WHOIS is free text whose layout changes per registry and whose
    parsers guess. A registrar name that came from a guess is not something
    to cite in a demand letter, so the source is recorded on every record.
    """
    domain = registrable_domain(host)
    if not domain or _is_ip_literal(domain):
        return {"domain": domain, "status": UNKNOWN, "source": "",
                "reason": "not a registrable domain"}

    url = RDAP_BOOTSTRAP + domain
    rejection = await asyncio.to_thread(validate_target_url, url)
    if not rejection:
        try:
            async with session.get(
                url,
                headers={**BASE_HEADERS, "Accept": "application/rdap+json",
                         "User-Agent": pick_user_agent(domain)},
                allow_redirects=True, max_redirects=5,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status == 200:
                    raw = await response.content.read(RDAP_BODY_CAP)
                    parsed = parse_rdap(_loads(raw))
                    parsed.update({"status": OK, "reason": ""})
                    return parsed
                if response.status == 404:
                    return {"domain": domain, "status": ABSENT, "source": "rdap",
                            "reason": "not registered (RDAP 404)",
                            "registrar": "", "abuse_emails": []}
                _log.info("RDAP for %s returned HTTP %s", domain, response.status)
        except Exception as exc:  # noqa: BLE001 - fall through to WHOIS
            _log.info("RDAP lookup for %s failed: %s", domain, exc)

    if allow_whois and _whois is not None:
        try:
            record = await asyncio.to_thread(_whois.whois, domain)
            if record and getattr(record, "domain_name", None):
                parsed = _from_whois(record)
                parsed.update({"status": OK, "reason": "RDAP unavailable; WHOIS text parsed"})
                return parsed
        except Exception as exc:  # noqa: BLE001
            _log.info("WHOIS lookup for %s failed: %s", domain, exc)

    return {"domain": domain, "status": UNKNOWN, "source": "",
            "registrar": "", "abuse_emails": [],
            "reason": "no registration data available"}


def _loads(raw: bytes) -> dict:
    import json
    payload = json.loads(raw.decode("utf-8", errors="ignore"))
    return payload if isinstance(payload, dict) else {}


async def check_host(session, host: str, *, dns_timeout: int = DEFAULT_DNS_TIMEOUT,
                     tls_timeout: int = DEFAULT_TLS_TIMEOUT,
                     rdap_timeout: int = DEFAULT_RDAP_TIMEOUT,
                     with_tls: bool = True, with_registration: bool = True) -> dict:
    """DNS + TLS + registration for one host, gathered concurrently."""
    host = (host or "").strip().lower().rstrip(".")
    tasks = {"dns": asyncio.to_thread(resolve_dns, host, timeout=dns_timeout)}
    if with_tls:
        tasks["tls"] = asyncio.to_thread(fetch_certificate, host, timeout=tls_timeout)
    if with_registration:
        tasks["registration"] = lookup_registration(session, host, timeout=rdap_timeout)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    report = {"host": host, "domain": registrable_domain(host),
              "checked_at": datetime.now(timezone.utc).isoformat()}
    for key, value in zip(tasks, results):
        if isinstance(value, BaseException):
            _log.info("%s check for %s raised: %s", key, host, value)
            report[key] = {"status": UNKNOWN, "reason": f"check failed: {value}"}
        else:
            report[key] = value

    report["live"] = bool(report.get("dns", {}).get("resolves"))
    report["mail_routable"] = bool(report.get("dns", {}).get("accepts_mail"))
    report["operator"] = _operator_name(report)
    report["escalation_emails"] = list(
        report.get("registration", {}).get("abuse_emails") or [])
    report["fingerprint"] = operator_fingerprint(report)
    return report


def _operator_name(report: dict) -> str:
    """The most defensible name we have for whoever runs this host.

    Certificate subject organisation first: it is the name a CA validated
    against the entity, which is a stronger claim than a registrant field
    the registrar will usually have redacted anyway.
    """
    tls = report.get("tls") or {}
    org = tls.get("subject_org") or ""
    if org and not is_privacy_proxy(org):
        return org
    registration = report.get("registration") or {}
    registrant = registration.get("registrant") or ""
    if registrant and not is_privacy_proxy(registrant):
        return registrant
    return ""


def operator_fingerprint(report: dict) -> dict:
    """The linkage keys two hosts can share.

    Wildcard SANs are reduced to their base domain: *.example.com and
    *.eu.example.com both point at one operator, and keeping the literal
    strings would make them look unrelated.
    """
    dns_records = (report.get("dns") or {}).get("records") or {}
    tls = report.get("tls") or {}
    registration = report.get("registration") or {}

    sans = set()
    for name in tls.get("sans") or []:
        name = name.lower().lstrip("*.")
        if name:
            sans.add(registrable_domain(name))

    return {
        "nameservers": sorted({ns.lower() for ns in dns_records.get("NS", [])}),
        "mx": sorted({mx.lower() for mx in dns_records.get("MX", [])}),
        "addresses": sorted(set(dns_records.get("A", []) + dns_records.get("AAAA", []))),
        "cert_fingerprint": tls.get("fingerprint_sha256", ""),
        "cert_domains": sorted(sans),
        "registrar": (registration.get("registrar") or "").lower(),
        "operator": (report.get("operator") or "").lower(),
    }


# Weakest first: a shared registrar is a hint, a shared certificate is
# effectively proof. Callers grouping demand letters should require more
# than "registrar" alone.
_LINK_STRENGTH = (
    ("cert_fingerprint", "serve the same TLS certificate"),
    ("operator", "name the same operator"),
    ("cert_domains", "share a certificate domain"),
    ("nameservers", "share a nameserver"),
    ("mx", "share a mail exchanger"),
    ("addresses", "resolve to the same address"),
    ("registrar", "share a registrar"),
)

_WEAK_LINKS = frozenset({"registrar", "addresses"})


def shared_operator(a: dict, b: dict) -> tuple:
    """(bool, reasons) for whether two host reports look like one operator.

    A shared registrar or a shared IP alone is not enough -- GoDaddy and a
    shared CDN edge would otherwise merge half the internet into a single
    respondent. Those count only alongside something stronger.
    """
    left, right = a.get("fingerprint") or {}, b.get("fingerprint") or {}
    reasons, strong = [], False

    for key, phrase in _LINK_STRENGTH:
        lv, rv = left.get(key), right.get(key)
        if not lv or not rv:
            continue
        if isinstance(lv, list):
            overlap = sorted(set(lv) & set(rv or []))
            if not overlap:
                continue
            reasons.append(f"{phrase} ({', '.join(overlap[:3])})")
        elif lv != rv:
            continue
        else:
            reasons.append(f"{phrase} ({lv})")
        if key not in _WEAK_LINKS:
            strong = True

    return strong, reasons


def group_by_operator(reports: list) -> list:
    """Cluster host reports into one group per apparent operator."""
    groups = []
    for report in reports:
        for group in groups:
            if any(shared_operator(report, member)[0] for member in group["members"]):
                group["members"].append(report)
                break
        else:
            groups.append({"members": [report]})

    for group in groups:
        group["hosts"] = sorted(m.get("host", "") for m in group["members"])
        group["operator"] = next(
            (m["operator"] for m in group["members"] if m.get("operator")), "")
        group["escalation_emails"] = sorted({
            email for m in group["members"] for email in m.get("escalation_emails", [])})
        first = group["members"][0]
        group["evidence"] = [
            reason
            for other in group["members"][1:]
            for reason in shared_operator(first, other)[1]
        ]
    return groups


async def check_hosts(hosts, *, concurrency: int = DEFAULT_CONCURRENCY,
                      session=None, **kwargs) -> list:
    """check_host over many hosts, deduplicated and bounded."""
    unique = sorted({(h or "").strip().lower().rstrip(".") for h in hosts if h})
    if not unique:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def _one(session_, host):
        async with semaphore:
            return await check_host(session_, host, **kwargs)

    async def _run(session_):
        return list(await asyncio.gather(*(_one(session_, h) for h in unique)))

    if session is not None:
        return await _run(session)
    connector = aiohttp.TCPConnector(limit=concurrency, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as owned:
        return await _run(owned)


def check_hosts_sync(hosts, **kwargs) -> list:
    return asyncio.run(check_hosts(hosts, **kwargs))


def summarize(reports: list) -> dict:
    """Counts a caller can put in front of a person without reading rows."""
    total = len(reports)
    live = sum(1 for r in reports if r.get("live"))
    mailable = sum(1 for r in reports if r.get("mail_routable"))
    escalation = sum(1 for r in reports if r.get("escalation_emails"))
    expiring = sorted(
        (r.get("host", "") for r in reports
         if 0 <= (r.get("tls") or {}).get("days_remaining", 9999) <= 30))
    return {
        "hosts": total,
        "live": live,
        "dead": total - live,
        "mail_routable": mailable,
        "with_escalation_contact": escalation,
        "tls_expiring_soon": expiring,
        "operator_groups": len(group_by_operator(reports)) if reports else 0,
    }
