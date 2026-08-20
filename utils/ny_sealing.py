"""Deterministic intake calculations for New York record-sealing guidance.

This module applies the product's configured screening rules to user-provided
paperwork. It is guidance, not a legal determination; ambiguous records should
be reviewed against the Certificate of Disposition and by counsel.
"""
from datetime import date

STATUS_SEALED = "Sealed"
STATUS_PENDING = "Pending Date"
STATUS_DISQUALIFIED = "Disqualified"

IMPLEMENTATION_WINDOW_END = date(2027, 11, 16)


def _add_years(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, day=28)


def _coerce_date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def eligibility(payload: dict, as_of: date | None = None) -> dict:
    """Return an explainable screening result from a normalized payload."""
    as_of = as_of or date.today()
    offense = payload.get("offense_details", {})
    timeline = payload.get("timeline_inputs", {})
    flags = payload.get("current_status_flags", {})

    charge_level = offense.get("charge_level")
    if charge_level not in {"Violation", "Misdemeanor", "Felony"}:
        return {"status": STATUS_DISQUALIFIED, "reason": "Charge level is missing or unsupported."}

    if offense.get("is_sex_offense"):
        return {"status": STATUS_DISQUALIFIED, "reason": "Sex-offense exclusions require separate legal review."}

    if charge_level == "Felony" and offense.get("felony_class") == "A" and not offense.get("is_article_220_drug"):
        return {"status": STATUS_DISQUALIFIED, "reason": "Class A non-drug felony is excluded from this automatic screen."}

    if flags.get("has_pending_ny_charges") or flags.get("has_pending_out_of_state_felony"):
        return {"status": STATUS_PENDING, "reason": "Pending charges must be resolved before this screen can qualify."}

    if not timeline.get("probation_parole_completed", False):
        return {"status": STATUS_PENDING, "reason": "Active probation or parole blocks eligibility until completion."}

    start = timeline.get("release_date") if timeline.get("incarceration_served") else timeline.get("sentencing_date")
    start = _coerce_date(start)
    if not start:
        return {"status": STATUS_PENDING, "reason": "Provide the sentencing date, or release date if incarcerated."}

    reset_date = _coerce_date(flags.get("subsequent_conviction_date"))
    if reset_date and reset_date > start:
        start = reset_date

    years = {"Violation": 1, "Misdemeanor": 3, "Felony": 8}[charge_level]
    threshold_date = _add_years(start, years)
    days_elapsed = (as_of - start).days
    result = {
        "status": STATUS_SEALED if as_of >= threshold_date else STATUS_PENDING,
        "start_date": start,
        "threshold_date": threshold_date,
        "days_elapsed": days_elapsed,
        "threshold_days": (threshold_date - start).days,
        "reason": "Waiting period has elapsed." if as_of >= threshold_date else "Waiting period has not elapsed.",
        "implementation_window_note": (
            "Pre-November 2024 convictions may be processed during the OCA automation window through November 16, 2027."
        ),
    }
    return result


def audit_instructions(targets: list[str]) -> list[str]:
    """Return practical, non-assertive instructions for commercial audits."""
    instructions = [
        "Request your NY DCJS criminal history and compare it with the Certificate of Disposition.",
        "Save the report date, source, and exact record identifiers before disputing a commercial report.",
    ]
    instructions.extend(
        f"Request a file disclosure and dispute inaccurate or sealed information with {target}." for target in targets
    )
    return instructions