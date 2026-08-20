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

from letter_compiler import compile_demand_letter, letter_filename

DEMANDS_DIR = "ccpa_demands"
SUMMARY_NAME = "audit_summary.json"
VERIFICATION_NAME = "verification_log.csv"
README_NAME = "README_EVIDENCE.txt"

VERIFICATION_HEADERS = [
    "Platform", "Category", "Identifier", "Profile URL",
    "Confidence", "Status", "First Discovered", "Last Updated",
]

README_TEMPLATE = """NON-PURSUIT — AUDIT TRAIL PACKAGE
Generated: {generated_at}

WHAT THIS ARCHIVE IS
--------------------
A point-in-time record of a consumer data-deletion campaign conducted
under the California Consumer Privacy Act (CCPA), as amended by the
California Privacy Rights Act (CPRA).

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

STATUTORY DEADLINES
-------------------
California Civil Code § 1798.105 establishes a consumer's right to
request deletion of personal information held by a business.

Under § 1798.130(a)(2), a business must respond to a verifiable consumer
request within 45 days of receipt. That period may be extended by a
further 45 days where reasonably necessary, provided the consumer is
notified within the first 45-day window along with the reason for the
extension.

Deadlines in {summary_name} are calculated as the date a request was sent
plus its response window ({default_window} days by default). Note that the
statute runs from RECEIPT, not from sending -- where those differ, the
recorded deadline is the more conservative of the two from the business's
perspective, and the actual statutory deadline may fall later.

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


def _iso(value=None) -> str:
    return (value or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def build_summary(requests, discovered, exposure_checks, generated_at=None) -> dict:
    """The structured record. Kept separate from the ZIP assembly so its
    shape can be asserted in tests without unzipping anything."""
    open_requests = [r for r in requests if r.get("status") != "Complete"]
    return {
        "generated_at": _iso(generated_at),
        "generator": "Non-Pursuit",
        "statute": "California Civil Code § 1798.105 (CCPA/CPRA)",
        "totals": {
            "requests_logged": len(requests),
            "requests_open": len(open_requests),
            "requests_overdue": sum(1 for r in requests if r.get("is_overdue")),
            "accounts_discovered": len(discovered),
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
            }
            for d in discovered
        ],
        "exposure_checks": exposure_checks or {},
    }


def build_verification_log(discovered) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(VERIFICATION_HEADERS)
    for row in discovered:
        writer.writerow([
            row.get("platform", ""),
            row.get("category", ""),
            row.get("target_identifier", ""),
            row.get("profile_url", ""),
            row.get("confidence", ""),
            row.get("status", ""),
            row.get("discovered_date", ""),
            row.get("updated_at", ""),
        ])
    return buffer.getvalue().encode("utf-8")


def build_readme(default_window: int, generated_at=None) -> bytes:
    return README_TEMPLATE.format(
        generated_at=_iso(generated_at),
        summary_name=SUMMARY_NAME,
        demands_dir=DEMANDS_DIR,
        verification_name=VERIFICATION_NAME,
        default_window=default_window,
    ).encode("utf-8")


def build_audit_package(requests, discovered, exposure_checks, target_profile,
                        record_url=None, default_window: int = 45,
                        generated_at=None) -> bytes:
    """Assemble the full archive and return its bytes.

    One letter is compiled per distinct broker in `requests`. Brokers are
    deduplicated because a campaign can legitimately log the same broker
    twice (an initial demand and a follow-up) and the archive wants one
    canonical letter per recipient, not two identical files.

    A missing record_url doesn't block compilation -- a request logged
    without one still gets its letter, with that line rendered empty,
    rather than being silently dropped from the evidence package.
    """
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        summary = build_summary(requests, discovered, exposure_checks, generated_at)
        archive.writestr(SUMMARY_NAME, json.dumps(summary, indent=2, ensure_ascii=False))
        archive.writestr(VERIFICATION_NAME, build_verification_log(discovered))
        archive.writestr(README_NAME, build_readme(default_window, generated_at))

        seen = set()
        for request in requests:
            broker = request.get("broker_name")
            if not broker or broker in seen:
                continue
            seen.add(broker)
            letter = compile_demand_letter(broker, target_profile, record_url=record_url)
            archive.writestr(f"{DEMANDS_DIR}/{letter_filename(broker)}", letter)

    buffer.seek(0)
    return buffer.getvalue()
