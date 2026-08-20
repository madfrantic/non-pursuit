from datetime import date

from ny_sealing import STATUS_DISQUALIFIED, STATUS_PENDING, STATUS_SEALED, eligibility


def payload(**overrides):
    value = {
        "offense_details": {"charge_level": "Misdemeanor", "felony_class": None, "is_sex_offense": False, "is_article_220_drug": False},
        "timeline_inputs": {"sentencing_date": date(2020, 5, 15), "incarceration_served": False, "release_date": None, "probation_parole_completed": True},
        "current_status_flags": {"has_pending_ny_charges": False, "has_pending_out_of_state_felony": False, "subsequent_conviction_date": None},
    }
    for section, values in overrides.items():
        value[section].update(values)
    return value


def test_misdemeanor_uses_three_year_clock():
    result = eligibility(payload(), as_of=date(2023, 5, 15))
    assert result["status"] == STATUS_SEALED
    assert result["threshold_date"] == date(2023, 5, 15)


def test_pending_charge_blocks_even_if_clock_elapsed():
    result = eligibility(payload(current_status_flags={"has_pending_ny_charges": True}), as_of=date(2026, 1, 1))
    assert result["status"] == STATUS_PENDING


def test_subsequent_conviction_resets_clock():
    result = eligibility(payload(current_status_flags={"subsequent_conviction_date": date(2022, 1, 1)}), as_of=date(2024, 1, 1))
    assert result["status"] == STATUS_PENDING
    assert result["start_date"] == date(2022, 1, 1)


def test_class_a_non_drug_felony_is_excluded():
    result = eligibility(payload(offense_details={"charge_level": "Felony", "felony_class": "A"}), as_of=date(2035, 1, 1))
    assert result["status"] == STATUS_DISQUALIFIED


def test_missing_sentencing_date_is_pending():
    result = eligibility(payload(timeline_inputs={"sentencing_date": None}), as_of=date(2026, 1, 1))
    assert result["status"] == STATUS_PENDING


def test_iso_dates_from_json_payload_are_supported():
    value = payload(timeline_inputs={"sentencing_date": "2020-05-15"})
    result = eligibility(value, as_of=date(2023, 5, 15))
    assert result["status"] == STATUS_SEALED