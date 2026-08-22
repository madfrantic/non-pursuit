"""
Coverage for the competitor dataset and the analysis over it.

The figures here end up in coursework deliverables -- a Mini PRD, an
industry-research writeup, a pitch. Two failure modes matter enough to pin:
comparing an annual price against a monthly one (which inverts the entire
price ranking), and presenting an unverified figure as fact. Both are tested
below.
"""
import pytest

import competitor_analysis as ca


# --- dataset loads and is shaped ------------------------------------------

def test_dataset_loads_every_competitor():
    names = {c.name for c in ca.load_competitors()}
    assert names == {"Optery", "Aura", "OneRep", "HelloPrivacy", "Incogni", "DeleteMe"}


def test_lookup_is_case_insensitive():
    assert ca.get("deleteme").name == "DeleteMe"


def test_unknown_competitor_raises_with_the_valid_names():
    with pytest.raises(ValueError, match="Unknown competitor"):
        ca.get("NotARealService")


def test_rows_with_no_name_are_dropped(tmp_path):
    source = tmp_path / "competitors.json"
    source.write_text('{"competitors": [{"name": "Real"}, {"website": "x"}]}')
    assert [c.name for c in ca.load_competitors(source)] == ["Real"]


def test_an_unknown_field_does_not_break_the_load(tmp_path):
    """The dataset is hand-edited for coursework; one stray key must not kill it."""
    source = tmp_path / "competitors.json"
    source.write_text('{"competitors": [{"name": "Real", "market_share": 0.2}]}')
    assert [c.name for c in ca.load_competitors(source)] == ["Real"]


# --- price normalisation ---------------------------------------------------

def test_annual_pricing_is_converted_to_monthly():
    deleteme = ca.get("DeleteMe")
    low, high = deleteme.monthly_range()
    assert deleteme.pricing_low == 129.0        # sourced figure is preserved
    assert low == pytest.approx(10.75, abs=0.01)
    assert high == pytest.approx(27.42, abs=0.01)


def test_monthly_pricing_passes_through_untouched():
    assert ca.get("Incogni").monthly_range() == (6.49, 12.98)


def test_cheapest_entry_is_not_decided_on_raw_numbers():
    """DeleteMe's 129 is annual. Ranking on the raw field would bury Optery."""
    assert ca.cheapest_entry().name == "Optery"


def test_annual_vendor_is_not_reported_as_the_most_expensive():
    """$129/yr is ~$10.75/mo -- cheaper than Aura, despite the larger number."""
    metrics = ca.comparison_metrics()
    assert metrics["monthly_entry_max"] == pytest.approx(12.0)  # Aura, not DeleteMe
    assert ca.get("DeleteMe").monthly_range()[0] < ca.get("Aura").monthly_range()[0]


# --- metrics ---------------------------------------------------------------

def test_metrics_count_every_competitor():
    assert ca.comparison_metrics()["total_competitors"] == 6


def test_vendors_without_coverage_are_named_not_averaged_as_zero():
    metrics = ca.comparison_metrics()
    assert metrics["coverage_undisclosed"] == ["Aura"]
    # HelloPrivacy has a min but no max, so it is disclosed yet not averaged.
    assert metrics["coverage_avg"] > 0


def test_widest_coverage_is_deleteme():
    assert ca.widest_coverage().name == "DeleteMe"
    assert ca.comparison_metrics()["coverage_max"] == 750


def test_metrics_on_an_empty_set_are_empty_not_a_crash():
    assert ca.comparison_metrics([]) == {}


def test_manual_operators_are_identified():
    assert "DeleteMe" in ca.comparison_metrics()["manual_operators"]


# --- the honesty guard -----------------------------------------------------

def test_low_confidence_rows_are_flagged_for_reverification():
    flagged = {row["name"] for row in ca.needs_reverification()}
    assert "HelloPrivacy" in flagged


def test_every_row_is_flagged_while_the_dataset_predates_2026():
    """The whole dataset is dated 2025-01-01, so nothing in it is citable as-is."""
    flagged = {row["name"] for row in ca.needs_reverification()}
    assert flagged == {c.name for c in ca.load_competitors()}


def test_metrics_carry_the_citation_warning():
    assert "unverified" in ca.comparison_metrics()["citation_warning"]


def test_a_fresh_high_confidence_row_is_not_flagged(tmp_path):
    source = tmp_path / "competitors.json"
    source.write_text(
        '{"competitors": [{"name": "Checked", "confidence": "High",'
        ' "last_updated": "2026-08-01"}]}')
    assert ca.needs_reverification(ca.load_competitors(source)) == []


def test_a_missing_last_updated_counts_as_stale(tmp_path):
    source = tmp_path / "competitors.json"
    source.write_text('{"competitors": [{"name": "Undated", "confidence": "High"}]}')
    flagged = ca.needs_reverification(ca.load_competitors(source))
    assert flagged[0]["reasons"] == ["last updated never"]


# --- positioning gaps ------------------------------------------------------

def test_the_architecture_gap_covers_the_whole_field():
    """The local-first claim is the one that holds against every competitor."""
    architecture = ca.gaps()["architecture"]
    assert len(architecture) == 1
    assert "local" in architecture[0].lower()


def test_gaps_name_the_sub_two_hundred_coverage_vendors():
    coverage = " ".join(ca.gaps()["coverage"])
    assert "Incogni" in coverage       # 180
    assert "OneRep" in coverage        # 199
    assert "HelloPrivacy" in coverage  # publishes a floor of 100 and no ceiling
    assert "DeleteMe" not in coverage  # 750
    assert "Optery" not in coverage    # 320


def test_a_vendor_publishing_only_a_floor_still_counts_as_disclosed():
    """HelloPrivacy says "100+" with no ceiling; that is a number, not a silence."""
    hello = ca.get("HelloPrivacy")
    assert hello.coverage_max is None
    assert hello.claimed_coverage == 100
    assert hello.publishes_coverage() is True
    assert "HelloPrivacy" not in ca.comparison_metrics()["coverage_undisclosed"]


def test_gaps_on_an_empty_set_return_empty_buckets():
    assert ca.gaps([]) == {"architecture": [], "transparency": [], "coverage": [],
                           "cost": [], "automation": []}
