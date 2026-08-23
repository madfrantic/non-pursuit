"""
OSINT dossier as a PDF -- the discovered footprint and email exposures in
a form you can hand to someone or attach to a complaint.

Deliberately fpdf2 rather than the reportlab path used by
data_export.build_pdf_export(). Those two reports have different jobs:
that one is a campaign/deadline log built from tabular request rows,
where platypus' automatic table pagination earns its keep. This one is a
flat sequence of findings, and fpdf2 draws it in a fraction of the code
with no system binaries involved (the explicit reason pdfkit/wkhtmltopdf
is not an option here -- it would need a system package the demo machine
may not have).

Two fpdf2 sharp edges are handled up front rather than discovered during
a live demo:

  * The built-in Helvetica is a latin-1 font. Any character outside that
    range -- a smart quote pasted from a breach description, an emoji in
    a platform name, a non-Latin handle -- raises at render time. Every
    string goes through _safe() first.
  * multi_cell() raises "not enough horizontal space" when a single
    unbroken token is wider than the text column, which is exactly what a
    long profile URL is. _wrappable() inserts soft break points into any
    run of non-space characters past a threshold.
"""
import io
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List

import config

# Longest run of non-space characters we let through untouched. Past this
# a zero-information break is inserted so multi_cell always has somewhere
# to wrap -- picked to sit comfortably inside the text column at 9pt.
_MAX_TOKEN = 60

# Characters that show up constantly in scraped OSINT text and are not in
# latin-1. Mapped to sensible ASCII rather than dropped, so the sentence
# still reads correctly.
_TRANSLITERATIONS = {
    "‘": "'", "’": "'", "‚": ",", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", "•": "-", " ": " ",
    "→": "->", "✓": "v", "✔": "v", "✗": "x", "✘": "x",
}


def _safe(value: Any) -> str:
    """Any value as a string the core PDF fonts can actually encode."""
    if value is None:
        return ""
    text = str(value)
    for source, replacement in _TRANSLITERATIONS.items():
        text = text.replace(source, replacement)
    # Emoji and anything else still outside latin-1 is dropped rather than
    # turned into "?" noise -- a stray "?" in a platform name reads like
    # data, an absence reads like formatting.
    text = text.encode("latin-1", "ignore").decode("latin-1")
    return " ".join(text.split())


def _wrappable(text: str, max_token: int = _MAX_TOKEN) -> str:
    """Insert break opportunities into long unbroken tokens (URLs)."""
    pieces = []
    for token in text.split(" "):
        while len(token) > max_token:
            pieces.append(token[:max_token])
            token = token[max_token:]
        pieces.append(token)
    return " ".join(piece for piece in pieces if piece)


def _records(source: Any) -> List[dict]:
    """Accept either a scanner result dict or a bare list of records.

    The aggregator hands back {"status", "records", "count", ...}; some
    call sites already have the inner list. Both are supported so the
    caller never has to reach into the vector shape.
    """
    if not source:
        return []
    if isinstance(source, dict):
        candidates = source.get("records")
        if candidates is None:
            candidates = list(source.get("certificates", []))
            domain_info = source.get("domain_info")
            if domain_info and isinstance(domain_info, dict) and (domain_info.get("domain") or domain_info.get("registrar")):
                candidates = [domain_info, *candidates]
        source = candidates or []
    if not isinstance(source, Iterable) or isinstance(source, (str, bytes)):
        return []
    return [record for record in source if isinstance(record, dict)]


def _label_for(record: dict) -> str:
    """The most identifying field a record happens to carry."""
    for key in ("platform", "service", "site", "entity_name", "case_name",
                "contributor_name", "subdomain", "domain", "repository", "name", "email"):
        if record.get(key):
            return _safe(record[key])
    return "Finding"


def _details_for(record: dict) -> List[str]:
    """Supporting lines, in a stable order, skipping anything absent."""
    ordered = (
        ("Category", "category"),
        ("Vector", "vector"),
        ("Confidence", "confidence"),
        ("Evidence", "reason"),
        ("Breach", "breach"),
        ("Description", "description"),
        ("Filing Type", "filing_type"),
        ("Date", "filing_date"),
        ("Date", "date"),
        ("Court", "court"),
        ("Docket", "docket_number"),
        ("Repository", "repository"),
        ("Recipient", "recipient"),
        ("Amount", "amount"),
        ("Target", "target_identifier"),
        ("Issuer", "issuer"),
        ("Registrar", "registrar"),
        ("URL", "profile_url"),
        ("URL", "court_url"),
        ("URL", "url"),
    )
    lines = []
    seen_url = False
    for label, key in ordered:
        value = record.get(key)
        if not value:
            continue
        if label == "URL":
            if seen_url:
                continue
            seen_url = True
        lines.append(f"{label}: {_safe(value)}")
    return lines


class _Dossier:
    """Thin wrapper so the section builders read as prose."""

    def __init__(self, pdf):
        self.pdf = pdf

    def heading(self, text: str) -> None:
        self.pdf.ln(3)
        self.pdf.set_font("Helvetica", "B", 12)
        self.pdf.set_text_color(37, 99, 235)
        self.pdf.multi_cell(0, 7, _safe(text), new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.ln(1)

    def body(self, text: str, size: int = 9, style: str = "", indent: int = 0) -> None:
        self.pdf.set_font("Helvetica", style, size)
        if indent:
            self.pdf.set_x(self.pdf.l_margin + indent)
        width = self.pdf.epw - indent
        self.pdf.multi_cell(width, 4.6, _wrappable(_safe(text)), new_x="LMARGIN", new_y="NEXT")

    def muted(self, text: str) -> None:
        self.pdf.set_text_color(110, 110, 110)
        self.body(text, size=9, style="I")
        self.pdf.set_text_color(0, 0, 0)

    def ln(self, height: float) -> None:
        self.pdf.ln(height)

    def rule(self) -> None:
        self.pdf.ln(1)
        y = self.pdf.get_y()
        self.pdf.set_draw_color(210, 210, 210)
        self.pdf.line(self.pdf.l_margin, y, self.pdf.w - self.pdf.r_margin, y)
        self.pdf.ln(2)


def _profile_value(profile: Dict[str, Any], *keys: str) -> str:
    """First non-empty of several possible key spellings.

    Callers pass either the OSINT profile shape ({"name", ...}) or the
    session profile shape ({"full_name", ...}); both reach this module.
    """
    for key in keys:
        value = (profile or {}).get(key)
        if value:
            return _safe(value)
    return ""


def _render_findings(doc: _Dossier, records: List[dict], empty_note: str) -> None:
    if not records:
        doc.muted(empty_note)
        return
    for index, record in enumerate(records, start=1):
        doc.body(f"{index}. {_label_for(record)}", size=10, style="B")
        for line in _details_for(record):
            doc.body(line, size=9, indent=5)
        doc.pdf.ln(1.5)


def generate_osint_pdf(
    profile: Dict[str, Any],
    footprint_results: Any = None,
    email_results: Any = None,
    github_results: Any = None,
    sec_results: Any = None,
    fec_results: Any = None,
    court_results: Any = None,
    infra_results: Any = None,
    **kwargs: Any,
) -> bytes:
    """Build the intelligence dossier and return the PDF as bytes.

    Accepts empty or partial input on purpose: a dossier for a scan that
    found nothing is still a useful artifact (it documents that the
    search was run), so no argument is required to be non-empty.
    """
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title("NON-PURSUIT: OSINT Intelligence Dossier")
    pdf.set_creator(config.APP_TITLE)
    pdf.add_page()

    doc = _Dossier(pdf)

    # --- header -------------------------------------------------------
    # The logo is optional: a missing or unreadable asset must not take
    # the whole export down mid-demo.
    logo_offset = 0
    try:
        if os.path.exists(config.APP_LOGO_PATH):
            pdf.image(config.APP_LOGO_PATH, x=pdf.l_margin, y=pdf.t_margin, w=14)
            logo_offset = 17
    except Exception:
        logo_offset = 0

    pdf.set_xy(pdf.l_margin + logo_offset, pdf.t_margin)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(37, 99, 235)
    pdf.multi_cell(pdf.epw - logo_offset, 8, "NON-PURSUIT: OSINT Intelligence Dossier",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(110, 110, 110)
    pdf.set_x(pdf.l_margin + logo_offset)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        pdf.epw - logo_offset, 4,
        _safe(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} - "
              f"{config.APP_TITLE} - passive OSINT, public sources only"),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    doc.rule()

    # --- target profile ----------------------------------------------
    doc.heading("Target Profile")
    fields = (
        ("Name", _profile_value(profile, "name", "full_name")),
        ("Handle", _profile_value(profile, "handle", "username")),
        ("Email", _profile_value(profile, "email", "email_address")),
        ("Domain", _profile_value(profile, "domain")),
        ("State", _profile_value(profile, "state")),
    )
    populated = [(label, value) for label, value in fields if value]
    if populated:
        for label, value in populated:
            doc.body(f"{label}: {value}", size=10)
    else:
        doc.muted("No profile details were supplied for this scan.")

    footprint = _records(footprint_results)
    emails = _records(email_results)
    github = _records(github_results)
    sec = _records(sec_results)
    fec = _records(fec_results)
    court = _records(court_results)
    infra = _records(infra_results)

    all_records = footprint + emails + github + sec + fec + court + infra

    breakdown = []
    if emails:
        breakdown.append(f"{len(emails)} email exposure{'s' if len(emails) != 1 else ''}")
    if footprint:
        breakdown.append(f"{len(footprint)} account footprint{'s' if len(footprint) != 1 else ''}")
    if github:
        breakdown.append(f"{len(github)} developer/code exposure{'s' if len(github) != 1 else ''}")
    if sec:
        breakdown.append(f"{len(sec)} SEC filing{'s' if len(sec) != 1 else ''}")
    if fec:
        breakdown.append(f"{len(fec)} FEC contribution{'s' if len(fec) != 1 else ''}")
    if court:
        breakdown.append(f"{len(court)} court docket{'s' if len(court) != 1 else ''}")
    if infra:
        breakdown.append(f"{len(infra)} domain/cert record{'s' if len(infra) != 1 else ''}")

    doc.ln(2)
    summary_text = f"Total findings: {len(all_records)}"
    if breakdown:
        summary_text += f" ({', '.join(breakdown)})"
    doc.body(summary_text, size=10, style="B")
    doc.rule()

    # --- findings -----------------------------------------------------
    doc.heading("Email & Identity Exposures")
    _render_findings(doc, emails,
                     "No email exposures were returned for this target.")

    doc.heading("Account & Platform Footprint")
    _render_findings(doc, footprint,
                     "No account footprint was returned for this target.")

    if github:
        doc.heading("Developer & Code Exposures")
        _render_findings(doc, github, "No code exposures found.")

    if sec:
        doc.heading("SEC Filings")
        _render_findings(doc, sec, "No SEC filings found.")

    if fec:
        doc.heading("FEC Contributions")
        _render_findings(doc, fec, "No FEC contributions found.")

    if court:
        doc.heading("Court Dockets")
        _render_findings(doc, court, "No court dockets found.")

    if infra:
        doc.heading("Domains & Infrastructure")
        _render_findings(doc, infra, "No domain/certificate records found.")

    doc.rule()
    doc.muted(
        "Compiled from passive, publicly accessible sources. Findings are leads "
        "for verification, not confirmed identity attributions."
    )

    output = pdf.output()
    # fpdf2 returns a bytearray; Streamlit's download_button wants bytes.
    return bytes(output) if not isinstance(output, bytes) else output


def build_dossier_bytes(profile: Dict[str, Any], findings: Dict[str, Any]) -> bytes:
    """Convenience wrapper: pull all vectors out of a full sweep result.

    run_full_osint_sweep() exposes each vector both at the top level and
    under "vectors"; presentation mode's mock mirrors that. Checking both
    keeps this working whichever shape the caller holds.
    """
    findings = findings or {}
    vectors = findings.get("vectors", {}) if isinstance(findings, dict) else {}
    def _get_vec(name):
        return findings.get(name) or vectors.get(name) or {}

    return generate_osint_pdf(
        profile,
        footprint_results=_get_vec("footprint"),
        email_results=_get_vec("email"),
        github_results=_get_vec("github"),
        sec_results=_get_vec("sec"),
        fec_results=_get_vec("fec"),
        court_results=_get_vec("courtlistener"),
        infra_results=_get_vec("infrastructure"),
    )
