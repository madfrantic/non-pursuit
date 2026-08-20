"""
Offline coverage for the scanner's classification and request building.

No network: classify() is a pure function over a dataset entry plus a
(status, body) pair, which is the whole point of keeping it separate from
the aiohttp plumbing. The two dataset traps documented in
footprint_scanner get explicit regression tests -- both fail silently in
production (as permanent false negatives that look like clean results),
so a test is the only thing that would catch a regression.
"""
import pytest

from footprint_scanner import (
    CONFIRMED,
    ERROR,
    NOT_FOUND,
    POSSIBLE,
    build_request,
    classify,
    discoveries,
    scan_account,
    summarize,
)


def make_site(**overrides):
    site = {
        "name": "ExampleSite",
        "uri_check": "https://example.com/{account}",
        "e_code": 200,
        "e_string": "profile-header",
        "m_code": 404,
        "m_string": "User not found",
        "cat": "social",
    }
    site.update(overrides)
    return site


# --- classification ---------------------------------------------------

def test_confirmed_requires_both_code_and_string():
    verdict, _ = classify(make_site(), 200, "<div class='profile-header'>hi</div>")
    assert verdict == CONFIRMED


def test_not_found_on_missing_string():
    verdict, _ = classify(make_site(), 404, "User not found")
    assert verdict == NOT_FOUND


def test_not_found_on_missing_code_alone():
    verdict, _ = classify(make_site(), 404, "some unrelated body")
    assert verdict == NOT_FOUND


def test_soft_200_without_match_is_possible():
    """A JS-rendered page returns 200 with neither marker present."""
    verdict, reason = classify(make_site(), 200, "<html><body><div id='root'></div></body></html>")
    assert verdict == POSSIBLE
    assert "200" in reason


@pytest.mark.parametrize("status", [403, 503])
def test_waf_challenge_is_possible_not_missing(status):
    """Cloudflare answering instead of the site must not read as a miss --
    the account can exist behind the gate."""
    verdict, reason = classify(make_site(), status, "Attention Required! | Cloudflare")
    assert verdict == POSSIBLE
    assert "WAF" in reason


def test_no_response_is_error():
    verdict, _ = classify(make_site(), None, "")
    assert verdict == ERROR


# --- the two dataset traps -------------------------------------------

def test_empty_missing_string_does_not_swallow_a_real_hit():
    """34 sites define m_string as "". Testing it unguarded makes
    `"" in content` always true, marking every one of them NOT_FOUND
    forever."""
    site = make_site(m_string="", m_code=302)
    verdict, _ = classify(site, 200, "<div class='profile-header'>hi</div>")
    assert verdict == CONFIRMED


def test_missing_code_ignored_when_equal_to_exists_code():
    """173 sites have m_code == e_code, where status alone can't separate
    hit from miss. The missing-code rule has to be skipped or they all
    read as NOT_FOUND."""
    site = make_site(e_code=200, m_code=200, m_string="No such user")
    verdict, _ = classify(site, 200, "<div class='profile-header'>hi</div>")
    assert verdict == CONFIRMED


def test_missing_string_still_vetoes_when_codes_are_equal():
    site = make_site(e_code=200, m_code=200, m_string="No such user")
    verdict, _ = classify(site, 200, "No such user")
    assert verdict == NOT_FOUND


def test_site_without_exists_string_is_possible_not_confirmed():
    """Status-code-only evidence is too thin to assert account ownership."""
    site = make_site(e_string="")
    verdict, _ = classify(site, 200, "anything at all")
    assert verdict == POSSIBLE


# --- request building -------------------------------------------------

def test_build_request_substitutes_account():
    request = build_request(make_site(), "alice")
    assert request["url"] == "https://example.com/alice"
    assert request["method"] == "GET"


def test_build_request_strips_forbidden_characters():
    site = make_site(strip_bad_char=".")
    assert build_request(site, "al.ice")["url"] == "https://example.com/alice"


def test_build_request_uses_post_when_body_present():
    site = make_site(post_body='{"username":"{account}"}')
    request = build_request(site, "alice")
    assert request["method"] == "POST"
    assert request["body"] == '{"username":"alice"}'


def test_build_request_merges_custom_headers():
    site = make_site(headers={"Accept": "application/json"})
    headers = build_request(site, "alice")["headers"]
    assert headers["Accept"] == "application/json"
    assert "User-Agent" in headers


def test_build_request_prefers_pretty_url_for_display():
    """Docker Hub probes an API endpoint but should link the profile."""
    site = make_site(
        uri_check="https://api.example.com/v2/users/{account}",
        uri_pretty="https://example.com/u/{account}",
    )
    request = build_request(site, "alice")
    assert request["url"].startswith("https://api.example.com")
    assert request["display_url"] == "https://example.com/u/alice"


# --- aggregation ------------------------------------------------------

def _result(platform, confidence):
    return {"platform": platform, "confidence": confidence, "category": "social"}


def test_summarize_counts_each_verdict():
    results = [
        _result("A", CONFIRMED), _result("B", CONFIRMED),
        _result("C", POSSIBLE), _result("D", NOT_FOUND), _result("E", ERROR),
    ]
    summary = summarize(results)
    assert summary[CONFIRMED] == 2
    assert summary[POSSIBLE] == 1
    assert summary[NOT_FOUND] == 1
    assert summary[ERROR] == 1


def test_discoveries_drops_misses_and_ranks_confirmed_first():
    results = [
        _result("Zeta", POSSIBLE), _result("Alpha", CONFIRMED),
        _result("Beta", NOT_FOUND), _result("Gamma", ERROR),
    ]
    found = discoveries(results)
    assert [r["platform"] for r in found] == ["Alpha", "Zeta"]


def test_scan_account_short_circuits_without_network():
    """Empty handle or empty site list must not open a session."""
    assert scan_account("", [make_site()]) == []
    assert scan_account("   ", [make_site()]) == []
    assert scan_account("alice", []) == []
