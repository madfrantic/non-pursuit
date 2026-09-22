"""Infrastructure: Certificate Transparency + WHOIS via crt.sh and RDAP."""
import asyncio
import aiohttp
from typing import Any, Dict, List

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY

_log = get_logger("osint_infrastructure")

CRT_SH = "https://crt.sh"
RDAP = "https://rdap.org"


async def _fetch_certificates(domain: str, retries: int = 3) -> List[Dict[str, Any]]:
    """
    Fetch certificates from crt.sh with retry logic (it's flaky).
    Returns staging subdomains, wildcard certs, and issuance dates.
    Falls back to CertSpotter API if crt.sh fails.
    """
    if not domain:
        return []

    for attempt in range(retries):
        try:
            params = {"q": domain, "output": "json"}
            async with aiohttp.ClientSession() as session:
                async with session.get(CRT_SH, params=params,
                                      timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 200:
                        return await r.json() or []
                    elif r.status in (502, 503, 504):
                        if attempt < retries - 1:
                            await asyncio.sleep(2 ** attempt)  # exponential backoff
                            continue
                    else:
                        _log.warning("crt.sh returned %d", r.status)
                        break
        except asyncio.TimeoutError:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            _log.info("crt.sh timeout after %d retries", retries)
        except Exception as exc:
            _log.info("crt.sh error: %s", exc)
            break
            
    _log.info("Falling back to CertSpotter for %s", domain)
    try:
        url = f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json() or []
                    # Transform CertSpotter format to match expected crt.sh format
                    return [
                        {
                            "name_value": "\\n".join(cert.get("dns_names", [])),
                            "issuer_name": cert.get("issuer", {}).get("name", ""),
                            "not_before": cert.get("not_before", ""),
                            "not_after": cert.get("not_after", ""),
                        }
                        for cert in data
                    ]
    except Exception as exc:
        _log.info("CertSpotter fallback error: %s", exc)

    return []


async def _fetch_rdap(domain: str) -> Dict[str, Any]:
    """Fetch RDAP records for domain registrant and status."""
    if not domain:
        return {}

    url = f"{RDAP}/domain/{domain}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as exc:
        _log.info("RDAP error for %s: %s", domain, exc)
    return {}


def _normalize_cert_records(certs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract subdomains and issuance dates from crt.sh response."""
    seen = set()
    normalized = []
    for cert in certs:
        domains = (cert.get("name_value", "") or "").split("\n")
        for d in domains:
            d = d.strip()
            if d and d not in seen:
                seen.add(d)
                normalized.append({
                    "subdomain": d,
                    "issuer": cert.get("issuer_name", ""),
                    "issued_at": cert.get("not_before", ""),
                    "expires_at": cert.get("not_after", ""),
                })
    return normalized


def _normalize_rdap_record(rdap: Dict[str, Any]) -> Dict[str, str]:
    """Extract key fields from RDAP response."""
    if not rdap:
        return {}
    return {
        "domain": rdap.get("ldhName", ""),
        "status": ", ".join(rdap.get("status", [])),
        "registrar": rdap.get("registrar", {}).get("name", "") if isinstance(rdap.get("registrar"), dict) else "",
        "nameservers": ", ".join([ns.get("ldhName", "") for ns in rdap.get("nameservers", [])]),
    }


async def scan_infrastructure(domain: str) -> Dict[str, Any]:
    """Scan CT logs and WHOIS for domain/subdomain exposure."""
    if not domain:
        return {
            "status": STATUS_EMPTY,
            "module": "infrastructure",
            "certificates": [],
            "domain_info": {},
            "error": "No domain provided",
        }

    # Run both queries concurrently
    certs_task = _fetch_certificates(domain)
    rdap_task = _fetch_rdap(domain)
    certs, rdap = await asyncio.gather(certs_task, rdap_task)

    cert_records = _normalize_cert_records(certs)
    rdap_record = _normalize_rdap_record(rdap)

    status = STATUS_SUCCESS if (cert_records or rdap_record) else STATUS_EMPTY
    if not certs and rdap:
        # crt.sh was unavailable but RDAP worked
        status = STATUS_SUCCESS if rdap_record else STATUS_UNAVAILABLE

    return {
        "status": status,
        "module": "infrastructure",
        "certificates": cert_records,
        "domain_info": rdap_record,
        "cert_count": len(cert_records),
    }
