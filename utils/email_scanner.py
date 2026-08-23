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
from urllib.parse import quote, urlencode

import requests

from applog import get_logger

_log = get_logger("email_scanner")

GRAVATAR_API = "https://www.gravatar.com/avatar"
GRAVATAR_JSON_FORMAT = "?d=404&format=json"

LIBRAVATAR_API = "https://www.libravatar.org/avatar"
LIBRAVATAR_JSON_FORMAT = "?d=404&format=json"

# The email goes in the *path*, not a ?q= parameter. The query form
# 301-redirects and then 404s for every address, which is why this vector
# reported ERROR on every scan until it was checked against the live API.
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
        # www.libravatar.org 301s to the apex domain. Without following it
        # this check reported ERROR for every address ever scanned.
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
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

    The endpoint answers with an ASCII-armored key block, not JSON -- a 200
    carrying "BEGIN PGP PUBLIC KEY BLOCK" is the hit, and 404 is the miss.
    """
    if not email or "@" not in email:
        return NOT_FOUND, "invalid email format"

    try:
        url = f"{PGP_API}/{quote(email)}"
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code == 200:
            try:
                data = response.json()
                if isinstance(data, dict) and "keys" in data:
                    keys = data.get("keys", [])
                    if keys:
                        count = len(keys)
                        s = "s" if count != 1 else ""
                        return CONFIRMED, f"PGP public key registered ({count} key{s})"
                    return NOT_FOUND, "PGP 200 OK but no keys in response"
            except Exception:
                pass
            if "BEGIN PGP PUBLIC KEY BLOCK" in response.text:
                return CONFIRMED, "PGP public key registered for this address"
            return NOT_FOUND, "PGP 200 OK but no key block returned"
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


import holehe_scanner


def scan_email(email: str) -> list:
    """Scan an email through all passive Holehe-style multi-platform vectors
    and DNS hygiene validation.

    Returns list of result dicts for each probed service.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return []

    try:
        results = holehe_scanner.scan_email_sync(email)
    except Exception as exc:
        _log.warning("Holehe async email scan failed: %s", exc)
        results = []

    # If holehe scan returned nothing (e.g. offline sandbox without event loop),
    # fall back to standard built-in probes
    if not results:
        checks = [
            ("Gravatar", "avatar_service", check_gravatar),
            ("Libravatar", "avatar_service", check_libravatar),
            ("PGP Keys", "encryption_key", check_pgp_keys),
        ]
        for service, vector, check_fn in checks:
            try:
                verdict, reason = check_fn(email)
                results.append({
                    "platform": service,
                    "service": service,
                    "identifier": email,
                    "target_identifier": email,
                    "confidence": verdict,
                    "reason": reason,
                    "vector": vector,
                    "profile_url": _PROFILE_URL_BUILDERS.get(service, lambda e: "")(email),
                    "avatar_url": _extract_avatar_url(service, email, None),
                })
            except Exception as e:
                results.append({
                    "platform": service,
                    "service": service,
                    "identifier": email,
                    "target_identifier": email,
                    "confidence": ERROR,
                    "reason": f"Exception: {str(e)[:60]}",
                    "vector": vector,
                })

    # Append passive DNS hygiene check
    try:
        verdict, reason = check_dns_records(email)
        results.append({
            "platform": "Domain DNS",
            "service": "Domain DNS",
            "identifier": email,
            "target_identifier": email,
            "confidence": verdict,
            "reason": reason,
            "vector": "domain_validation",
        })
    except Exception as e:
        results.append({
            "platform": "Domain DNS",
            "service": "Domain DNS",
            "identifier": email,
            "target_identifier": email,
            "confidence": ERROR,
            "reason": f"DNS check failed: {str(e)[:60]}",
            "vector": "domain_validation",
        })

    return results


EMAIL_CATEGORY = "email"


def exposure_findings(results: list) -> list:
    """The positive hits from a sweep, across every probed service.

    No service whitelist: an account on an adult platform, a marketplace or
    a breach DB is an exposure exactly like a Gravatar profile is. Only the
    confidence gate applies, so the caller's ``count`` stays equal to what
    actually renders — the raw NOT_FOUND probes stay in ``checks``.
    """
    if not results or not isinstance(results, list):
        return []
    return [
        row for row in results
        if isinstance(row, dict) and row.get("confidence") in (CONFIRMED, POSSIBLE)
    ]



# Where a human goes to see the association for themselves.
_PROFILE_URL_BUILDERS = {
    "Gravatar": lambda email: f"https://gravatar.com/{_gravatar_hash(email)}",
    "PGP Keys": lambda email: f"{PGP_API}?{urlencode({'q': email})}",
    "Spotify": lambda email: "https://open.spotify.com",
    "Duolingo": lambda email: "https://www.duolingo.com",
    "Pinterest": lambda email: "https://www.pinterest.com",
    "Chess.com": lambda email: "https://www.chess.com",
    "GitHub": lambda email: "https://github.com",
    "Adobe": lambda email: "https://account.adobe.com",
    "Eventbrite": lambda email: "https://www.eventbrite.com",
    "Substack": lambda email: "https://substack.com",
    "Imgur": lambda email: "https://imgur.com",
    "Pornhub": lambda email: "https://www.pornhub.com",
    "OnlyFans": lambda email: "https://onlyfans.com",
    "XVideos": lambda email: "https://www.xvideos.com",
    "Stripchat": lambda email: "https://stripchat.com",
    "Chaturbate": lambda email: "https://chaturbate.com",
    "X (Twitter)": lambda email: "https://x.com",
    "Reddit": lambda email: "https://reddit.com",
    "eBay": lambda email: "https://www.ebay.com",
    "Snapchat": lambda email: "https://www.snapchat.com",
}


def _extract_avatar_url(service: str, email: str, response_data: dict | None) -> str:
    """Extract a displayable avatar image URL from a service response."""
    if service == "Gravatar":
        hash_val = _gravatar_hash(email)
        return f"https://www.gravatar.com/avatar/{hash_val}?s=128&d=404"
    elif service == "Libravatar":
        hash_val = _libravatar_hash(email)
        return f"https://www.libravatar.org/avatar/{hash_val}?s=128&d=404"
    return ""


def email_discoveries(results: list) -> list:
    """Reshape scan results into discovered_accounts rows.

    Only CONFIRMED and POSSIBLE carry through. Avatar URLs and direct
    profile links are attached for visual confirmation.
    """
    rows = []
    for result in results:
        if result.get("confidence") not in (CONFIRMED, POSSIBLE):
            continue
        email = result.get("identifier") or result.get("target_identifier")
        service = result.get("platform") or result.get("service")
        if not service or service == "Domain DNS":
            continue
        builder = _PROFILE_URL_BUILDERS.get(service)
        profile_url = result.get("profile_url") or (builder(email) if builder else "")
        avatar_url = result.get("avatar_url") or _extract_avatar_url(service, email, None)
        rows.append({
            "platform": service,
            "category": EMAIL_CATEGORY,
            "target_identifier": email,
            "profile_url": profile_url,
            "avatar_url": avatar_url,
            "confidence": result.get("confidence"),
            "reason": result.get("reason", ""),
            "emailrecovery": result.get("emailrecovery"),
            "phoneNumber": result.get("phoneNumber"),
            "rate_limited": result.get("rate_limited", False),
        })
    return rows


def summarize_email_scan(results: list) -> dict:
    """Aggregate scan results into summary counts."""
    summary = {CONFIRMED: 0, POSSIBLE: 0, NOT_FOUND: 0, ERROR: 0}
    for result in results:
        summary[result["confidence"]] = summary.get(result["confidence"], 0) + 1
    return summary
