"""
Turns a scored exposure into a deletion demand addressed to somebody real.

This is the last stage of the pipeline and the only one whose output leaves
the machine, which shapes every decision in it.

WHY CONFIDENCE GATES REMEDIATION

The engine will not generate a demand for an exposure it is not confident
belongs to the user. That is not caution for its own sake -- a demand naming
an account the requester cannot connect to themselves gets a one-line
refusal, and worse, it teaches the recipient that requests from this sender
are noise. A 30%-confidence account is a lead for the user to confirm, not a
letter to send. The gate is identity_graph.BAND_HIGH by default and is
adjustable, because the user is entitled to overrule it for an account they
recognise on sight.

WHY BROKERS AND PLATFORMS RENDER DIFFERENTLY

A broker holds a dossier assembled without consent, and the demand is a
statutory deletion right asserted against a business that has never met you.
A platform holds an account you opened, and the demand is a data-subject
request against a service you have a relationship with. Those are different
letters. Brokers route through the existing, already-approved statutory
templates via letter_compiler; platforms use platform_erasure_request.j2.

HUMAN VALIDATION ZONE

This repo's CLAUDE.md designates demand-letter content a Human Validation
Zone: legally binding text requires manual sign-off before use. The broker
path is unaffected -- it renders templates that already have that sign-off,
through the same compiler, with no change to their text.

The platform template is new and therefore unsigned. Every payload rendered
from it comes back with requires_human_signoff=True and ready_to_send=False,
and the API surfaces both. This module will not mark that template
sign-off-complete on its own; a human edits SIGNED_OFF_TEMPLATES after
reviewing the text. Generating a draft for review is the useful thing to
automate. Deciding that a legal document is fit to send is not.
"""
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import jurisdiction_router
import letter_compiler
import privacy_contacts
from identity_graph import BAND_HIGH

TEMPLATES_DIR = Path(__file__).resolve().parent / "statutory_letters"
PLATFORM_TEMPLATE = "platform_erasure_request.j2"

# Templates a human has reviewed and approved for sending, per the Human
# Validation Zone in CLAUDE.md. The four statutory letters carry that
# sign-off from the Phase 1 review; the platform template does not yet.
# Adding an entry here is a human's decision, not a code change made on
# their behalf.
SIGNED_OFF_TEMPLATES = frozenset(letter_compiler.TEMPLATE_FILES)

# Every template type this pipeline can render, mapped to the file that
# renders it -- letter_compiler's four signed-off statutory letters, plus
# the one template remediation owns directly. This is the single mapping
# the API's template listing and any future caller should read off of,
# rather than each keeping its own type<->filename table that can drift out
# of sync with this one (that drift is exactly what api/models.TemplateType
# is built from this dict to prevent).
PLATFORM_TEMPLATE_TYPE = "platform_erasure"
ALL_TEMPLATES = {**letter_compiler.TEMPLATE_FILES, PLATFORM_TEMPLATE_TYPE: PLATFORM_TEMPLATE}

EXPOSURE_PLATFORM = "platform"
EXPOSURE_BROKER = "broker"


def _environment() -> Environment:
    # autoescape off: the output is plain text for an email body, and
    # HTML-escaping a name like "O'Brien" would corrupt the letter. Same
    # reasoning, and same setting, as letter_compiler.
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
    )


def _statute_basis(jurisdiction) -> str:
    """The 'basis for this request' paragraph, assembled from the routed
    jurisdiction rather than written per template.

    Keeping the citation in one place means a platform letter and a broker
    letter sent by the same person cite the same statute, and a correction
    to the routing logic reaches both.
    """
    return (
        f"This request is made under {jurisdiction.statute}. {jurisdiction.summary} "
        f"Where that framework does not reach you, I ask that you process this "
        f"request under your own published privacy policy and the deletion "
        f"procedure it describes."
    )


def deadline_for(jurisdiction, sent_on: datetime | None = None) -> str:
    """The date a response is due.

    Reported as information only. The Campaign Tracker's own deadline math
    is a Human Validation Zone with a signed-off day-45 convention, and this
    function deliberately does not feed it -- a second, differently-derived
    deadline reaching the ledger is exactly the drift that convention exists
    to prevent.
    """
    sent_on = sent_on or datetime.now()
    return (sent_on + timedelta(days=jurisdiction.response_window_days)).strftime("%Y-%m-%d")


def evidence_summary(exposure: dict, limit: int = 3) -> str:
    """One line naming why this exposure is attributed to the user.

    Carried into the payload record, not the letter. The recipient does not
    need the reasoning; the user does, at the moment they decide whether to
    send it.
    """
    items = exposure.get("evidence") or []
    if not items:
        return "no corroborating evidence beyond the account existing"
    return "; ".join(item.get("detail", "") for item in items[:limit] if item.get("detail"))


def render_platform_demand(exposure: dict, profile: dict, contact: dict,
                           jurisdiction=None, current_date: str | None = None) -> str:
    jurisdiction = jurisdiction or jurisdiction_router.route(profile)
    template = _environment().get_template(PLATFORM_TEMPLATE)
    return template.render(
        platform_name=exposure.get("platform", ""),
        account_handle=exposure.get("handle", ""),
        account_url=exposure.get("url", ""),
        user_name=profile.get("name", ""),
        user_location=profile.get("location", ""),
        user_email=profile.get("email", ""),
        statute_basis=_statute_basis(jurisdiction),
        response_window_days=jurisdiction.response_window_days,
        contact_verified=bool(contact.get("verified")),
        contact_reason=contact.get("reason", "an automated lookup"),
        current_date=current_date or datetime.now().strftime("%B %d, %Y"),
    )


def _payload(*, exposure_type, name, handle, url, confidence, band, contact,
             channel, subject, body, template, jurisdiction, evidence,
             notes="", last_verified="") -> dict:
    signed_off = template in SIGNED_OFF_TEMPLATES
    # channel["ready"] is False for CHANNEL_NONE and for
    # CHANNEL_VULNERABILITY_BOUNTY -- privacy_contacts surfaces a resolved
    # bug-bounty contact so the user can see something was found, but it is
    # never a channel this pipeline treats as sendable.
    channel_ready = channel.get("ready", channel["channel"] != privacy_contacts.CHANNEL_NONE)
    ready_to_send = signed_off and channel_ready

    reasons = []
    if not signed_off:
        reasons.append("template awaiting human sign-off (see CLAUDE.md HVZ)")
    if channel["channel"] == privacy_contacts.CHANNEL_NONE:
        reasons.append("no verified or discoverable contact")
    elif channel["channel"] == privacy_contacts.CHANNEL_VULNERABILITY_BOUNTY:
        reasons.append(
            "resolved contact is a vulnerability-disclosure channel "
            f"({channel['target']}), not a privacy contact -- do not send a "
            "CCPA/GDPR demand here")

    return {
        "exposure_type": exposure_type,
        "target_name": name,
        "handle": handle,
        "url": url,
        "confidence": confidence,
        "band": band,
        "evidence_summary": evidence,
        "contact_source": contact.get("source", ""),
        "contact_verified": bool(contact.get("verified")),
        "contact_last_verified": last_verified or contact.get("verified_on", ""),
        "delivery_channel": channel["channel"],
        "delivery_target": channel["target"],
        "subject": subject,
        "body": body,
        "template": template,
        "jurisdiction": jurisdiction.label,
        "statute": jurisdiction.statute,
        "response_window_days": jurisdiction.response_window_days,
        "response_due": deadline_for(jurisdiction),
        "requires_human_signoff": not signed_off,
        # Deliverable only when a human has approved the text *and* there is
        # a real privacy channel to send it to. Either missing makes this a draft.
        "ready_to_send": ready_to_send,
        "blocked_reason": "; ".join(reasons),
        "notes": notes,
    }


def plan_platform_demands(scored_accounts: list, profile: dict, contacts: dict, *,
                          min_confidence: float = BAND_HIGH,
                          current_date: str | None = None) -> list:
    """A demand payload per account scoring at or above `min_confidence`."""
    jurisdiction = jurisdiction_router.route(profile)
    payloads = []

    for exposure in scored_accounts:
        if exposure.get("confidence", 0) < min_confidence:
            continue
        platform = exposure.get("platform", "")
        contact = contacts.get(platform) or {"source": privacy_contacts.SOURCE_NONE,
                                             "emails": [], "urls": [],
                                             "reason": "not resolved"}
        channel = privacy_contacts.best_channel(contact)
        payloads.append(_payload(
            exposure_type=EXPOSURE_PLATFORM,
            name=platform,
            handle=exposure.get("handle", ""),
            url=exposure.get("url", ""),
            confidence=exposure.get("confidence", 0),
            band=exposure.get("band", ""),
            contact=contact,
            channel=channel,
            subject=(f"Data deletion request — account "
                     f"\"{exposure.get('handle', '')}\" on {platform}"),
            body=render_platform_demand(exposure, profile, contact, jurisdiction,
                                        current_date=current_date),
            template=PLATFORM_TEMPLATE,
            jurisdiction=jurisdiction,
            evidence=evidence_summary(exposure),
        ))
    return payloads


def plan_broker_demands(broker_rows: list, profile: dict, *,
                        include_manual: bool = True,
                        current_date: str | None = None) -> list:
    """A demand payload per broker holding, or possibly holding, a record.

    Manual-check rows are included by default and marked as such. A broker
    whose search could not be automated is not a broker that holds nothing,
    and a compliance package that quietly omits it overstates how clean the
    picture is.
    """
    from broker_probe import MANUAL_CHECK, RECORD_FOUND

    jurisdiction = jurisdiction_router.route(profile)
    template = jurisdiction.template_type
    payloads = []

    for row in broker_rows:
        verdict = row.get("verdict")
        if verdict == RECORD_FOUND:
            confidence, band = 95.0, "high"
        elif verdict == MANUAL_CHECK and include_manual:
            confidence, band = 50.0, "medium"
        else:
            continue

        contact = {
            "source": "data/brokers.csv" if row.get("contact_verified") else privacy_contacts.SOURCE_NONE,
            "emails": [row["compliance_email"]] if row.get("compliance_email") else [],
            "urls": [row["optout_url"]] if row.get("optout_url") else [],
            "verified": bool(row.get("contact_verified")),
            "verified_on": row.get("last_verified", ""),
            "reason": "data/brokers.csv",
        }
        channel = privacy_contacts.best_channel(contact)
        record_url = (row.get("record_urls") or [""])[0]

        payloads.append(_payload(
            exposure_type=EXPOSURE_BROKER,
            name=row.get("broker", ""),
            handle="",
            url=record_url or row.get("search_url", ""),
            confidence=confidence,
            band=band,
            contact=contact,
            channel=channel,
            subject=f"{jurisdiction.label} deletion request — {profile.get('name', '')}",
            body=letter_compiler.compile_demand_letter(
                row.get("broker", ""), profile, record_url=record_url,
                template_type=template, current_date=current_date),
            template=template,
            jurisdiction=jurisdiction,
            evidence=(f"broker search returned a matching record"
                      if verdict == RECORD_FOUND
                      else "broker could not be searched automatically — confirm by hand"),
            notes=row.get("notes", ""),
            last_verified=row.get("last_verified", ""),
        ))
    return payloads


def build_compliance_report(scored: dict, broker_rows: list, profile: dict,
                            contacts: dict, *, min_confidence: float = BAND_HIGH,
                            current_date: str | None = None) -> dict:
    """The finished artefact: what was found, how sure, and what to send."""
    jurisdiction = jurisdiction_router.route(profile)
    accounts = scored.get("accounts", [])

    platform_payloads = plan_platform_demands(
        accounts, profile, contacts, min_confidence=min_confidence,
        current_date=current_date)
    broker_payloads = plan_broker_demands(
        broker_rows, profile, current_date=current_date)
    payloads = platform_payloads + broker_payloads

    return {
        "generated": current_date or datetime.now().strftime("%Y-%m-%d"),
        "subject": {k: v for k, v in profile.items() if k != "email"} | {
            "email": profile.get("email", "")},
        "jurisdiction": {
            "label": jurisdiction.label,
            "statute": jurisdiction.statute,
            "response_window_days": jurisdiction.response_window_days,
            "note": jurisdiction_router.STATUTORY_WINDOW_NOTE,
        },
        "identity_confidence": scored.get("summary", {}),
        "seeded": scored.get("seeded", False),
        "exposures": accounts,
        "brokers": broker_rows,
        "remediation": {
            "min_confidence": min_confidence,
            "payloads": payloads,
            "ready_to_send": sum(1 for p in payloads if p["ready_to_send"]),
            "awaiting_signoff": sum(1 for p in payloads if p["requires_human_signoff"]),
            "no_contact": sum(1 for p in payloads if p["delivery_channel"] == "none"),
        },
        "caveats": _caveats(scored, payloads),
    }


def _caveats(scored: dict, payloads: list) -> list:
    """Everything about this report that a reader would otherwise have to
    infer. A compliance artefact that does not state its own limits invites
    being read as more certain than it is."""
    caveats = []
    if not scored.get("seeded"):
        caveats.append(
            "No identity seed was supplied, so every confidence score rests on "
            "handle rarity and inter-account links alone. Scores are materially "
            "weaker than they look without a name, email or location to corroborate against."
        )
    unsigned = [p for p in payloads if p["requires_human_signoff"]]
    if unsigned:
        caveats.append(
            f"{len(unsigned)} payload(s) render from a template that has not had the "
            "human sign-off this repo's CLAUDE.md requires for legally binding text. "
            "They are drafts for review, not documents to send."
        )
    unresolved = [p for p in payloads if p["delivery_channel"] == "none"]
    if unresolved:
        caveats.append(
            f"{len(unresolved)} exposure(s) have no verified or discoverable privacy "
            "contact. No address was constructed for them -- a guessed mailbox would "
            "start a statutory clock against somewhere that may not receive mail."
        )
    return caveats
