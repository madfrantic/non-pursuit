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
    validate_target_url,
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


def test_redirect_requires_review_without_following_it():
    verdict, reason = classify(make_site(), 302, "")
    assert verdict == POSSIBLE
    assert "redirect" in reason


def test_target_url_rejects_non_https_and_private_addresses():
    assert validate_target_url("http://example.com/alice")
    assert validate_target_url("https://127.0.0.1/alice")


def test_malformed_site_becomes_error_without_aborting_scan():
    results = scan_account("alice", [{"name": "Broken site"}])
    assert len(results) == 1
    assert results[0]["confidence"] == ERROR


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


# --- WAF-blocked platforms (LinkedIn) ---------------------------------

def test_linkedin_999_is_manual_review_not_a_miss():
    """LinkedIn answers automated GETs with its own invented HTTP 999.
    Reading that as NOT_FOUND would report 'no LinkedIn account' to
    someone who has one."""
    from footprint_scanner import is_manual_review
    verdict, reason = classify(make_site(), 999, "")
    assert verdict == POSSIBLE
    assert is_manual_review({"reason": reason})


def test_rate_limit_is_not_a_miss():
    verdict, _ = classify(make_site(), 429, "")
    assert verdict == POSSIBLE


@pytest.mark.parametrize("verdict", [ERROR, POSSIBLE])
def test_waf_protected_site_always_degrades_to_manual_review(verdict):
    """A declared-WAF site answers automated checks three different ways
    depending on the network -- a refusal code, a dropped connection, or
    a bare 200 auth wall. All three mean the same thing, so none of them
    may look like a row a future rescan could settle."""
    from footprint_scanner import _degrade_waf_error, is_manual_review
    site = make_site(protection=["WAF"])
    result, reason = _degrade_waf_error(site, verdict, "whatever happened")
    assert result == POSSIBLE
    assert is_manual_review({"reason": reason})


def test_waf_protected_site_keeps_a_genuine_404():
    """A real miss is still a real miss -- the degrade must not turn
    'this handle isn't there' into 'go look yourself'."""
    from footprint_scanner import _degrade_waf_error, is_manual_review
    site = make_site(protection=["WAF"])
    result, reason = _degrade_waf_error(site, NOT_FOUND, "no match (HTTP 404)")
    assert result == NOT_FOUND
    assert not is_manual_review({"reason": reason})


def test_unprotected_site_failure_stays_an_error():
    """Only declared-WAF sites get the benefit of the doubt."""
    from footprint_scanner import _degrade_waf_error
    verdict, _ = _degrade_waf_error(make_site(), ERROR, "timed out")
    assert verdict == ERROR


def test_manual_review_is_false_for_an_ordinary_ambiguous_row():
    from footprint_scanner import is_manual_review
    _, reason = classify(make_site(), 200, "<div id='root'></div>")
    assert not is_manual_review({"reason": reason})
