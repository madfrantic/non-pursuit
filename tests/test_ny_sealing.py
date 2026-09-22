"""Four-stage sealing pipeline: classify -> screen -> merge -> fill.

The regression tests that matter most here are the two clock tests. The
previous implementation ran every charge level through one waiting-period
table and reported the answer as Clean Slate, which meant a violation came
back as a full automatic seal and the ten-year petition clock did not exist
at all. `test_violation_is_160_55_not_clean_slate` and
`test_petition_and_clean_slate_clocks_diverge_on_identical_facts` are the
two that fail if that ever regresses.
"""
from datetime import date

import pytest

import ny_sealing as ns


def case(**overrides):
    value = {
        "case_metadata": {
            "docket_or_indictment_no": "2014NY001234",
            "court_name": "NY County Criminal Court",
            "county": "New York",
        },
        "offense_details": {
            "charge_level": ns.LEVEL_MISDEMEANOR,
            "penal_law_section": "PL 155.25",
            "offense_description": "Petit Larceny",
            "felony_class": None,
        },
        "timeline_inputs": {
            "sentencing_date": date(2020, 5, 15),
            "incarceration_served": False,
            "release_date": None,
            "probation_parole_completed": True,
        },
        "current_status_flags": {
            "has_pending_ny_charges": False,
            "has_pending_out_of_state_felony": False,
            "subsequent_conviction_date": None,
            "total_convictions": 1,
            "total_felony_convictions": 0,
        },
    }
    for section, values in overrides.items():
        value[section].update(values)
    return value


# --- Stage 1: classify ----------------------------------------------------

@pytest.mark.parametrize("citation,expected", [
    ("PL 155.25", "155"),
    ("PL 130.65", "130"),
    ("155.25", "155"),
    ("P.L. 220.06", "220"),
    ("§ 263.15", "263"),
    ("", None),
    ("not a citation", None),
])
def test_article_is_parsed_from_the_citation(citation, expected):
    assert ns.classify({"charge_level": ns.LEVEL_MISDEMEANOR,
                        "penal_law_section": citation}).article == expected


def test_article_130_citation_infers_a_sex_offense_and_says_so():
    charge = ns.classify({"charge_level": ns.LEVEL_FELONY,
                          "penal_law_section": "PL 130.65"})
    assert charge.is_sex_offense
    assert charge.requires_sora
    assert any("130" in note for note in charge.classification_notes)


def test_an_explicit_user_flag_beats_the_inferred_one():
    """Someone entering the charge by hand knows more than the regex does."""
    charge = ns.classify({"charge_level": ns.LEVEL_FELONY,
                          "penal_law_section": "PL 130.65",
                          "is_sex_offense": False})
    assert charge.is_sex_offense is False


def test_felony_class_is_dropped_for_non_felonies():
    charge = ns.classify({"charge_level": ns.LEVEL_MISDEMEANOR, "felony_class": "A"})
    assert charge.felony_class is None
    assert charge.is_class_a_felony is False


def test_class_a_drug_felony_is_distinguished_from_other_class_a():
    drug = ns.classify({"charge_level": ns.LEVEL_FELONY, "felony_class": "A",
                        "penal_law_section": "PL 220.43"})
    other = ns.classify({"charge_level": ns.LEVEL_FELONY, "felony_class": "A",
                         "penal_law_section": "PL 125.25"})
    assert drug.is_class_a_drug_felony
    assert not other.is_class_a_drug_felony


# --- Stage 2: the two regressions ----------------------------------------

def test_violation_is_160_55_not_clean_slate():
    """The original bug: a violation screened as a full Clean Slate seal.

    Violations are CPL 160.55, they seal only partially, and Clean Slate
    must decline them outright rather than lending them its clock.
    """
    result = ns.screen(
        case(offense_details={"charge_level": ns.LEVEL_VIOLATION,
                              "penal_law_section": "PL 240.20"}),
        as_of=date(2026, 8, 22))

    clean_slate = result.by_statute(ns.CPL_160_57)
    assert clean_slate.status == ns.STATUS_NOT_APPLICABLE
    assert clean_slate.threshold_date is None

    violation = result.by_statute(ns.CPL_160_55)
    assert violation.status == ns.STATUS_SEALED
    assert violation.seal_scope == ns.SCOPE_PARTIAL
    assert result.headline.statute == ns.CPL_160_55
    assert any("court file is not" in note for note in violation.notes)


def test_petition_and_clean_slate_clocks_diverge_on_identical_facts():
    """Clean Slate runs from release; CPL 160.59 excludes time served.

    Two years inside must move the two thresholds apart by exactly those
    730 days, in opposite directions from the sentencing date. Sharing a
    date helper between them is what this test exists to prevent.
    """
    facts = case(
        offense_details={"charge_level": ns.LEVEL_FELONY, "felony_class": "D"},
        timeline_inputs={"sentencing_date": date(2014, 1, 1),
                         "incarceration_served": True,
                         "release_date": date(2016, 1, 1)},
        current_status_flags={"total_convictions": 1, "total_felony_convictions": 1})
    result = ns.screen(facts, as_of=date(2026, 8, 22))

    clean_slate = result.by_statute(ns.CPL_160_57)
    petition = result.by_statute(ns.CPL_160_59)

    assert clean_slate.start_date == date(2016, 1, 1)      # release
    assert clean_slate.threshold_date == date(2024, 1, 1)  # +8 years
    assert petition.start_date == date(2014, 1, 1)         # sentencing
    assert petition.threshold_date == date(2025, 12, 31)   # +10 years +730 days
    assert (petition.threshold_date - clean_slate.threshold_date).days == 730
    assert any("excluded" in note for note in petition.notes)


# --- Stage 2: pathway behaviour ------------------------------------------

def test_non_conviction_seals_under_160_50_only():
    result = ns.screen(
        case(offense_details={"charge_level": ns.LEVEL_NON_CONVICTION}),
        as_of=date(2026, 8, 22))
    assert result.by_statute(ns.CPL_160_50).status == ns.STATUS_SEALED
    for statute in (ns.CPL_160_55, ns.CPL_160_57, ns.CPL_160_59):
        assert result.by_statute(statute).status == ns.STATUS_NOT_APPLICABLE


@pytest.mark.parametrize("citation,flag", [
    ("VTL 1192.1", "is_dwai"),
    ("PL 240.37", "is_loitering_prostitution"),
])
def test_160_55_carve_outs_are_disqualified(citation, flag):
    result = ns.screen(
        case(offense_details={"charge_level": ns.LEVEL_VIOLATION,
                              "penal_law_section": citation}),
        as_of=date(2026, 8, 22))
    assert result.by_statute(ns.CPL_160_55).status == ns.STATUS_DISQUALIFIED


def test_conditional_discharge_holds_the_160_55_seal_for_a_year():
    payload = case(
        offense_details={"charge_level": ns.LEVEL_VIOLATION, "penal_law_section": "PL 240.20"},
        timeline_inputs={"sentencing_date": date(2026, 1, 1),
                         "conditional_discharge_imposed": True})
    during = ns.screen(payload, as_of=date(2026, 6, 1)).by_statute(ns.CPL_160_55)
    after = ns.screen(payload, as_of=date(2027, 1, 1)).by_statute(ns.CPL_160_55)
    assert during.status == ns.STATUS_PENDING
    assert after.status == ns.STATUS_SEALED


def test_misdemeanor_uses_the_three_year_clean_slate_clock():
    result = ns.screen(case(), as_of=date(2023, 5, 15)).by_statute(ns.CPL_160_57)
    assert result.status == ns.STATUS_SEALED
    assert result.threshold_date == date(2023, 5, 15)


def test_clean_slate_seals_a_class_a_drug_felony_but_not_a_class_a_homicide():
    drug = ns.screen(case(
        offense_details={"charge_level": ns.LEVEL_FELONY, "felony_class": "A",
                         "penal_law_section": "PL 220.43"},
        timeline_inputs={"sentencing_date": date(2010, 1, 1)}),
        as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_57)
    homicide = ns.screen(case(
        offense_details={"charge_level": ns.LEVEL_FELONY, "felony_class": "A",
                         "penal_law_section": "PL 125.25"},
        timeline_inputs={"sentencing_date": date(2010, 1, 1)}),
        as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_57)
    assert drug.status == ns.STATUS_SEALED
    assert homicide.status == ns.STATUS_DISQUALIFIED


def test_out_of_state_conviction_is_outside_clean_slate():
    result = ns.screen(
        case(offense_details={"is_out_of_state": True}), as_of=date(2026, 8, 22))
    assert result.by_statute(ns.CPL_160_57).status == ns.STATUS_DISQUALIFIED


def test_pending_charge_blocks_both_clocked_pathways():
    result = ns.screen(
        case(current_status_flags={"has_pending_ny_charges": True}),
        as_of=date(2036, 1, 1))
    assert result.by_statute(ns.CPL_160_57).status == ns.STATUS_PENDING
    assert result.by_statute(ns.CPL_160_59).status == ns.STATUS_PENDING


def test_active_supervision_blocks_clean_slate_but_not_the_petition():
    """CPL 160.59 has no supervision bar; Clean Slate does."""
    result = ns.screen(
        case(timeline_inputs={"probation_parole_completed": False,
                              "sentencing_date": date(2010, 1, 1)}),
        as_of=date(2026, 8, 22))
    assert result.by_statute(ns.CPL_160_57).status == ns.STATUS_PENDING
    assert result.by_statute(ns.CPL_160_59).status == ns.STATUS_ELIGIBLE_TO_PETITION


def test_subsequent_conviction_restarts_the_clock():
    result = ns.screen(
        case(current_status_flags={"subsequent_conviction_date": date(2022, 1, 1)}),
        as_of=date(2024, 1, 1)).by_statute(ns.CPL_160_57)
    assert result.status == ns.STATUS_PENDING
    assert result.start_date == date(2022, 1, 1)


@pytest.mark.parametrize("flags,fragment", [
    ({"total_convictions": 3}, "at most two convictions"),
    ({"total_felony_convictions": 2, "total_convictions": 2}, "at most one felony"),
])
def test_160_59_conviction_count_limits(flags, fragment):
    payload = case(
        offense_details={"charge_level": ns.LEVEL_FELONY, "felony_class": "D"},
        timeline_inputs={"sentencing_date": date(2005, 1, 1)},
        current_status_flags=flags)
    result = ns.screen(payload, as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_59)
    assert result.status == ns.STATUS_DISQUALIFIED
    assert fragment in result.reason


@pytest.mark.parametrize("offense", [
    {"penal_law_section": "PL 130.65"},
    {"penal_law_section": "PL 263.15"},
    {"charge_level": ns.LEVEL_FELONY, "penal_law_section": "PL 125.25"},
    {"charge_level": ns.LEVEL_FELONY, "is_violent_felony": True},
    {"charge_level": ns.LEVEL_FELONY, "felony_class": "A"},
    {"requires_sora": True},
])
def test_160_59_statutory_exclusions_disqualify(offense):
    payload = case(offense_details=offense,
                   timeline_inputs={"sentencing_date": date(2000, 1, 1)})
    result = ns.screen(payload, as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_59)
    assert result.status == ns.STATUS_DISQUALIFIED


def test_160_58_needs_a_drug_charge_and_completed_treatment():
    not_drugs = ns.screen(case(), as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_58)
    assert not_drugs.status == ns.STATUS_NOT_APPLICABLE

    untreated = ns.screen(
        case(offense_details={"penal_law_section": "PL 220.06"}),
        as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_58)
    assert untreated.status == ns.STATUS_PENDING

    treated = ns.screen(
        case(offense_details={"penal_law_section": "PL 220.06"},
             timeline_inputs={"diversion_or_dtap_completed": True}),
        as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_58)
    assert treated.status == ns.STATUS_ELIGIBLE_TO_PETITION
    assert treated.seal_scope == ns.SCOPE_CONDITIONAL


def test_pre_clean_slate_conviction_carries_the_oca_window_note():
    result = ns.screen(
        case(timeline_inputs={"sentencing_date": date(2015, 1, 1)}),
        as_of=date(2026, 8, 22)).by_statute(ns.CPL_160_57)
    assert any("2027-11-16" in note for note in result.notes)


def test_missing_charge_level_makes_every_pathway_not_applicable():
    result = ns.screen(case(offense_details={"charge_level": "Nonsense"}),
                       as_of=date(2026, 8, 22))
    assert all(p.status == ns.STATUS_NOT_APPLICABLE for p in result.pathways)
    assert result.headline is None


def test_iso_date_strings_from_a_json_payload_are_accepted():
    result = ns.screen(case(timeline_inputs={"sentencing_date": "2020-05-15"}),
                       as_of=date(2023, 5, 15))
    assert result.by_statute(ns.CPL_160_57).status == ns.STATUS_SEALED


def test_a_full_automatic_seal_outranks_a_petition_in_the_headline():
    result = ns.screen(case(timeline_inputs={"sentencing_date": date(2005, 1, 1)}),
                       as_of=date(2026, 8, 22))
    assert result.by_statute(ns.CPL_160_59).status == ns.STATUS_ELIGIBLE_TO_PETITION
    assert result.headline.statute == ns.CPL_160_57
    assert result.headline.mechanism == ns.MECHANISM_AUTOMATIC


def test_every_clock_is_flagged_for_human_signoff():
    """docs/SAFETY_BOUNDARIES.md HVZ: no pathway may self-approve its own deadline math."""
    assert ns.SIGNED_OFF_PATHWAYS == frozenset()
    result = ns.screen(case(), as_of=date(2026, 8, 22))
    assert all(p.requires_human_signoff for p in result.applicable)
    assert result.as_dict()["requires_human_signoff"] is True


# --- Stage 3: merge -------------------------------------------------------

def _record(**kw):
    return ns.build_record(
        {"name": "Jane Roe", "nysid": "NY1234567", "dob": date(1985, 3, 2)},
        kw.pop("cases", [case(timeline_inputs={"sentencing_date": date(2005, 1, 1)})]),
        discretionary_factors=kw.pop("discretionary_factors", "Steady employment since 2016."),
        as_of=kw.pop("as_of", date(2026, 8, 22)), **kw)


def test_venue_is_the_court_of_the_most_serious_conviction():
    misdemeanor = case(
        case_metadata={"court_name": "NY County Criminal Court", "county": "New York"},
        timeline_inputs={"sentencing_date": date(2005, 1, 1)})
    felony = case(
        case_metadata={"court_name": "Kings County Supreme Court", "county": "Kings"},
        offense_details={"charge_level": ns.LEVEL_FELONY, "felony_class": "D"},
        timeline_inputs={"sentencing_date": date(2004, 1, 1)},
        current_status_flags={"total_felony_convictions": 1, "total_convictions": 2})
    record = _record(cases=[misdemeanor, felony])
    assert record.venue == "Kings County Supreme Court"
    assert record.service_counties == ("Kings", "New York")


def test_same_class_convictions_break_the_venue_tie_by_recency():
    older = case(case_metadata={"court_name": "Older Court", "county": "Bronx"},
                 timeline_inputs={"sentencing_date": date(2004, 1, 1),
                                  "conviction_date": date(2004, 1, 1)})
    newer = case(case_metadata={"court_name": "Newer Court", "county": "Queens"},
                 timeline_inputs={"sentencing_date": date(2006, 1, 1),
                                  "conviction_date": date(2006, 1, 1)})
    assert _record(cases=[older, newer]).venue == "Newer Court"


def test_more_than_two_cases_blocks_filing():
    payload = case(timeline_inputs={"sentencing_date": date(2005, 1, 1)})
    record = _record(cases=[payload, payload, payload])
    assert not record.ready_to_file
    assert any("at most two convictions" in issue for issue in record.blocking_issues)


def test_two_felonies_block_filing():
    felony = case(
        offense_details={"charge_level": ns.LEVEL_FELONY, "felony_class": "D"},
        timeline_inputs={"sentencing_date": date(2004, 1, 1)},
        current_status_flags={"total_felony_convictions": 1, "total_convictions": 2})
    record = _record(cases=[felony, felony])
    assert any("at most one felony" in issue for issue in record.blocking_issues)


def test_the_affidavit_narrative_is_required():
    record = _record(discretionary_factors="")
    assert not record.ready_to_file
    assert any("reasons why the court should grant" in i for i in record.blocking_issues)


def test_a_complete_record_is_ready_to_file():
    record = _record()
    assert record.ready_to_file
    assert record.blocking_issues == ()
    assert len(record.eligible_cases) == 1


def test_a_disqualified_case_surfaces_as_a_blocking_issue():
    record = _record(cases=[case(
        offense_details={"charge_level": ns.LEVEL_FELONY, "is_violent_felony": True},
        timeline_inputs={"sentencing_date": date(2000, 1, 1)})])
    assert not record.ready_to_file
    assert record.eligible_cases == ()


# --- Stage 4: fill --------------------------------------------------------

def test_fill_motion_returns_a_pdf():
    output = ns.fill_motion(_record())
    assert isinstance(output, bytes)
    assert output.startswith(b"%PDF-")
    assert len(output) > 1500


def test_fill_motion_survives_an_empty_record():
    """A half-finished intake must still render something to check against."""
    output = ns.fill_motion(ns.build_record({}, []))
    assert output.startswith(b"%PDF-")


def test_fill_motion_handles_characters_outside_latin_1():
    record = ns.build_record(
        {"name": "Renée O’Brien — 北京"},
        [case(timeline_inputs={"sentencing_date": date(2005, 1, 1)})],
        discretionary_factors="Employed since 2016 — “steady”.")
    assert ns.fill_motion(record).startswith(b"%PDF-")


def test_the_motion_never_claims_to_be_the_court_form():
    assert "NOT A COURT FORM" in ns.DRAFT_WATERMARK
    assert len(ns.AFFIDAVIT_EXCLUSIONS) == 8


# --- audit instructions ---------------------------------------------------

def test_audit_instructions_warn_when_the_seal_is_only_partial():
    screening = ns.screen(
        case(offense_details={"charge_level": ns.LEVEL_VIOLATION,
                              "penal_law_section": "PL 240.20"}),
        as_of=date(2026, 8, 22))
    lines = ns.audit_instructions(["Checkr"], screening)
    assert any("partially" in line for line in lines)
    assert any("Checkr" in line for line in lines)


def test_audit_instructions_still_work_without_a_screening():
    lines = ns.audit_instructions(["Sterling", "HireRight"])
    assert len(lines) == 4
