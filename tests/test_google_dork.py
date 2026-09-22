"""Query-string coverage for the dork builder.

The URL helpers were already exercised indirectly through the results
page; what was untested is the raw query string, which is now rendered
verbatim in the Master Dossier. A malformed operator here is visible to
the user and gets pasted straight into Google, so the exact shape of the
string matters.
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UTILS = os.path.join(ROOT, "utils")
if UTILS not in sys.path:
    sys.path.insert(0, UTILS)

from google_dork import (  # noqa: E402
    build_broker_dork_url,
    build_combined_dork_url,
    build_dork_query,
    domain_from_url,
)


@pytest.mark.parametrize("url,expected", [
    ("https://www.spokeo.com/", "spokeo.com"),
    ("https://mylife.com/ccpa/index.pubview", "mylife.com"),
    ("", ""),
])
def test_domain_from_url_strips_www_and_path(url, expected):
    assert domain_from_url(url) == expected


def test_single_domain_query_quotes_the_name():
    assert build_dork_query("Jane Doe", "", ["spokeo.com"]) == 'site:spokeo.com "Jane Doe"'


def test_location_is_quoted_as_its_own_phrase():
    query = build_dork_query("Jane Doe", "New York, NY", ["spokeo.com"])
    assert query == 'site:spokeo.com "Jane Doe" "New York, NY"'


def test_multiple_domains_are_or_joined():
    query = build_dork_query("Jane Doe", "", ["spokeo.com", "mylife.com"])
    assert query == 'site:spokeo.com OR site:mylife.com "Jane Doe"'


def test_blank_domains_are_dropped_not_rendered_as_empty_operators():
    query = build_dork_query("Jane Doe", "", ["spokeo.com", "", None])
    assert query == 'site:spokeo.com "Jane Doe"'


def test_missing_name_yields_a_site_only_query():
    assert build_dork_query("", "", ["spokeo.com"]) == "site:spokeo.com"


def test_urls_percent_encode_the_query():
    url = build_broker_dork_url("Jane Doe", "", "spokeo.com")
    assert url.startswith("https://www.google.com/search?q=")
    assert "site%3Aspokeo.com" in url
    assert "%22Jane+Doe%22" in url


def test_combined_url_covers_every_domain():
    url = build_combined_dork_url("Jane Doe", "", ["spokeo.com", "mylife.com"])
    assert "site%3Aspokeo.com" in url and "site%3Amylife.com" in url
