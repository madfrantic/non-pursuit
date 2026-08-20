"""
Map a saved profile onto the statutory framework that actually applies to it.

The letter compiler renders whatever template it is handed; this module is
the thing that decides *which* one, so that decision lives in one testable
place instead of being spread across the UI as a chain of if-statements.

Routing is deliberately conservative. A template is only selected when the
profile carries a positive signal for that jurisdiction -- an EU/EEA/UK
country code, or a US state with an enacted consumer deletion right. Every
other profile, including an empty one, falls through to the generic
policy-based demand. Guessing a statute wrong is worse than not citing one:
a letter that cites a law which does not reach the recipient invites a
one-line rejection and burns the request.

Precedence when a profile matches more than one framework (a California
resident with an EU country code, say) runs GDPR > CCPA > NY > generic.
GDPR outranks the state statutes because it attaches to the data subject
and carries the widest extraterritorial reach under Art. 3(2).

`response_window_days` is reported here as statutory fact, but it is NOT
wired into the Campaign Tracker -- see the note on STATUTORY_WINDOW_NOTE.
"""
from dataclasses import dataclass

CCPA = "ccpa_deletion"
GDPR = "gdpr_erasure"
NY_HYBRID = "ny_hybrid"
GENERIC = "generic_deletion"

# The tracker still logs every request against config.CCPA_RESPONSE_WINDOW_DAYS.
# Changing that is deadline math -- a Human Validation Zone under CLAUDE.md --
# so the per-jurisdiction windows below are surfaced to the user as
# information and are not yet used to compute any deadline.
STATUTORY_WINDOW_NOTE = (
    "Statutory response window shown for reference. The Campaign Tracker "
    "currently logs every request against the 45-day CCPA window."
)

# EU/EEA member states, ISO 3166-1 alpha-2. EEA non-EU members (IS, LI, NO)
# are included: the GDPR applies there by decision of the EEA Joint Committee.
_EU_EEA = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
    "IS", "LI", "NO",
})

# UK GDPR (retained EU law) grants a materially identical Art. 17 right, so
# UK profiles route to the same template. "UK" is not a valid ISO code but is
# what users actually type.
_UK = frozenset({"GB", "UK"})

_GDPR_COUNTRIES = _EU_EEA | _UK


@dataclass(frozen=True)
class Jurisdiction:
    """One statutory framework and the template that asserts it."""

    template_type: str
    label: str
    statute: str
    response_window_days: int
    summary: str


JURISDICTIONS = {
    GDPR: Jurisdiction(
        template_type=GDPR,
        label="EU / UK — GDPR",
        statute="Regulation (EU) 2016/679, Art. 17",
        response_window_days=30,
        summary=(
            "Right to erasure with an Art. 21 objection to legitimate-interest "
            "processing. One-month response deadline under Art. 12(3)."
        ),
    ),
    CCPA: Jurisdiction(
        template_type=CCPA,
        label="California — CCPA/CPRA",
        statute="Cal. Civ. Code § 1798.105",
        response_window_days=45,
        summary=(
            "Consumer right to delete, with service-provider and third-party "
            "pass-through. 45-day response deadline under § 1798.130(a)(2)."
        ),
    ),
    NY_HYBRID: Jurisdiction(
        template_type=NY_HYBRID,
        label="New York — GBL Art. 25 + SHIELD Act",
        statute="N.Y. Gen. Bus. Law § 380 et seq.; § 899-bb",
        response_window_days=30,
        summary=(
            "NY FCRA suppression and § 380-f dispute reinvestigation (30 days), "
            "paired with a SHIELD Act § 899-bb safeguards demand over residual "
            "data. New York has no general consumer erasure statute; § 899-bb is "
            "a security duty, not a deletion right."
        ),
    ),
    GENERIC: Jurisdiction(
        template_type=GENERIC,
        label="Other — policy-based demand",
        statute="Broker privacy policy and published opt-out procedure",
        response_window_days=45,
        summary=(
            "No enacted statutory deletion right identified for this location. "
            "Demands suppression under the broker's own published policy."
        ),
    ),
}

# Highest-authority first, so the UI can present the override list in the same
# order the router prefers them.
PRECEDENCE = (GDPR, CCPA, NY_HYBRID, GENERIC)


def _norm(value) -> str:
    return str(value or "").strip().upper()


def route(profile) -> Jurisdiction:
    """Return the Jurisdiction that applies to a profile_state profile.

    Accepts the canonical profile dict; reads only `country` and `state`, and
    tolerates both being absent. An unrecognized value routes to GENERIC
    rather than raising -- a profile half-filled by a user mid-typing must
    still produce a usable letter.
    """
    profile = profile or {}
    country = _norm(profile.get("country"))
    state = _norm(profile.get("state"))

    if country in _GDPR_COUNTRIES:
        return JURISDICTIONS[GDPR]

    # A non-US, non-GDPR country outranks any stale US state left in the
    # profile: someone who has moved abroad is not covered by CCPA.
    if country and country not in ("US", "USA"):
        return JURISDICTIONS[GENERIC]

    if state == "CA":
        return JURISDICTIONS[CCPA]
    if state == "NY":
        return JURISDICTIONS[NY_HYBRID]

    return JURISDICTIONS[GENERIC]


def route_template(profile) -> str:
    """The template_type letter_compiler should render for this profile."""
    return route(profile).template_type


def get(template_type) -> Jurisdiction:
    """Look up a Jurisdiction by template_type, for a manual UI override."""
    try:
        return JURISDICTIONS[template_type]
    except KeyError:
        raise ValueError(
            f"Unknown template_type {template_type!r}; "
            f"expected one of {list(PRECEDENCE)}"
        ) from None


def available_jurisdictions() -> list:
    """Every Jurisdiction, highest statutory authority first."""
    return [JURISDICTIONS[key] for key in PRECEDENCE]


def gdpr_country_codes() -> frozenset:
    """The country codes that route to GDPR, for UI hints and tests."""
    return _GDPR_COUNTRIES
