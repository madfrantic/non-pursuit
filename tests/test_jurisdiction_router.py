"""
Coverage for statutory-framework routing.

Guessing the wrong jurisdiction is worse than citing none: a letter that
invokes a statute that doesn't reach the recipient invites a one-line
rejection. These tests pin the routing table itself (Phase 4/6A rule-based
verification for a change to letter-generation logic) so a future edit that
silently reorders precedence or drops a country code fails loudly here
instead of in a real letter.
"""
import pytest

import jurisdiction_router as router
from letter_compiler import available_templates


def _profile(**overrides):
    base = {"country": "US", "state": ""}
    base.update(overrides)
    return base


# --- CA -> CCPA --------------------------------------------------------

def test_california_routes_to_ccpa():
    assert router.route_template(_profile(state="CA")) == router.CCPA


def test_california_lowercase_state_still_routes_to_ccpa():
    assert router.route_template(_profile(state="ca")) == router.CCPA


# --- NY -> hybrid --------------------------------------------------------

def test_new_york_routes_to_ny_hybrid():
    assert router.route_template(_profile(state="NY")) == router.NY_HYBRID


# --- EU/UK -> GDPR ---------------------------------------------------------

@pytest.mark.parametrize("country", ["DE", "FR", "IE", "ES", "IT", "NL", "IS", "LI", "NO"])
def test_eu_eea_country_routes_to_gdpr(country):
    assert router.route_template(_profile(country=country, state="")) == router.GDPR


@pytest.mark.parametrize("country", ["GB", "UK"])
def test_uk_routes_to_gdpr(country):
    assert router.route_template(_profile(country=country, state="")) == router.GDPR


def test_gdpr_outranks_us_state_when_both_present():
    """A GDPR country code takes precedence over a stale US state field --
    someone who has moved abroad is no longer covered by CCPA."""
    assert router.route_template(_profile(country="DE", state="CA")) == router.GDPR


# --- fallback --------------------------------------------------------------

def test_unmatched_us_state_falls_back_to_generic():
    assert router.route_template(_profile(state="TX")) == router.GENERIC


def test_non_gdpr_non_us_country_falls_back_to_generic():
    assert router.route_template(_profile(country="JP", state="")) == router.GENERIC


def test_empty_profile_falls_back_to_generic():
    assert router.route_template({}) == router.GENERIC


def test_none_profile_falls_back_to_generic():
    assert router.route_template(None) == router.GENERIC


def test_missing_country_defaults_to_us_routing():
    """A profile saved before the country field existed has no `country`
    key at all -- it must still route off state, not fall through to
    generic just because the key is absent."""
    assert router.route_template({"state": "CA"}) == router.CCPA


# --- template registry stays in sync with the router ------------------------

def test_every_jurisdiction_template_type_has_a_compiled_template():
    templates = available_templates()
    for jurisdiction in router.available_jurisdictions():
        assert jurisdiction.template_type in templates


def test_precedence_order_is_gdpr_ccpa_ny_generic():
    assert router.PRECEDENCE == (router.GDPR, router.CCPA, router.NY_HYBRID, router.GENERIC)


# --- lookup helpers ----------------------------------------------------

def test_get_returns_matching_jurisdiction():
    assert router.get(router.NY_HYBRID).template_type == router.NY_HYBRID


def test_get_unknown_template_type_raises():
    with pytest.raises(ValueError):
        router.get("not_a_jurisdiction")


def test_gdpr_country_codes_includes_germany_and_uk():
    codes = router.gdpr_country_codes()
    assert "DE" in codes
    assert "GB" in codes
