"""
Censys, Shodan, Maltego and Epieos.

These four are in the catalog as fixed records rather than as scrapes. Shodan
and Censys are search engines whose terms forbid scraping the site; Maltego's
value is in its transform catalog, which is licensed; Epieos exposes no API.
For all four the honest options were "describe it from public documentation"
or "leave it out", and leaving out four of the best-known tools in the field
would make the catalog misleading by omission.

The entries are hardcoded and therefore never go stale on their own. They are
short and factual for that reason -- a claim about pricing or capability here
cannot be refreshed by re-running the import, so nothing is asserted that is
likely to change.
"""
from __future__ import annotations

from typing import Any, Dict, List

STATIC_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "Censys",
        "description": "Internet-wide scanning and asset search platform",
        "url": "https://search.censys.io/",
        "source": "Censys",
        "source_id": "censys",
        "categories": ["Network Infrastructure OSINT"],
        "api_available": True,
        "api_docs_url": "https://docs.censys.com/",
        "documentation_url": "https://docs.censys.com/",
        "access_type": "freemium",
        "status": "live",
        "input_type": "Domain, IP, Certificate",
        "output_type": "Host and certificate records",
        "opsec": "passive",
        "opsec_note": "Queries Censys' own scan data; the target is not contacted.",
        "registration_required": True,
        "local_install": False,
        "best_for": "Finding exposed hosts, services and TLS certificates for a domain",
    },
    {
        "name": "Shodan",
        "description": "Search engine for internet-connected devices",
        "url": "https://www.shodan.io/",
        "source": "Shodan",
        "source_id": "shodan",
        "categories": ["Network Infrastructure OSINT"],
        "api_available": True,
        "api_docs_url": "https://developer.shodan.io/api",
        "documentation_url": "https://help.shodan.io/",
        "access_type": "freemium",
        "status": "live",
        "input_type": "IP, Domain, Service banner",
        "output_type": "Device and service records",
        "opsec": "passive",
        "opsec_note": "Serves Shodan's own scan results; no packets reach the target.",
        "registration_required": True,
        "local_install": False,
        "best_for": "Identifying internet-facing devices and services by banner or IP",
    },
    {
        "name": "Maltego",
        "description": "OSINT and cyber investigations platform",
        "url": "https://www.maltego.com/",
        "source": "Maltego",
        "source_id": "maltego",
        "categories": ["Foundational OSINT Tools", "Cyberthreat Intelligence OSINT"],
        "documentation_url": "https://docs.maltego.com/",
        "api_available": True,
        "api_docs_url": "https://docs.maltego.com/support/solutions/15000017605",
        "access_type": "freemium",
        "status": "live",
        "input_type": "Entity (person, domain, IP, email)",
        "output_type": "Link-analysis graph",
        "opsec": "active",
        "opsec_note": "OPSEC depends entirely on which transforms are run; some "
                      "query the target directly.",
        "registration_required": True,
        "local_install": True,
        "best_for": "Link analysis and relationship mapping across entities",
    },
    {
        "name": "Epieos",
        "description": "Email and phone reverse lookup OSINT tool",
        "url": "https://epieos.com/",
        "source": "Epieos",
        "source_id": "epieos",
        "categories": ["Email Address OSINT", "Phone Number OSINT"],
        "access_type": "free",
        "status": "live",
        "input_type": "Email, Phone",
        "output_type": "Linked accounts and profile data",
        "opsec": "passive",
        "opsec_note": "Runs lookups server-side; no notification is sent to the "
                      "address being checked.",
        "registration_required": False,
        "local_install": False,
        "api_available": False,
        "best_for": "Resolving an email or phone number to linked public accounts",
    },
]


def fetch(client=None) -> List[Dict[str, Any]]:
    """The four static entries. Takes a client for interface symmetry only."""
    return [dict(entry) for entry in STATIC_TOOLS]
