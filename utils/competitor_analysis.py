"""
The commercial data-removal market Non-Pursuit is positioned against.

Ported from the Privacy Agent coursework scaffold
(Documents/PURSUIT/deepseek.py) -- the one part of that file this app did not
already do better. It answers the questions the Mini PRD and industry-research
deliverables ask: what do the incumbents charge, how many brokers do they
cover, how do they actually perform a removal, and where is the gap this
project sits in.

TWO THINGS THIS MODULE REFUSES TO DO QUIETLY
--------------------------------------------

1. It will not compare an annual price against a monthly one. DeleteMe prices
   per year and everyone else per month, so ranking on the raw `pricing_low`
   field puts the $129/yr option (about $10.75/mo) above the $32/mo one and
   inverts the entire price story. `monthly_range` normalises first, and
   every metric here is computed on normalised figures.

2. It will not present the numbers as verified. They are secondary desk
   research with a `last_updated` of 2025-01-01 and a self-declared
   `confidence` per row -- see data/competitors.json. Anything headed for a
   PRD, a pitch deck, or a graded submission goes through
   `needs_reverification()` first. Citing a competitor's price wrongly in a
   market analysis is the same class of error as citing the wrong statute in
   a demand letter: it is not a rounding problem, it is the argument
   collapsing.

Read-only. Nothing here writes, and no field carries user PII, so this module
sits outside the Human Validation Zones in docs/SAFETY_BOUNDARIES.md.
"""
import json
from dataclasses import dataclass
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "competitors.json"

MONTHS_PER_YEAR = 12

# Rows at or below this confidence are never reported as fact. "Low" is the
# scaffold's own label for a figure it could not corroborate.
UNRELIABLE_CONFIDENCE = frozenset({"low", "unknown", ""})

# How stale a `last_updated` may be before the figure is treated as needing a
# fresh look. Removal services reprice and re-tier often enough that a
# year-old number is a guess.
STALE_AFTER_YEAR = 2026

CITATION_WARNING = (
    "Competitor figures are unverified secondary research. Re-check the "
    "vendor's current pricing page before citing any number externally."
)


@dataclass(frozen=True)
class Competitor:
    """One commercial removal service, exactly as sourced."""

    name: str
    website: str = ""
    pricing_low: float | None = None
    pricing_high: float | None = None
    pricing_unit: str = ""
    coverage_min: int | None = None
    coverage_max: int | None = None
    removal_mechanism: str = ""
    key_features: str = ""
    positioning: str = ""
    confidence: str = ""
    last_updated: str = ""

    @property
    def is_annual(self) -> bool:
        return "year" in self.pricing_unit.lower()

    @property
    def is_manual(self) -> bool:
        """True when a human, not software, performs the removal."""
        mechanism = self.removal_mechanism.lower()
        return "human" in mechanism or "analyst" in mechanism or "manual" in mechanism

    def monthly_range(self) -> tuple[float | None, float | None]:
        """(low, high) in USD/month, whatever unit the row was sourced in.

        Annual prices divide by 12. This is the only comparable form; the
        raw fields are kept as sourced so the original figure stays auditable.
        """
        divisor = MONTHS_PER_YEAR if self.is_annual else 1
        low = self.pricing_low / divisor if self.pricing_low is not None else None
        high = self.pricing_high / divisor if self.pricing_high is not None else None
        return low, high

    def publishes_coverage(self) -> bool:
        """True when the vendor states how many brokers it covers."""
        return self.claimed_coverage is not None

    @property
    def claimed_coverage(self) -> int | None:
        """The coverage figure to judge the vendor on.

        Prefers the top of the range; falls back to the floor for a vendor
        that publishes "100+" and no ceiling. Reading only `coverage_max`
        would score such a vendor as having disclosed nothing.
        """
        return self.coverage_max if self.coverage_max is not None else self.coverage_min

    def is_reliable(self) -> bool:
        """False when the row's own confidence says not to lean on it."""
        return self.confidence.strip().lower() not in UNRELIABLE_CONFIDENCE

    def is_stale(self) -> bool:
        """True when `last_updated` predates STALE_AFTER_YEAR."""
        year = self.last_updated[:4]
        if not year.isdigit():
            return True
        return int(year) < STALE_AFTER_YEAR


def _load(path=None) -> dict:
    source = Path(path) if path else DATA_PATH
    return json.loads(source.read_text(encoding="utf-8"))


def load_competitors(path=None) -> list:
    """Every competitor in the dataset, in file order.

    File order is the sourced order and carries no ranking; sort explicitly
    if you need one.
    """
    payload = _load(path)
    known = set(Competitor.__dataclass_fields__)
    return [
        # Unknown keys are dropped rather than raising: the dataset is edited
        # by hand for coursework, and one stray field should not take the
        # whole market analysis down.
        Competitor(**{k: v for k, v in row.items() if k in known})
        for row in payload.get("competitors", [])
        if row.get("name")
    ]


def get(name: str, path=None) -> Competitor:
    """Look up one competitor by name, case-insensitively."""
    wanted = name.strip().lower()
    for competitor in load_competitors(path):
        if competitor.name.lower() == wanted:
            return competitor
    raise ValueError(
        f"Unknown competitor {name!r}; expected one of "
        f"{[c.name for c in load_competitors(path)]}"
    )


def comparison_metrics(competitors=None) -> dict:
    """Market shape, priced per month and coverage-weighted.

    Only rows that actually carry a figure feed each average, and vendors
    that publish no coverage number are counted separately rather than
    silently averaged away as zero.
    """
    competitors = load_competitors() if competitors is None else competitors
    if not competitors:
        return {}

    lows, highs = [], []
    for competitor in competitors:
        low, high = competitor.monthly_range()
        if low is not None:
            lows.append(low)
        if high is not None:
            highs.append(high)

    coverages = [c.coverage_max for c in competitors if c.coverage_max is not None]
    undisclosed = [c.name for c in competitors if not c.publishes_coverage()]

    return {
        "total_competitors": len(competitors),
        "monthly_entry_min": round(min(lows), 2) if lows else None,
        "monthly_entry_max": round(max(lows), 2) if lows else None,
        "monthly_entry_avg": round(sum(lows) / len(lows), 2) if lows else None,
        "monthly_top_tier_max": round(max(highs), 2) if highs else None,
        "coverage_max": max(coverages) if coverages else None,
        "coverage_avg": round(sum(coverages) / len(coverages)) if coverages else None,
        "coverage_undisclosed": undisclosed,
        "manual_operators": [c.name for c in competitors if c.is_manual],
        "unreliable_rows": [c.name for c in competitors if not c.is_reliable()],
        "citation_warning": CITATION_WARNING,
    }


def cheapest_entry(competitors=None) -> Competitor | None:
    """The lowest monthly entry price, after unit normalisation."""
    competitors = load_competitors() if competitors is None else competitors
    priced = [c for c in competitors if c.monthly_range()[0] is not None]
    if not priced:
        return None
    return min(priced, key=lambda c: c.monthly_range()[0])


def widest_coverage(competitors=None) -> Competitor | None:
    """The vendor claiming the most brokers covered."""
    competitors = load_competitors() if competitors is None else competitors
    disclosed = [c for c in competitors if c.coverage_max is not None]
    if not disclosed:
        return None
    return max(disclosed, key=lambda c: c.coverage_max)


def needs_reverification(competitors=None) -> list:
    """Rows that must not be cited externally without a fresh check.

    A row qualifies if its own confidence is low, or its `last_updated`
    predates STALE_AFTER_YEAR. Call this before any figure from this dataset
    goes into a document someone else reads.
    """
    competitors = load_competitors() if competitors is None else competitors
    flagged = []
    for competitor in competitors:
        reasons = []
        if not competitor.is_reliable():
            reasons.append(f"confidence is {competitor.confidence or 'unstated'}")
        if competitor.is_stale():
            reasons.append(f"last updated {competitor.last_updated or 'never'}")
        if reasons:
            flagged.append({"name": competitor.name, "reasons": reasons})
    return flagged


def gaps(competitors=None) -> dict:
    """Where the incumbents leave room, grouped by the axis they leave it on.

    These are positioning claims, not measurements. `architecture` is the
    only one that holds for the whole field at once and is the reason this
    project exists: every service here is a hosted subscription, so using one
    means handing your identity file to a third party in order to have it
    removed from third parties. Non-Pursuit runs on the user's own machine
    against a local encrypted store, which is a categorical difference
    rather than a better score on the same axis.
    """
    competitors = load_competitors() if competitors is None else competitors
    found = {"architecture": [], "transparency": [], "coverage": [],
             "cost": [], "automation": []}
    if not competitors:
        return found

    found["architecture"].append(
        "All " + str(len(competitors)) + " services are hosted subscriptions: "
        "the user uploads their identity file to a vendor to have it removed "
        "from brokers. Non-Pursuit keeps it local and encrypted."
    )

    for competitor in competitors:
        if not competitor.is_reliable():
            found["transparency"].append(
                f"{competitor.name} publishes figures this dataset could not corroborate")
        if not competitor.publishes_coverage():
            found["coverage"].append(
                f"{competitor.name} does not publish a broker-coverage number")
        elif competitor.claimed_coverage < 200:
            found["coverage"].append(
                f"{competitor.name} covers {competitor.claimed_coverage} brokers")
        low, _ = competitor.monthly_range()
        if low is not None and low > 10:
            found["cost"].append(
                f"{competitor.name} starts at ${low:.2f}/mo")
        if competitor.is_manual:
            found["automation"].append(
                f"{competitor.name} depends on human analysts, which caps its throughput")

    return found
