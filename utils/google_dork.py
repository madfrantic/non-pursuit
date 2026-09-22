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


def build_dork_query(name: str, location: str, domains: list) -> str:
    """The raw query string, e.g. 'site:spokeo.com "Jane Doe" "New York, NY"'.

    Public because the Master Dossier shows the query itself, not just a
    link: the point is that the operator can read the string, paste it
    into Google, and verify with their own eyes that a broker still lists
    them before a demand letter goes out. Argument order matches the
    build_*_dork_url helpers below.
    """
    site_clause = " OR ".join(f"site:{d}" for d in domains if d)
    parts = [p for p in [site_clause] if p]
    if name:
        parts.append(f'"{name}"')
    if location:
        parts.append(f'"{location}"')
    return " ".join(parts)


def build_combined_dork_url(name: str, location: str, domains: list) -> str:
    """One Google search restricted to every given broker domain at once."""
    query = build_dork_query(name, location, domains)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def build_broker_dork_url(name: str, location: str, domain: str) -> str:
    """A Google search targeted at just one broker's domain."""
    return build_combined_dork_url(name, location, [domain])
