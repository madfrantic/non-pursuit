"""
Google "dork" search-link builder.

A dork is just a search query built from Google's own operators (site:, OR,
quoted phrases) to narrow results to specific broker sites — no API key, no
scraping, just a smarter URL. This is a real, working search, unlike the
simulated exposure numbers elsewhere in the app.

Broker domains are pulled from brokers.csv's own search_url column (via
domain_from_url), so this stays in sync automatically instead of hardcoding
a separate list of broker domains that could drift out of date.
"""
from urllib.parse import quote_plus, urlparse


def domain_from_url(url: str) -> str:
    """Extract a bare domain ('spokeo.com') from a full URL, stripping www."""
    if not url:
        return ""
    netloc = urlparse(url).netloc
    return netloc[4:] if netloc.startswith("www.") else netloc


def _build_query(domains: list, name: str, location: str) -> str:
    site_clause = " OR ".join(f"site:{d}" for d in domains if d)
    parts = [p for p in [site_clause] if p]
    if name:
        parts.append(f'"{name}"')
    if location:
        parts.append(f'"{location}"')
    return " ".join(parts)


def build_combined_dork_url(name: str, location: str, domains: list) -> str:
    """One Google search restricted to every given broker domain at once."""
    query = _build_query(domains, name, location)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def build_broker_dork_url(name: str, location: str, domain: str) -> str:
    """A Google search targeted at just one broker's domain."""
    return build_combined_dork_url(name, location, [domain])
