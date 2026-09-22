"""New York record-sealing screening, as a four-stage pipeline.

    classify()      raw paperwork      -> a typed Charge
    screen()        a Charge           -> one PathwayResult per statute
    build_record()  screened cases     -> a SealingRecord shaped like the form
    fill_motion()   a SealingRecord    -> a printable motion, as PDF bytes

This is guidance, not a legal determination. Ambiguous records belong with
counsel and against the Certificate of Disposition, which is the only
document that authoritatively states what someone was convicted of.

WHY FOUR STAGES AND NOT ONE FUNCTION

The previous implementation collapsed classification, rules and output into
a single 40-line `eligibility()`. That is what let a real bug hide: it ran
every charge level through one clock table -- {Violation: 1, Misdemeanor: 3,
Felony: 8} -- and reported the result as Clean Slate. Violations are not
Clean Slate. They are CPL 160.55, they seal only partially, and the one-year
figure came from the conditional-discharge period, not from CPL 160.57 at
all. With classification and rules fused, there was nowhere for that
distinction to live.

Splitting them means each statute gets its own evaluator, each evaluator
declares the charge levels it does *not* reach, and a charge level that
belongs to no pathway comes back NOT_APPLICABLE instead of silently
borrowing another statute's arithmetic.

WHY THE PATHWAYS DO NOT OVERLAP

New York has five sealing mechanisms and they are not tiers of one thing.
They differ in trigger (automatic vs. petition), in what they seal, and in
how they measure time. Two clocks in particular run in opposite directions
off the same fact:

  * CPL 160.57 (Clean Slate) measures from *release* from incarceration.
    Time served brings eligibility closer.
  * CPL 160.59 measures ten years from *sentencing* and then excludes any
    post-sentence incarceration from the count. Time served pushes
    eligibility further away.

Sharing a date helper between those two produces dates that are too early on
the petition path -- an applicant told they qualify when they do not, who
files, pays, serves the District Attorney and loses. `_clean_slate_clock()`
and `_petition_clock()` are therefore deliberately separate functions, and
neither is written in terms of the other.

HUMAN VALIDATION ZONE

docs/SAFETY_BOUNDARIES.md designates statutory deadline calculation a Human Validation Zone.
Every clock in this module is that kind of math and none of it carries
sign-off yet: `SIGNED_OFF_PATHWAYS` is empty, and every PathwayResult comes
back with `requires_human_signoff=True`. The day-45 approval recorded for
campaign_manager.calculate_deadline() is a different statute on a different
ledger and does not extend here. A human edits SIGNED_OFF_PATHWAYS after
reviewing the arithmetic; this module will not do it on their behalf.
"""
from dataclasses import dataclass, field
from datetime import date

# --- vocabulary -----------------------------------------------------------

STATUS_SEALED = "Sealed"
STATUS_PENDING = "Pending Date"
STATUS_ELIGIBLE_TO_PETITION = "Eligible to Petition"
STATUS_DISQUALIFIED = "Disqualified"
STATUS_NOT_APPLICABLE = "Not Applicable"

# What the seal actually accomplishes, which is not the same across statutes
# and is the detail most often lost. A CPL 160.55 seal leaves the court file
# open to inspection; reporting it as plain "Sealed" overstates the relief.
SCOPE_FULL = "full"
SCOPE_PARTIAL = "partial"
SCOPE_CONDITIONAL = "conditional"
SCOPE_NONE = "none"

MECHANISM_AUTOMATIC = "automatic"
MECHANISM_PETITION = "petition"

CPL_160_50 = "CPL 160.50"
CPL_160_55 = "CPL 160.55"
CPL_160_57 = "CPL 160.57"
CPL_160_58 = "CPL 160.58"
CPL_160_59 = "CPL 160.59"

PATHWAYS = (CPL_160_50, CPL_160_55, CPL_160_57, CPL_160_58, CPL_160_59)

# Human Validation Zone, per docs/SAFETY_BOUNDARIES.md. A pathway listed here has had its
# clock arithmetic reviewed and approved by a person. Adding an entry is a
# human's decision; nothing in this module writes to it.
SIGNED_OFF_PATHWAYS = frozenset()

LEVEL_NON_CONVICTION = "Non-Conviction"
LEVEL_INFRACTION = "Traffic Infraction"
LEVEL_VIOLATION = "Violation"
LEVEL_MISDEMEANOR = "Misdemeanor"
LEVEL_FELONY = "Felony"

CHARGE_LEVELS = (
    LEVEL_NON_CONVICTION,
    LEVEL_INFRACTION,
    LEVEL_VIOLATION,
    LEVEL_MISDEMEANOR,
    LEVEL_FELONY,
)

FELONY_CLASSES = ("A", "B", "C", "D", "E")

# Clean Slate waiting periods, CPL 160.57. Violations are absent on purpose:
# the statute does not reach them.
CLEAN_SLATE_YEARS = {LEVEL_MISDEMEANOR: 3, LEVEL_FELONY: 8}
PETITION_YEARS = 10

# OCA's deadline to work through convictions predating the Clean Slate
# effective date of 16 Nov 2024.
CLEAN_SLATE_EFFECTIVE = date(2024, 11, 16)
IMPLEMENTATION_WINDOW_END = date(2027, 11, 16)

# Penal Law articles that decide eligibility. Kept as strings because that
# is how they are cited and how they arrive from the intake form.
ARTICLE_CONSPIRACY = "105"
ARTICLE_HOMICIDE = "125"
ARTICLE_SEX_OFFENSE = "130"
ARTICLE_CONTROLLED_SUBSTANCE = "220"
ARTICLE_MARIJUANA = "221"
ARTICLE_CHILD_PERFORMANCE = "263"

# CPL 160.55 seals violations and traffic infractions with exactly two
# carve-outs.
VTL_DWAI = "1192.1"
PL_LOITERING_PROSTITUTION = "240.37"


# --- shared helpers -------------------------------------------------------

def _coerce_date(value) -> date | None:
    """A date from a date, an ISO string, or nothing at all.

    Malformed input degrades to None rather than raising: an intake form is
    allowed to be half-finished, and the pathway evaluators already treat a
    missing date as "cannot compute yet".
    """
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _add_years(start: date, years: int) -> date:
    """Anniversary of `start`, `years` later.

    29 February has no anniversary in a common year; it lands on the 28th,
    which is the convention the courts' own date arithmetic uses.
    """
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, day=28)


def _parse_article(penal_law: str) -> str | None:
    """The article number from a citation like 'PL 155.25' or '155.25'.

    Returns None rather than guessing when there is no recognisable article,
    so a blank or freeform entry cannot accidentally match an exclusion.
    """
    if not penal_law:
        return None
    cleaned = str(penal_law).strip().upper()
    for prefix in ("PL", "P.L.", "PENAL LAW", "VTL", "V.T.L."):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    cleaned = cleaned.lstrip("§ ").strip()
    head = cleaned.split(".")[0].split("-")[0].split("(")[0].strip()
    return head if head.isdigit() else None


# --- Stage 1: classify ----------------------------------------------------

@dataclass(frozen=True)
class Charge:
    """One charge, typed, before any eligibility rule has looked at it.

    Separating this from the rules is the whole point of the rewrite: an
    exclusion is a property of the *charge* (this is an article 130 offence),
    while eligibility is a property of a charge under a *particular statute*.
    Conflating them is what produced the violation bug.
    """
    level: str
    penal_law: str = ""
    description: str = ""
    article: str | None = None
    felony_class: str | None = None
    is_sex_offense: bool = False
    is_child_performance: bool = False
    is_homicide_felony: bool = False
    is_violent_felony: bool = False
    is_conspiracy: bool = False
    is_attempt: bool = False
    requires_sora: bool = False
    is_controlled_substance: bool = False
    is_dwai: bool = False
    is_loitering_prostitution: bool = False
    is_out_of_state: bool = False
    underlying_offense_eligible: bool = True
    classification_notes: tuple = ()

    @property
    def is_class_a_felony(self) -> bool:
        return self.level == LEVEL_FELONY and self.felony_class == "A"

    @property
    def is_class_a_drug_felony(self) -> bool:
        """The one Class A felony Clean Slate does reach.

        CPL 160.57 excludes Class A felonies *other than* Class A drug
        felonies, so this distinction decides the outcome on its own.
        """
        return self.is_class_a_felony and self.is_controlled_substance


def classify(offense: dict) -> Charge:
    """Stage 1. Turn intake fields into a typed Charge.

    Explicit user flags always win over anything inferred from the citation
    string. Someone entering a charge by hand knows more than a regex does,
    and a screen that overrode them would be unusable for exactly the
    ambiguous records that most need review.
    """
    offense = offense or {}
    level = offense.get("charge_level")
    if level not in CHARGE_LEVELS:
        level = ""

    penal_law = (offense.get("penal_law_section") or "").strip()
    article = _parse_article(penal_law)
    citation = penal_law.upper().replace(" ", "")
    notes = []

    def flag(key: str, inferred: bool, note: str) -> bool:
        if offense.get(key) is not None:
            return bool(offense.get(key))
        if inferred:
            notes.append(note)
        return inferred

    is_sex_offense = flag(
        "is_sex_offense", article == ARTICLE_SEX_OFFENSE,
        f"Article {ARTICLE_SEX_OFFENSE} citation read as a sex offense.")
    is_child_performance = flag(
        "is_child_performance", article == ARTICLE_CHILD_PERFORMANCE,
        f"Article {ARTICLE_CHILD_PERFORMANCE} citation read as sexual performance by a child.")
    is_homicide_felony = flag(
        "is_homicide_felony", article == ARTICLE_HOMICIDE and level == LEVEL_FELONY,
        f"Article {ARTICLE_HOMICIDE} felony citation read as a homicide offense.")
    is_conspiracy = flag(
        "is_conspiracy", article == ARTICLE_CONSPIRACY,
        f"Article {ARTICLE_CONSPIRACY} citation read as conspiracy.")
    is_controlled_substance = flag(
        "is_article_220_drug", article in (ARTICLE_CONTROLLED_SUBSTANCE, ARTICLE_MARIJUANA),
        f"Article {article} citation read as a controlled-substance offense.")
    is_dwai = flag(
        "is_dwai", VTL_DWAI.replace(".", "") in citation.replace(".", "") and "1192" in citation,
        "VTL 1192(1) citation read as DWAI, which CPL 160.55 excludes.")
    is_loitering = flag(
        "is_loitering_prostitution",
        PL_LOITERING_PROSTITUTION.replace(".", "") in citation.replace(".", ""),
        "PL 240.37 citation read as loitering for prostitution, which CPL 160.55 excludes.")

    felony_class = offense.get("felony_class")
    if felony_class not in FELONY_CLASSES:
        felony_class = None
    if level != LEVEL_FELONY:
        felony_class = None

    return Charge(
        level=level,
        penal_law=penal_law,
        description=(offense.get("offense_description") or "").strip(),
        article=article,
        felony_class=felony_class,
        is_sex_offense=is_sex_offense,
        is_child_performance=is_child_performance,
        is_homicide_felony=is_homicide_felony,
        is_violent_felony=bool(offense.get("is_violent_felony")),
        is_conspiracy=is_conspiracy,
        is_attempt=bool(offense.get("is_attempt")),
        requires_sora=bool(offense.get("requires_sora")) or is_sex_offense,
        is_controlled_substance=is_controlled_substance,
        is_dwai=is_dwai,
        is_loitering_prostitution=is_loitering,
        is_out_of_state=bool(offense.get("is_out_of_state")),
        underlying_offense_eligible=offense.get("underlying_offense_eligible", True),
        classification_notes=tuple(notes),
    )


# --- Stage 2: apply rules -------------------------------------------------

@dataclass(frozen=True)
class PathwayResult:
    """What one statute says about one charge."""
    statute: str
    label: str
    mechanism: str
    status: str
    seal_scope: str
    reason: str
    start_date: date | None = None
    threshold_date: date | None = None
    days_elapsed: int | None = None
    days_remaining: int | None = None
    notes: tuple = ()
    requires_human_signoff: bool = True

    @property
    def applicable(self) -> bool:
        return self.status != STATUS_NOT_APPLICABLE

    @property
    def favourable(self) -> bool:
        return self.status in (STATUS_SEALED, STATUS_ELIGIBLE_TO_PETITION)

    def as_dict(self) -> dict:
        return {
            "statute": self.statute, "label": self.label,
            "mechanism": self.mechanism, "status": self.status,
            "seal_scope": self.seal_scope, "reason": self.reason,
            "start_date": self.start_date, "threshold_date": self.threshold_date,
            "days_elapsed": self.days_elapsed, "days_remaining": self.days_remaining,
            "notes": list(self.notes),
            "requires_human_signoff": self.requires_human_signoff,
        }


def _result(statute, label, mechanism, status, scope, reason, **kw) -> PathwayResult:
    return PathwayResult(
        statute=statute, label=label, mechanism=mechanism, status=status,
        seal_scope=scope, reason=reason,
        requires_human_signoff=statute not in SIGNED_OFF_PATHWAYS, **kw)


def _not_applicable(statute, label, mechanism, reason) -> PathwayResult:
    return _result(statute, label, mechanism, STATUS_NOT_APPLICABLE,
                   SCOPE_NONE, reason)


def _clean_slate_clock(timeline: dict) -> date | None:
    """CPL 160.57's start date: release from incarceration, else sentencing.

    Time served moves this *earlier* relative to the sentencing date, which
    is the opposite of how CPL 160.59 treats the same fact. Kept separate
    from _petition_clock() for exactly that reason -- see the module
    docstring.
    """
    if timeline.get("incarceration_served"):
        return _coerce_date(timeline.get("release_date"))
    return _coerce_date(timeline.get("sentencing_date"))


def _petition_clock(timeline: dict) -> tuple:
    """CPL 160.59's start date and the incarceration days it excludes.

    The statute runs ten years from sentencing but does not count jail or
    prison time served *after* sentencing. Excluding days is arithmetically
    the same as pushing the threshold date out by that many days, which is
    how it is applied below.
    """
    start = _coerce_date(timeline.get("sentencing_date"))
    excluded = timeline.get("days_incarcerated")
    if excluded is None:
        release = _coerce_date(timeline.get("release_date"))
        if timeline.get("incarceration_served") and release and start and release > start:
            excluded = (release - start).days
        else:
            excluded = 0
    return start, max(int(excluded or 0), 0)


def _elapsed(start: date, threshold: date, as_of: date) -> dict:
    return {
        "start_date": start,
        "threshold_date": threshold,
        "days_elapsed": (as_of - start).days,
        "days_remaining": max((threshold - as_of).days, 0),
    }


def _screen_160_50(charge: Charge, timeline: dict, flags: dict, as_of: date) -> PathwayResult:
    """Non-convictions: dismissals, acquittals, favourable terminations."""
    label = "Non-conviction sealing"
    if charge.level != LEVEL_NON_CONVICTION:
        return _not_applicable(
            CPL_160_50, label, MECHANISM_AUTOMATIC,
            "Applies only to cases that ended without a conviction.")
    return _result(
        CPL_160_50, label, MECHANISM_AUTOMATIC, STATUS_SEALED, SCOPE_FULL,
        "A case terminated in the accused's favour seals automatically, with no application.",
        notes=("If a background check still shows this case, the seal was not applied — "
               "raise it with the court clerk and DCJS.",))


def _screen_160_55(charge: Charge, timeline: dict, flags: dict, as_of: date) -> PathwayResult:
    """Violations and traffic infractions. Partial seal only."""
    label = "Violation / infraction sealing"
    if charge.level not in (LEVEL_VIOLATION, LEVEL_INFRACTION):
        return _not_applicable(
            CPL_160_55, label, MECHANISM_AUTOMATIC,
            "Applies only to violations and traffic infractions. Misdemeanours and "
            "felonies are Clean Slate or petition matters.")

    if charge.is_dwai:
        return _result(
            CPL_160_55, label, MECHANISM_AUTOMATIC, STATUS_DISQUALIFIED, SCOPE_NONE,
            "Driving While Ability Impaired (VTL § 1192(1)) is expressly carved out of CPL 160.55.")
    if charge.is_loitering_prostitution:
        return _result(
            CPL_160_55, label, MECHANISM_AUTOMATIC, STATUS_DISQUALIFIED, SCOPE_NONE,
            "Loitering for the purpose of a prostitution offence (PL § 240.37) is expressly "
            "carved out of CPL 160.55.")

    partial_note = (
        "Partial seal only. DCJS, police and prosecutor records are sealed; the court "
        "file is not, so the conviction stays findable in court records.")

    sentencing = _coerce_date(timeline.get("sentencing_date"))
    if timeline.get("conditional_discharge_imposed"):
        if not sentencing:
            return _result(
                CPL_160_55, label, MECHANISM_AUTOMATIC, STATUS_PENDING, SCOPE_PARTIAL,
                "A conditional discharge was imposed; provide the sentencing date to date the seal.",
                notes=(partial_note,))
        threshold = _add_years(sentencing, 1)
        sealed = as_of >= threshold
        return _result(
            CPL_160_55, label, MECHANISM_AUTOMATIC,
            STATUS_SEALED if sealed else STATUS_PENDING, SCOPE_PARTIAL,
            ("The conditional-discharge year has run, so the seal should have been applied."
             if sealed else
             "Most courts hold the seal until the one-year conditional discharge ends."),
            notes=(partial_note,), **_elapsed(sentencing, threshold, as_of))

    return _result(
        CPL_160_55, label, MECHANISM_AUTOMATIC, STATUS_SEALED, SCOPE_PARTIAL,
        "Violations and traffic infractions seal automatically on disposition where no "
        "conditional discharge was imposed.",
        notes=(partial_note,))


def _screen_160_57(charge: Charge, timeline: dict, flags: dict, as_of: date) -> PathwayResult:
    """Clean Slate. Automatic, misdemeanours and felonies only."""
    label = "Clean Slate automatic sealing"
    if charge.level not in CLEAN_SLATE_YEARS:
        return _not_applicable(
            CPL_160_57, label, MECHANISM_AUTOMATIC,
            "Clean Slate reaches misdemeanour and felony convictions only. Violations "
            "seal under CPL 160.55 and non-convictions under CPL 160.50.")

    if charge.is_out_of_state:
        return _result(
            CPL_160_57, label, MECHANISM_AUTOMATIC, STATUS_DISQUALIFIED, SCOPE_NONE,
            "Clean Slate reaches New York convictions only.")
    if charge.is_sex_offense or charge.requires_sora:
        return _result(
            CPL_160_57, label, MECHANISM_AUTOMATIC, STATUS_DISQUALIFIED, SCOPE_NONE,
            "Sex offences and offences carrying SORA registration are excluded from Clean Slate.")
    if charge.is_class_a_felony and not charge.is_class_a_drug_felony:
        return _result(
            CPL_160_57, label, MECHANISM_AUTOMATIC, STATUS_DISQUALIFIED, SCOPE_NONE,
            "Class A felonies are excluded from Clean Slate, other than Class A drug felonies.")

    if flags.get("has_pending_ny_charges") or flags.get("has_pending_out_of_state_felony"):
        return _result(
            CPL_160_57, label, MECHANISM_AUTOMATIC, STATUS_PENDING, SCOPE_NONE,
            "Clean Slate cannot seal while a charge is pending. Resolve it first.")
    if not timeline.get("probation_parole_completed", False):
        return _result(
            CPL_160_57, label, MECHANISM_AUTOMATIC, STATUS_PENDING, SCOPE_NONE,
            "Clean Slate cannot seal during active probation or parole supervision.")

    start = _clean_slate_clock(timeline)
    if not start:
        return _result(
            CPL_160_57, label, MECHANISM_AUTOMATIC, STATUS_PENDING, SCOPE_NONE,
            "Provide the release date if incarceration was served, otherwise the sentencing date.")

    reset = _coerce_date(flags.get("subsequent_conviction_date"))
    notes = []
    if reset and reset > start:
        start = reset
        notes.append("Clock restarted at the most recent subsequent conviction.")

    years = CLEAN_SLATE_YEARS[charge.level]
    threshold = _add_years(start, years)
    sealed = as_of >= threshold

    if _coerce_date(timeline.get("sentencing_date")) and \
            _coerce_date(timeline.get("sentencing_date")) < CLEAN_SLATE_EFFECTIVE:
        notes.append(
            f"Conviction predates the 16 Nov 2024 effective date, so OCA has until "
            f"{IMPLEMENTATION_WINDOW_END.isoformat()} to process it. The seal may lag "
            f"the date below.")

    return _result(
        CPL_160_57, label, MECHANISM_AUTOMATIC,
        STATUS_SEALED if sealed else STATUS_PENDING, SCOPE_FULL,
        (f"The {years}-year Clean Slate period has run."
         if sealed else f"The {years}-year Clean Slate period has not yet run."),
        notes=tuple(notes), **_elapsed(start, threshold, as_of))


def _screen_160_58(charge: Charge, timeline: dict, flags: dict, as_of: date) -> PathwayResult:
    """Conditional sealing after drug diversion or DTAP. Petition, no standard form."""
    label = "Conditional sealing after treatment"
    if not charge.is_controlled_substance:
        return _not_applicable(
            CPL_160_58, label, MECHANISM_PETITION,
            "Applies only to controlled-substance and marijuana convictions.")
    if not timeline.get("diversion_or_dtap_completed"):
        return _result(
            CPL_160_58, label, MECHANISM_PETITION, STATUS_PENDING, SCOPE_CONDITIONAL,
            "Requires completion of judicial diversion or DTAP and all other sentence terms.")
    return _result(
        CPL_160_58, label, MECHANISM_PETITION, STATUS_ELIGIBLE_TO_PETITION, SCOPE_CONDITIONAL,
        "Treatment and sentence terms are complete, so a conditional-sealing motion may be filed.",
        notes=("There is no standard form — the motion goes to the sentencing court.",
               "The seal is conditional: a later misdemeanour or felony arrest unseals it."))


def _screen_160_59(charge: Charge, timeline: dict, flags: dict, as_of: date) -> PathwayResult:
    """The ten-year discretionary petition. The pathway a person actually files."""
    label = "Ten-year petition"
    if charge.level not in (LEVEL_MISDEMEANOR, LEVEL_FELONY):
        return _not_applicable(
            CPL_160_59, label, MECHANISM_PETITION,
            "The petition route addresses criminal convictions. Violations and "
            "non-convictions seal by other means.")

    # The eight statutory exclusions, in the order the affidavit lists them.
    exclusions = [
        (charge.is_sex_offense, "a sex offence defined in Penal Law article 130"),
        (charge.is_child_performance, "an offence defined in Penal Law article 263"),
        (charge.is_homicide_felony, "a felony offence defined in Penal Law article 125"),
        (charge.is_violent_felony, "a violent felony offence defined in Penal Law § 70.02"),
        (charge.is_class_a_felony, "a Class A felony offence"),
        (charge.is_conspiracy and not charge.underlying_offense_eligible,
         "a Penal Law article 105 conspiracy where the underlying offence is not eligible"),
        (charge.is_attempt and charge.level == LEVEL_FELONY and not charge.underlying_offense_eligible,
         "a felony attempt to commit an offence that is not eligible"),
        (charge.requires_sora, "an offence requiring SORA registration under Correction Law article 6-C"),
    ]
    hit = [text for applies, text in exclusions if applies]
    if hit:
        return _result(
            CPL_160_59, label, MECHANISM_PETITION, STATUS_DISQUALIFIED, SCOPE_NONE,
            f"CPL 160.59 excludes {hit[0]}.",
            notes=tuple(f"Also excluded as {h}." for h in hit[1:]))

    total = flags.get("total_convictions")
    felonies = flags.get("total_felony_convictions")
    if total is not None and int(total) > 2:
        return _result(
            CPL_160_59, label, MECHANISM_PETITION, STATUS_DISQUALIFIED, SCOPE_NONE,
            f"CPL 160.59 allows at most two convictions; {int(total)} were entered.",
            notes=("Convictions arising from the same criminal transaction count as one — "
                   "if these arose from one incident, re-enter the count accordingly.",))
    if felonies is not None and int(felonies) > 1:
        return _result(
            CPL_160_59, label, MECHANISM_PETITION, STATUS_DISQUALIFIED, SCOPE_NONE,
            f"CPL 160.59 allows at most one felony conviction; {int(felonies)} were entered.")

    if flags.get("has_pending_ny_charges") or flags.get("has_pending_out_of_state_felony"):
        return _result(
            CPL_160_59, label, MECHANISM_PETITION, STATUS_PENDING, SCOPE_NONE,
            "The affidavit requires that no criminal charge be open or pending.")

    start, excluded_days = _petition_clock(timeline)
    if not start:
        return _result(
            CPL_160_59, label, MECHANISM_PETITION, STATUS_PENDING, SCOPE_NONE,
            "Provide the sentencing date for the most recent conviction.")

    reset = _coerce_date(flags.get("subsequent_conviction_date"))
    notes = []
    if reset and reset > start:
        start = reset
        notes.append("Clock restarted at the most recent subsequent conviction.")

    threshold = _add_years(start, PETITION_YEARS)
    if excluded_days:
        threshold = date.fromordinal(threshold.toordinal() + excluded_days)
        notes.append(
            f"{excluded_days} day(s) of post-sentence incarceration are excluded from the "
            f"ten-year count, moving the earliest filing date out by that much.")

    ready = as_of >= threshold
    return _result(
        CPL_160_59, label, MECHANISM_PETITION,
        STATUS_ELIGIBLE_TO_PETITION if ready else STATUS_PENDING, SCOPE_FULL,
        ("Ten qualifying years have run, so a sealing motion may be filed."
         if ready else "Ten qualifying years have not yet run."),
        notes=tuple(notes) + (
            "Sealing is discretionary — the judge decides, and the District Attorney "
            "has 45 days to consent or oppose.",),
        **_elapsed(start, threshold, as_of))


_EVALUATORS = (
    (CPL_160_50, _screen_160_50),
    (CPL_160_55, _screen_160_55),
    (CPL_160_57, _screen_160_57),
    (CPL_160_58, _screen_160_58),
    (CPL_160_59, _screen_160_59),
)


@dataclass(frozen=True)
class Screening:
    """Every pathway's verdict on one charge, plus the headline."""
    charge: Charge
    pathways: tuple
    as_of: date

    def by_statute(self, statute: str) -> PathwayResult | None:
        return next((p for p in self.pathways if p.statute == statute), None)

    @property
    def applicable(self) -> tuple:
        return tuple(p for p in self.pathways if p.applicable)

    @property
    def favourable(self) -> tuple:
        return tuple(p for p in self.pathways if p.favourable)

    @property
    def headline(self) -> PathwayResult | None:
        """The pathway to lead with.

        An already-effective automatic seal outranks a petition the person
        would have to file, and a full seal outranks a partial one. Where
        nothing is favourable, the nearest pending pathway is more useful
        than the first disqualification.
        """
        order = {SCOPE_FULL: 0, SCOPE_CONDITIONAL: 1, SCOPE_PARTIAL: 2, SCOPE_NONE: 3}
        good = self.favourable
        if good:
            return sorted(good, key=lambda p: (
                p.mechanism != MECHANISM_AUTOMATIC, order[p.seal_scope]))[0]
        pending = [p for p in self.applicable if p.status == STATUS_PENDING]
        if pending:
            return sorted(pending, key=lambda p: (
                p.days_remaining if p.days_remaining is not None else 10**6))[0]
        return self.applicable[0] if self.applicable else None

    def as_dict(self) -> dict:
        head = self.headline
        return {
            "as_of": self.as_of,
            "charge_level": self.charge.level,
            "penal_law": self.charge.penal_law,
            "classification_notes": list(self.charge.classification_notes),
            "pathways": [p.as_dict() for p in self.pathways],
            "headline": head.as_dict() if head else None,
            "requires_human_signoff": any(p.requires_human_signoff for p in self.applicable),
        }


def screen(payload: dict, as_of: date | None = None) -> Screening:
    """Stage 2. Run one case through every pathway, independently."""
    as_of = as_of or date.today()
    charge = classify(payload.get("offense_details", {}))
    timeline = payload.get("timeline_inputs", {}) or {}
    flags = payload.get("current_status_flags", {}) or {}

    if not charge.level:
        unsupported = tuple(
            _not_applicable(statute, statute, MECHANISM_AUTOMATIC,
                            "Charge level is missing or unsupported.")
            for statute, _ in _EVALUATORS)
        return Screening(charge=charge, pathways=unsupported, as_of=as_of)

    return Screening(
        charge=charge, as_of=as_of,
        pathways=tuple(fn(charge, timeline, flags, as_of) for _, fn in _EVALUATORS))


# --- Stage 3: merge -------------------------------------------------------

@dataclass(frozen=True)
class Applicant:
    """The identity block at the head of the CPL 160.59 Notice of Motion."""
    name: str = ""
    aka: str = ""
    nysid: str = ""
    motorist_id: str = ""
    dob: date | None = None
    address: str = ""
    city_state_zip: str = ""
    phone: str = ""
    email: str = ""


@dataclass(frozen=True)
class CaseRecord:
    """One conviction as the form's repeating case row wants it."""
    docket_number: str = ""
    court_name: str = ""
    county: str = ""
    conviction_charge: str = ""
    law_section: str = ""
    conviction_date: date | None = None
    sentence_date: date | None = None
    sentence_term: str = ""
    release_date: date | None = None
    screening: Screening | None = None


@dataclass(frozen=True)
class SealingRecord:
    """Stage 3's output: everything the motion needs, validated as a set.

    Merging matters because CPL 160.59 is filed per *applicant*, not per
    case. Two convictions in different counties are one motion, filed in one
    court under the statute's venue rule, served on both District Attorneys.
    Screening each case in isolation cannot see any of that.
    """
    applicant: Applicant
    cases: tuple = ()
    discretionary_factors: str = ""
    prior_application: bool = False
    intends_further_application: bool = False
    attachments: tuple = ()
    as_of: date = field(default_factory=date.today)

    @property
    def eligible_cases(self) -> tuple:
        return tuple(
            c for c in self.cases
            if c.screening and (r := c.screening.by_statute(CPL_160_59))
            and r.status == STATUS_ELIGIBLE_TO_PETITION)

    @property
    def venue(self) -> str:
        """The court the motion is filed in, under CPL 160.59's venue rule.

        Two cases means the court that entered the most serious conviction;
        where both are the same class, the more recent one. Returned as the
        court name so the caller can print it, with an empty string when
        there is nothing to decide between.
        """
        ranked = [c for c in self.cases if c.court_name]
        if not ranked:
            return ""
        if len(ranked) == 1:
            return ranked[0].court_name

        def seriousness(case: CaseRecord) -> tuple:
            level = case.screening.charge.level if case.screening else ""
            weight = {LEVEL_FELONY: 3, LEVEL_MISDEMEANOR: 2,
                      LEVEL_VIOLATION: 1}.get(level, 0)
            klass = case.screening.charge.felony_class if case.screening else None
            class_weight = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}.get(klass, 0)
            return (weight, class_weight,
                    case.conviction_date.toordinal() if case.conviction_date else 0)

        return sorted(ranked, key=seriousness, reverse=True)[0].court_name

    @property
    def service_counties(self) -> tuple:
        """Every county whose District Attorney must be served."""
        return tuple(sorted({c.county for c in self.cases if c.county}))

    @property
    def blocking_issues(self) -> tuple:
        """Everything that stops this being a filable motion today."""
        issues = []
        if not self.applicant.name:
            issues.append("Applicant name is required.")
        if not self.cases:
            issues.append("At least one conviction must be listed.")
        if len(self.cases) > 2:
            issues.append(
                f"CPL 160.59 allows at most two convictions; {len(self.cases)} are listed.")
        felonies = sum(
            1 for c in self.cases
            if c.screening and c.screening.charge.level == LEVEL_FELONY)
        if felonies > 1:
            issues.append(
                f"CPL 160.59 allows at most one felony; {felonies} are listed.")
        for case in self.cases:
            result = case.screening.by_statute(CPL_160_59) if case.screening else None
            if result and result.status == STATUS_DISQUALIFIED:
                issues.append(f"{case.docket_number or 'A listed case'}: {result.reason}")
            elif result and result.status == STATUS_PENDING:
                issues.append(f"{case.docket_number or 'A listed case'}: {result.reason}")
        if not self.discretionary_factors.strip():
            issues.append(
                "The affidavit requires reasons why the court should grant sealing.")
        return tuple(issues)

    @property
    def ready_to_file(self) -> bool:
        return not self.blocking_issues


def build_record(applicant: dict, cases: list, *, discretionary_factors: str = "",
                 prior_application: bool = False,
                 intends_further_application: bool = False,
                 attachments: list | None = None,
                 as_of: date | None = None) -> SealingRecord:
    """Stage 3. Screen every case and merge them into one filable record."""
    as_of = as_of or date.today()
    applicant = applicant or {}
    merged = []

    for case in cases or []:
        screening = screen(case, as_of=as_of)
        offense = case.get("offense_details", {}) or {}
        meta = case.get("case_metadata", {}) or {}
        timeline = case.get("timeline_inputs", {}) or {}
        merged.append(CaseRecord(
            docket_number=(meta.get("docket_or_indictment_no") or "").strip(),
            court_name=(meta.get("court_name") or meta.get("court_type") or "").strip(),
            county=(meta.get("county") or "").strip(),
            conviction_charge=(offense.get("offense_description") or "").strip(),
            law_section=(offense.get("penal_law_section") or "").strip(),
            conviction_date=_coerce_date(timeline.get("conviction_date")),
            sentence_date=_coerce_date(timeline.get("sentencing_date")),
            sentence_term=(timeline.get("sentence_term") or "").strip(),
            release_date=_coerce_date(timeline.get("release_date")),
            screening=screening,
        ))

    default_attachments = [
        "Affidavit in Support of Sealing Pursuant to CPL 160.59",
        "Affidavit of Service on the District Attorney",
        "Certificate of Disposition for each conviction listed",
    ]
    return SealingRecord(
        applicant=Applicant(
            name=(applicant.get("name") or "").strip(),
            aka=(applicant.get("aka") or "").strip(),
            nysid=(applicant.get("nysid") or "").strip(),
            motorist_id=(applicant.get("motorist_id") or "").strip(),
            dob=_coerce_date(applicant.get("dob")),
            address=(applicant.get("address") or "").strip(),
            city_state_zip=(applicant.get("city_state_zip") or "").strip(),
            phone=(applicant.get("phone") or "").strip(),
            email=(applicant.get("email") or "").strip(),
        ),
        cases=tuple(merged),
        discretionary_factors=(discretionary_factors or "").strip(),
        prior_application=bool(prior_application),
        intends_further_application=bool(intends_further_application),
        attachments=tuple(attachments or default_attachments),
        as_of=as_of,
    )


# --- Stage 4: fill --------------------------------------------------------

# The eight exclusions, verbatim from the affidavit, so the rendered motion
# reproduces the attestation the court expects rather than a paraphrase.
AFFIDAVIT_EXCLUSIONS = (
    "a sex offense defined in article one hundred thirty of the Penal Law;",
    "an offense defined in article two hundred sixty-three of the Penal Law;",
    "a felony offense defined in article one hundred twenty-five of the Penal Law;",
    "a violent felony offense defined in section 70.02 of the Penal Law;",
    "a class A felony offense defined in the Penal Law;",
    "a felony offense defined in article one hundred five of the Penal Law where the "
    "underlying offense is not an eligible offense;",
    "an attempt to commit an offense that is not an eligible offense if the attempt is a felony; or,",
    "an offense for which registration as a sex offender is required pursuant to article "
    "six-C of the correction law.",
)

DRAFT_WATERMARK = (
    "DRAFT — NOT A COURT FORM. This document reproduces the structure of the official "
    "CPL 160.59 Notice of Motion so the applicant can check their answers. File on the "
    "form published by the New York State Unified Court System."
)


def _fmt(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def fill_motion(record: SealingRecord) -> bytes:
    """Stage 4. Render the merged record as a printable motion.

    fpdf2, matching utils/pdf_generator.py -- same reasoning as that module:
    no system binaries, and the output is a flat sequence of blocks rather
    than a paginated table.

    This is deliberately *not* passed off as the official form. There is no
    fillable AcroForm published for the CPL 160.59 motion, so every page
    carries DRAFT_WATERMARK and the caller is told to transcribe onto the
    court's own PDF. A document that looked like the real filing but was not
    would be the worst possible output here.
    """
    from fpdf import FPDF

    pdf = FPDF(format="Letter", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    # fpdf2's multi_cell leaves the cursor at the right edge of the cell it
    # just drew, so every block sets x back explicitly. Without this an
    # indented line silently pushes the one after it off the page.
    def heading(text: str, size: int = 12) -> None:
        pdf.set_font("Helvetica", "B", size)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(width, 6, _safe_latin1(text))
        pdf.ln(1)

    def body(text: str, size: int = 9, style: str = "", indent: float = 0) -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(width - indent, 4.6, _safe_latin1(text))

    def rule() -> None:
        pdf.ln(1.5)
        pdf.set_draw_color(150, 150, 150)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2.5)

    pdf.set_fill_color(240, 236, 220)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.multi_cell(width, 3.6, _safe_latin1(DRAFT_WATERMARK), fill=True)
    pdf.ln(3)

    heading("NOTICE OF MOTION AND AFFIDAVIT IN SUPPORT", 13)
    body("Sealing Pursuant to CPL 160.59", 10, "B")
    rule()

    a = record.applicant
    heading("In the Matter of the Application of:", 10)
    for left, right in (
        (f"Name: {_fmt(a.name)}", f"NYSID: {_fmt(a.nysid)}"),
        (f"AKA(s): {_fmt(a.aka)}", f"Motorist ID #: {_fmt(a.motorist_id)}"),
        (f"Date of birth: {_fmt(a.dob)}", f"Phone: {_fmt(a.phone)}"),
    ):
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(width / 2, 5, _safe_latin1(left))
        pdf.cell(width / 2, 5, _safe_latin1(right), new_x="LMARGIN", new_y="NEXT")
    body(f"Address: {_fmt(a.address)}  {_fmt(a.city_state_zip)}")
    rule()

    heading("The applicant moves to seal the following case(s):", 10)
    if not record.cases:
        body("No cases listed.", 9, "I")
    for index, case in enumerate(record.cases, start=1):
        body(f"{index}.  Docket / Indictment / SCI: {_fmt(case.docket_number)}", 9, "B")
        body(f"Court: {_fmt(case.court_name)}    County: {_fmt(case.county)}", 9, indent=6)
        body(f"Charge: {_fmt(case.conviction_charge)}    "
             f"Law/Section: {_fmt(case.law_section)}", 9, indent=6)
        body(f"Convicted: {_fmt(case.conviction_date)}    "
             f"Sentenced: {_fmt(case.sentence_date)}    "
             f"Term: {_fmt(case.sentence_term)}", 9, indent=6)
        body(f"Released from incarceration: {_fmt(case.release_date)}", 9, indent=6)
        result = case.screening.by_statute(CPL_160_59) if case.screening else None
        if result:
            body(f"Screening: {result.status} — {result.reason}", 8, "I", indent=6)
        pdf.ln(1)
    rule()

    heading("Venue and service", 10)
    body(f"File in: {_fmt(record.venue)}")
    counties = ", ".join(record.service_counties)
    body(f"Serve the District Attorney of: {_fmt(counties)}")
    body("The District Attorney has 45 days after service to consent to or oppose the "
         "sealing. If opposed, the court will hold a hearing.", 8, "I")
    rule()

    heading("Attachments", 10)
    for item in record.attachments:
        body(f"- {item}")
    rule()

    heading("Affidavit in Support", 10)
    body("The applicant states the following facts upon information and belief that they "
         "are true:")
    pdf.ln(1)
    body("I was convicted of a crime or crimes in no more than two criminal transactions "
         "in New York State or elsewhere, and no more than one of those criminal "
         "convictions includes a conviction for a felony offense. I do not have any open "
         "or pending criminal charges against me.", 9, indent=4)
    pdf.ln(1)
    body("I am not applying to seal any of the following offenses:", 9, indent=4)
    for letter, text in zip("abcdefgh", AFFIDAVIT_EXCLUSIONS):
        body(f"{letter}. {text}", 8.5, indent=10)
    pdf.ln(1)
    body("It has been over 10 years since I was sentenced for my most recent case. I did "
         "not count any jail or prison time I served after being sentenced in calculating "
         "the 10-year period.", 9, indent=4)
    pdf.ln(1)
    body(f"I {'have' if record.prior_application else 'have not'} filed any other "
         f"application to seal a conviction pursuant to either CPL 160.58 or CPL 160.59.", 9, indent=4)
    body(f"I {'do' if record.intends_further_application else 'do not'} intend to file any "
         f"other application to seal an eligible conviction.", 9, indent=4)
    rule()

    heading("Reasons the court should grant this application", 10)
    body(record.discretionary_factors or
         "[Required. Describe rehabilitation, employment, community ties, and any other "
         "reason the court should exercise its discretion.]",
         9, "" if record.discretionary_factors else "I")
    rule()

    if record.blocking_issues:
        heading("Before filing, resolve:", 10)
        for issue in record.blocking_issues:
            body(f"- {issue}", 8.5)

    output = pdf.output()
    return bytes(output)


def _safe_latin1(text: str) -> str:
    """fpdf2's built-in Helvetica is latin-1; anything outside it raises.

    Same guard as pdf_generator._safe, kept local so this module does not
    depend on that one's private helpers.
    """
    mapped = (str(text)
              .replace("‘", "'").replace("’", "'")
              .replace("“", '"').replace("”", '"')
              .replace("–", "-").replace("—", "-")
              .replace("…", "...").replace("•", "-")
              .replace(" ", " ").replace("é", "e"))
    return mapped.encode("latin-1", "replace").decode("latin-1")


# --- commercial-report audit ---------------------------------------------

def audit_instructions(targets: list, screening: Screening | None = None) -> list:
    """Practical, non-assertive steps for auditing commercial background reports.

    A seal binds DCJS, the police and the courts. It does not reach a
    consumer reporting agency that pulled the record before it sealed, which
    is why this exists at all -- and why it stays instructions rather than
    automation.
    """
    instructions = [
        "Request your NY DCJS criminal history and compare it with the Certificate of Disposition.",
        "Save the report date, source, and exact record identifiers before disputing a commercial report.",
    ]
    if screening is not None:
        partial = [p for p in screening.applicable if p.seal_scope == SCOPE_PARTIAL]
        if partial:
            instructions.append(
                "This record seals only partially — the court file stays public, so a "
                "vendor searching court records may still surface it lawfully.")
    instructions.extend(
        f"Request a file disclosure and dispute inaccurate or sealed information with {target}."
        for target in targets)
    return instructions
