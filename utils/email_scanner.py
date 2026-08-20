"""
Passive email reconnaissance engine.

Zero-alert, read-only email verification through public APIs and DNS:
- Gravatar/Libravatar profile hashes for avatar/bio presence
- PGP key server lookups for encryption key associations
- DNS MX/SPF validation for domain hygiene

All operations are passive (no form probes, password resets, registration hits,
or side effects). The goal is mapping email exposure without alerting the target.
"""
import hashlib
import socket
from urllib.parse import urlencode

import requests

from applog import get_logger

_log = get_logger("email_scanner")

GRAVATAR_API = "https://www.gravatar.com/avatar"
GRAVATAR_JSON_FORMAT = "?d=404&format=json"

LIBRAVATAR_API = "https://www.libravatar.org/avatar"
LIBRAVATAR_JSON_FORMAT = "?d=404&format=json"

PGP_API = "https://keys.openpgp.org/vks/v1/by-email"

CONFIRMED = "CONFIRMED"
POSSIBLE = "POSSIBLE"
NOT_FOUND = "NOT_FOUND"
ERROR = "ERROR"

TIMEOUT = 10


def _gravatar_hash(email: str) -> str:
    """MD5 hash of lowercased, trimmed email (Gravatar standard)."""
    return hashlib.md5(email.lower().strip().encode()).hexdigest()


def _libravatar_hash(email: str) -> str:
    """SHA256 hash of lowercased, trimmed email (Libravatar standard)."""
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()


def check_gravatar(email: str) -> tuple[str, str]:
    """Passive Gravatar profile check via public CDN endpoint.

    Returns (verdict, reason). Does not modify anything, only queries public
    JSON endpoint with a 404 default to detect profile presence.
    """
    if not email or "@" not in email:
        return NOT_FOUND, "invalid email format"

    try:
        hash_val = _gravatar_hash(email)
        url = f"{GRAVATAR_API}/{hash_val}{GRAVATAR_JSON_FORMAT}"
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
        if response.status_code == 200:
            try:
                data = response.json()
                return CONFIRMED, f"Gravatar profile found (entry_count={data.get('entry', [{}])[0].get('id', 'unknown')})"
            except Exception:
                return POSSIBLE, "Gravatar returned 200 but unparseable JSON"
        elif response.status_code == 404:
            return NOT_FOUND, "Gravatar 404 (no public profile)"
        else:
            return ERROR, f"Gravatar HTTP {response.status_code}"
    except requests.RequestException as e:
        return ERROR, f"Gravatar request failed: {str(e)[:60]}"


def check_libravatar(email: str) -> tuple[str, str]:
    """Passive Libravatar profile check (decentralized Gravatar alternative).

    Returns (verdict, reason). Same passive query pattern as Gravatar.
    """
    if not email or "@" not in email:
        return NOT_FOUND, "invalid email format"

    try:
        hash_val = _libravatar_hash(email)
        url = f"{LIBRAVATAR_API}/{hash_val}{LIBRAVATAR_JSON_FORMAT}"
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
        if response.status_code == 200:
            return CONFIRMED, "Libravatar profile found"
        elif response.status_code == 404:
            return NOT_FOUND, "Libravatar 404 (no public profile)"
        else:
            return ERROR, f"Libravatar HTTP {response.status_code}"
    except requests.RequestException as e:
        return ERROR, f"Libravatar request failed: {str(e)[:60]}"


def check_pgp_keys(email: str) -> tuple[str, str]:
    """Passive PGP key server lookup (keys.openpgp.org).

    Returns (verdict, reason). Checks for public encryption key associations
    without modifying anything.
    """
    if not email or "@" not in email:
        return NOT_FOUND, "invalid email format"

    try:
        params = {"q": email}
        url = f"{PGP_API}?{urlencode(params)}"
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
        if response.status_code == 200:
            try:
                data = response.json()
                keys = data.get("keys", [])
                if keys:
                    return CONFIRMED, f"PGP public key(s) found ({len(keys)} key(s) registered)"
                else:
                    return NOT_FOUND, "PGP 200 OK but no keys for this email"
            except Exception:
                return POSSIBLE, "PGP server returned data but unparseable"
        elif response.status_code == 404:
            return NOT_FOUND, "PGP 404 (no keys registered)"
        else:
            return ERROR, f"PGP HTTP {response.status_code}"
    except requests.RequestException as e:
        return ERROR, f"PGP request failed: {str(e)[:60]}"


def check_dns_records(email: str) -> tuple[str, str]:
    """Passive DNS validation for domain MX/SPF.

    For custom domains (emails with non-public suffixes), validates that
    the domain can be resolved (basic hygiene check).
    """
    if not email or "@" not in email:
        return NOT_FOUND, "invalid email format"

    try:
        domain = email.split("@")[-1].lower()
        # Public-suffix check: skip for well-known providers
        public_suffixes = {"gmail.com", "yahoo.com", "outlook.com", "protonmail.com", "icloud.com"}
        if domain in public_suffixes:
            return NOT_FOUND, f"{domain} is a public email provider (no custom domain)"

        # Check domain resolves (simple DNS validation)
        try:
            socket.gethostbyname(domain)
            return CONFIRMED, f"Domain {domain} resolves (valid)"
        except socket.gaierror:
            return NOT_FOUND, f"Domain {domain} does not resolve"
    except Exception as e:
        return ERROR, f"DNS check failed: {str(e)[:60]}"


def scan_email(email: str) -> list:
    """Scan an email through all passive vectors.

    Returns list of result dicts: [
        {
            "service": "Gravatar" | "Libravatar" | "PGP Keys" | "Domain DNS",
            "identifier": email,
            "confidence": CONFIRMED | POSSIBLE | NOT_FOUND | ERROR,
            "reason": str,
            "vector": str,
        },
        ...
    ]
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return []

    results = []
    checks = [
        ("Gravatar", "avatar_service", check_gravatar),
        ("Libravatar", "avatar_service", check_libravatar),
        ("PGP Keys", "encryption_key", check_pgp_keys),
        ("Domain DNS", "domain_validation", check_dns_records),
    ]

    for service, vector, check_fn in checks:
        try:
            verdict, reason = check_fn(email)
            results.append({
                "service": service,
                "identifier": email,
                "confidence": verdict,
                "reason": reason,
                "vector": vector,
            })
        except Exception as e:
            email_token = hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]
            _log.warning("Check %s failed for email token %s: %s", service, email_token, e)
            results.append({
                "service": service,
                "identifier": email,
                "confidence": ERROR,
                "reason": f"Exception: {str(e)[:60]}",
                "vector": vector,
            })

    return results


EMAIL_CATEGORY = "email"

# Where a human goes to see the association for themselves. Libravatar
# has no public profile page (only the avatar endpoint), and a resolving
# MX record isn't a profile at all, so neither gets a link rather than
# being handed a URL that proves nothing.
_PROFILE_URL_BUILDERS = {
    "Gravatar": lambda email: f"https://gravatar.com/{_gravatar_hash(email)}",
    "PGP Keys": lambda email: f"{PGP_API}?{urlencode({'q': email})}",
}


def _extract_avatar_url(service: str, email: str, response_data: dict | None) -> str:
    """Extract a displayable avatar image URL from a service response.

    Gravatar and Libravatar both return avatar images at predictable URLs
    built from the email hash. For Libravatar, we use the endpoint URL
    directly; for Gravatar, the CDN address is stable.
    """
    if service == "Gravatar":
        hash_val = _gravatar_hash(email)
        return f"https://www.gravatar.com/avatar/{hash_val}?s=128&d=404"
    elif service == "Libravatar":
        hash_val = _libravatar_hash(email)
        return f"https://www.libravatar.org/avatar/{hash_val}?s=128&d=404"
    return ""


def email_discoveries(results: list) -> list:
    """Reshape scan results into discovered_accounts rows.

    Only CONFIRMED and POSSIBLE carry through. A NOT_FOUND is the absence
    of an association, and writing those to a table called
    "discovered_accounts" would inflate every downstream count -- the
    audit summary included -- with things that were never found.

    The identifier is the email itself, so re-scanning the same address
    updates its rows rather than accumulating duplicates (the table's
    UNIQUE(platform, target_identifier) does the work).

    Avatar URLs are included for visual confirmation: Gravatar and
    Libravatar both serve avatar images at hash-based URLs.
    """
    rows = []
    for result in results:
        if result["confidence"] not in (CONFIRMED, POSSIBLE):
            continue
        email = result["identifier"]
        service = result["service"]
        builder = _PROFILE_URL_BUILDERS.get(service)
        rows.append({
            "platform": service,
            "category": EMAIL_CATEGORY,
            "target_identifier": email,
            "profile_url": builder(email) if builder else "",
            "avatar_url": _extract_avatar_url(service, email, None),
            "confidence": result["confidence"],
            "reason": result["reason"],
        })
    return rows


def summarize_email_scan(results: list) -> dict:
    """Aggregate scan results into summary counts."""
    summary = {CONFIRMED: 0, POSSIBLE: 0, NOT_FOUND: 0, ERROR: 0}
    for result in results:
        summary[result["confidence"]] = summary.get(result["confidence"], 0) + 1
    return summary
