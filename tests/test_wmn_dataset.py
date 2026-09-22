"""
Coverage for site selection: the full sweep is the default, LinkedIn is
present despite being absent upstream, and NSFW stays out regardless.
"""
from wmn_dataset import EXTRA_SITES, NSFW_CATEGORY, select_sites

DATASET = {
    "sites": [
        {"name": "Reddit", "uri_check": "https://reddit.com/user/{account}", "cat": "social"},
        {"name": "Obscure Forum", "uri_check": "https://obscure.example/{account}", "cat": "misc"},
        {"name": "Adult Site", "uri_check": "https://nsfw.example/{account}", "cat": NSFW_CATEGORY},
    ]
}


def _names(sites):
    return {s["name"] for s in sites}


def test_default_selection_is_the_full_sweep():
    """The default must not be the curated subset -- a footprint tool
    whose default answer covers a fraction of what it knows about is
    reporting a floor as a finding."""
    names = _names(select_sites(DATASET))
    assert "Obscure Forum" in names


def test_linkedin_is_in_the_pool_despite_being_absent_upstream():
    assert "LinkedIn" in _names(select_sites(DATASET))
    assert "LinkedIn" in _names(select_sites(DATASET, deep=False))


def test_linkedin_declares_waf_protection_and_no_exists_string():
    """Both are load-bearing: WAF routes a refusal to manual review, and
    no e_string caps it at POSSIBLE so a scrape can never assert
    ownership."""
    linkedin = next(s for s in EXTRA_SITES if s["name"] == "LinkedIn")
    assert "WAF" in linkedin["protection"]
    assert not linkedin["e_string"]


def test_nsfw_stays_excluded_from_the_full_sweep():
    assert "Adult Site" not in _names(select_sites(DATASET))


def test_nsfw_included_only_when_explicitly_asked_for():
    assert "Adult Site" in _names(select_sites(DATASET, include_nsfw=True))


def test_category_filter_returns_only_that_category():
    assert _names(select_sites(DATASET, categories=["social"])) == {"Reddit"}
