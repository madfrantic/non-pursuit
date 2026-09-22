"""
Builds the complete audit-trail package as a single in-memory ZIP.

The point of this file is evidentiary. A deletion campaign's value in a
dispute is being able to show what was demanded, of whom, on what date,
and what the statutory clock was at the time -- spread across a SQLite
file and a browser session, none of that is portable. This bundles it
into one archive a user can hand to an attorney, attach to a complaint,
or simply keep.

Everything is assembled in memory (io.BytesIO) and never touched to disk.
That's required for the hosted build, which must not write user data to a
shared server, and it's harmless locally where the browser handles the
download anyway.

Letters are compiled through utils/letter_compiler, the same path the UI
renders from, so the archived copy is character-for-character what the
user saw and sent rather than a re-implementation that can drift.
"""
import csv
import io
import json
import zipfile
from datetime import datetime

from letter_compiler import compile_demand_letter, letter_filename, relational_disassociation_clause
import jurisdiction_router

DEMANDS_DIR = "ccpa_demands"
SUMMARY_NAME = "audit_summary.json"
VERIFICATION_NAME = "verification_log.csv"
README_NAME = "README_EVIDENCE.txt"

VERIFICATION_HEADERS = [
    "Platform", "Category", "Identifier", "Profile URL",
    "Confidence", "Status", "User Verified", "First Discovered", "Last Updated",
]

README_TEMPLATE = """NON-PURSUIT — AUDIT TRAIL PACKAGE
Generated: {generated_at}

WHAT THIS ARCHIVE IS
--------------------
A point-in-time record of a consumer data-deletion campaign conducted
under {frameworks_line}.

It was produced locally by Non-Pursuit from the user's own records. No
third party generated, reviewed, or holds a copy of its contents.

CONTENTS
--------
{summary_name}
    Structured record of every deletion request logged: recipient,
    channel, date sent, the response deadline computed from that date,
    current status, and whether the statutory window has lapsed. Also
    includes discovered online accounts and self-search exposure answers.

{demands_dir}/
    The demand letters as sent, one plain-text file per recipient. These
    are compiled from the same template and code path used to produce the
    letters delivered to each broker.

{verification_name}
    Log of accounts discovered during footprint reconnaissance, each with
    its confidence state and the date it was first observed and last
    updated.

STATUTORY BASIS AND DEADLINES
-----------------------------
{statutory_section}
Deadlines in {summary_name} are calculated as the date a request was sent
plus its response window ({default_window} days by default). Note that
these statutes generally run from RECEIPT, not from sending -- where those
differ, the recorded deadline is the more conservative of the two from the
recipient's perspective, and the actual statutory deadline may fall later.

CHAIN OF CUSTODY
----------------
Records in this archive were entered or confirmed by the user at the
timestamps shown. Dates reflect when each event was recorded in
Non-Pursuit, which is not independently notarized or witnessed. This
archive is a self-maintained record, and its evidentiary weight is a
matter for the recipient to assess.

This package is generated for record-keeping purposes. It is not legal
advice. For advice about a specific situation, consult a qualified
attorney licensed in the relevant jurisdiction.
"""


# One prose block per statutory framework, plus the short name used in the
# archive's opening line. The README has to describe what is actually in the
# ZIP: an archive whose letters are GDPR erasure demands must not open by
# announcing itself as a CCPA campaign, because the README is the part a
# recipient or an attorney reads first to orient themselves.
FRAMEWORK_NAMES = {
    jurisdiction_router.CCPA:
        "the California Consumer Privacy Act (CCPA), as amended by the "
        "California Privacy Rights Act (CPRA)",
    jurisdiction_router.GDPR:
        "the EU/UK General Data Protection Regulation (GDPR)",
    jurisdiction_router.NY_HYBRID:
        "New York General Business Law Article 25 and the New York SHIELD Act",
    jurisdiction_router.GENERIC:
        "the published deletion and opt-out policies of each recipient",
}

STATUTE_BLOCKS = {
    jurisdiction_router.CCPA: """California Civil Code § 1798.105 establishes a consumer's right to
request deletion of personal information held by a business.

Under § 1798.130(a)(2), a business must respond to a verifiable consumer
request within 45 days of receipt. That period may be extended by a
further 45 days where reasonably necessary, provided the consumer is
notified within the first 45-day window along with the reason for the
extension.
""",
    jurisdiction_router.GDPR: """Article 17 of Regulation (EU) 2016/679 (GDPR) establishes a data
subject's right to erasure. Where a controller relies on legitimate
interests under Article 6(1)(f), an objection under Article 21(1)
requires erasure unless the controller demonstrates overriding
compelling legitimate grounds.

Article 12(3) requires the controller to respond without undue delay and
in any event within one month of receipt. Article 19 requires the
controller to communicate the erasure to each recipient to whom the data
was disclosed.
""",
    jurisdiction_router.NY_HYBRID: """New York General Business Law Article 25 (§ 380 et seq.) governs
consumer reporting agencies operating in New York. § 380-f requires a
consumer reporting agency to reinvestigate disputed information within
thirty days and to delete information found inaccurate, incomplete, or
unverifiable. § 380-j prohibits the reporting of specified categories of
obsolete and adverse information.

New York General Business Law § 899-bb (the SHIELD Act) separately
requires any business holding the private information of a New York
resident to maintain reasonable administrative, technical, and physical
safeguards over it. § 899-bb is a data security statute and does not
itself create a right of erasure; it is invoked in these letters as to
data a recipient declines to delete.
""",
    jurisdiction_router.GENERIC: """Letters in this archive addressed to recipients outside an enacted
statutory deletion regime rely on each recipient's own published privacy
policy and opt-out procedure rather than on a specific statute. No
statutory response deadline attaches to those requests; the 45-day
window recorded for them is a record-keeping convention, not a legal
obligation on the recipient.
""",
}


def _iso(value=None) -> str:
    return (value or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _frameworks_line(template_types) -> str:
    """Render the opening 'conducted under ...' clause for the frameworks
    actually present, joined as readable English."""
    names = [FRAMEWORK_NAMES[t] for t in jurisdiction_router.PRECEDENCE if t in template_types]
    if not names:
        return FRAMEWORK_NAMES[jurisdiction_router.CCPA]
    if len(names) == 1:
        return names[0]
    return f"{'; '.join(names[:-1])}; and {names[-1]}"


def _statutory_section(template_types) -> str:
    """Concatenate one statute block per distinct framework in the batch.

    Ordered by jurisdiction_router.PRECEDENCE rather than by whatever order
    the brokers happened to be logged in, so two archives covering the same
    frameworks read identically. `template_types` is a set, so a batch with
    twenty CCPA letters still yields exactly one CCPA block.
    """
    blocks = [STATUTE_BLOCKS[t] for t in jurisdiction_router.PRECEDENCE if t in template_types]
    if not blocks:
        blocks = [STATUTE_BLOCKS[jurisdiction_router.CCPA]]
    return "\n".join(blocks)


def build_summary(requests, discovered, exposure_checks, generated_at=None,
                  target_profile=None, osint_findings=None,
                  template_types=None) -> dict:
    """The structured record. Kept separate from the ZIP assembly so its
    shape can be asserted in tests without unzipping anything.

    Only verified accounts (user confirmed: 'yes, that's me') are included
    in the audit, so false positives don't pollute the evidence package.
    """
    import discovered_accounts as da
    verified = [
        d for d in discovered
        if d.get("verification_status") == da.VERIFIED_CONFIRMED
    ]
    open_requests = [r for r in requests if r.get("status") != "Complete"]
    return {
        "generated_at": _iso(generated_at),
        "generator": "Non-Pursuit",
        "statutes": [
            jurisdiction_router.get(t).statute
            for t in jurisdiction_router.PRECEDENCE
            if t in set(template_types or {jurisdiction_router.CCPA})
        ],
        "totals": {
            "requests_logged": len(requests),
            "requests_open": len(open_requests),
            "requests_overdue": sum(1 for r in requests if r.get("is_overdue")),
            "accounts_discovered": len(discovered),
            "accounts_verified": len(verified),
        },
        "deletion_requests": [
            {
                "broker": r.get("broker_name"),
                "channel": r.get("channel"),
                "date_sent": r.get("date_sent"),
                "response_window_days": r.get("response_window_days"),
                "deadline": r.get("deadline"),
                "days_remaining": r.get("days_remaining"),
                "status": r.get("status"),
                "is_overdue": bool(r.get("is_overdue")),
            }
            for r in requests
        ],
        "discovered_accounts": [
            {
                "platform": d.get("platform"),
                "category": d.get("category"),
                "identifier": d.get("target_identifier"),
                "profile_url": d.get("profile_url"),
                "confidence": d.get("confidence"),
                "status": d.get("status"),
                "discovered_date": d.get("discovered_date"),
                "user_verified": d.get("verification_status") == da.VERIFIED_CONFIRMED,
            }
            for d in discovered
        ],
        "exposure_checks": exposure_checks or {},
        "osint_findings": osint_findings or {},
        "relational_entities": (target_profile or {}).get("relational_entities") or [],
        "relational_disassociation_clause": relational_disassociation_clause(target_profile or {}),
    }


def build_verification_log(discovered) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(VERIFICATION_HEADERS)
    for row in discovered:
        import discovered_accounts as da
        verified = row.get("verification_status") == da.VERIFIED_CONFIRMED
        writer.writerow([
            row.get("platform", ""),
            row.get("category", ""),
            row.get("target_identifier", ""),
            row.get("profile_url", ""),
            row.get("confidence", ""),
            row.get("status", ""),
            "YES" if verified else "NO",
            row.get("discovered_date", ""),
            row.get("updated_at", ""),
        ])
    return buffer.getvalue().encode("utf-8")


def build_readme(default_window: int, generated_at=None, template_types=None) -> bytes:
    """Render the evidence README for the frameworks actually in the batch.

    `template_types` is the set of jurisdiction_router template_type values
    the archive's letters were compiled from. It defaults to CCPA when
    omitted, which is what every archive produced before jurisdiction
    routing existed contained.
    """
    template_types = set(template_types or {jurisdiction_router.CCPA})
    return README_TEMPLATE.format(
        generated_at=_iso(generated_at),
        summary_name=SUMMARY_NAME,
        demands_dir=DEMANDS_DIR,
        verification_name=VERIFICATION_NAME,
        default_window=default_window,
        frameworks_line=_frameworks_line(template_types),
        statutory_section=_statutory_section(template_types),
    ).encode("utf-8")


def build_audit_package(requests, discovered, exposure_checks, target_profile,
                        record_url=None, default_window: int = 45,
                        generated_at=None, osint_findings=None,
                        broker_template_types=None) -> bytes:
    """Assemble the full archive and return its bytes.

    One letter is compiled per distinct broker in `requests`. Brokers are
    deduplicated because a campaign can legitimately log the same broker
    twice (an initial demand and a follow-up) and the archive wants one
    canonical letter per recipient, not two identical files.

    A missing record_url doesn't block compilation -- a request logged
    without one still gets its letter, with that line rendered empty,
    rather than being silently dropped from the evidence package.

    Statutory template selection: `target_profile` carries `state` and
    `country` (from profile_state) so each letter routes through
    utils/jurisdiction_router the same way the Deletion Letters page does,
    rather than this archive silently defaulting to CCPA regardless of
    where the user actually lives.

    `broker_template_types`, if given, maps broker_name -> template_type
    for the specific letters the user actually reviewed and confirmed in
    the UI (components/letters.py records one entry per broker there).
    A broker present in that map always uses its recorded template_type,
    so the archived letter is byte-for-byte the letter the user saw and
    checked off -- not a fresh auto-route computed at export time, which
    could differ if the user overrode the router's default in the UI.
    Brokers absent from the map (logged before this existed, or added
    directly to the tracker) fall back to auto-routing off target_profile.
    """
    buffer = io.BytesIO()
    broker_template_types = broker_template_types or {}

    # Letters are compiled before anything is written so the README and the
    # summary can describe the frameworks actually present. Compiling into a
    # dict first costs one pass over the brokers and keeps the archive
    # internally consistent -- a README that cites CCPA over a folder of GDPR
    # letters is exactly the kind of contradiction that discredits an
    # evidence package.
    letters = {}
    used_template_types = set()
    for request in requests:
        broker = request.get("broker_name")
        if not broker or broker in letters:
            continue
        template_type = (
            broker_template_types.get(broker)
            or jurisdiction_router.route_template(target_profile)
        )
        used_template_types.add(template_type)
        letters[broker] = compile_demand_letter(
            broker, target_profile, record_url=record_url, template_type=template_type,
        )

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        summary = build_summary(
            requests, discovered, exposure_checks, generated_at, target_profile,
            osint_findings=osint_findings, template_types=used_template_types,
        )
        archive.writestr(SUMMARY_NAME, json.dumps(summary, indent=2, ensure_ascii=False))
        archive.writestr(VERIFICATION_NAME, build_verification_log(discovered))
        archive.writestr(
            README_NAME, build_readme(default_window, generated_at, used_template_types)
        )
        for broker, letter in letters.items():
            archive.writestr(f"{DEMANDS_DIR}/{letter_filename(broker)}", letter)

    buffer.seek(0)
    return buffer.getvalue()
