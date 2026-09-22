"""
Export everything this app has tracked about you.

data/tracker.db is the sole record of a real deletion campaign and
self-search history, with no backup path of its own -- if that file is
ever lost, so is the whole campaign. These give the user a plain,
portable copy they control, not tied to this app or this machine: JSON
for a full machine-readable copy, CSV for opening the campaign in a
spreadsheet, and a branded PDF for handing to someone else (an attorney,
a family member helping out) who just needs to read it.
"""
import csv
import io
import json
import os
from datetime import datetime
from xml.sax.saxutils import escape

import config

CSV_FIELDS = [
    "broker_name", "channel", "date_sent", "deadline",
    "status", "days_remaining", "notes",
]
CSV_HEADERS = ["Broker", "Channel", "Date Sent", "Deadline", "Status", "Days Remaining", "Notes"]


def build_json_export(requests: list[dict], exposure_checks: dict,
                      discovered_accounts: list[dict] | None = None) -> bytes:
    """requests: from tracker.get_all_requests(). exposure_checks: from
    exposure_store.get_all_checks(). Both are already plain dict/list data,
    so this just adds an export timestamp and serializes."""
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "campaign_requests": requests,
        "exposure_checks": exposure_checks,
        "discovered_accounts": discovered_accounts or [],
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def build_csv_export(requests: list[dict]) -> bytes:
    """The campaign requests table (deadline/days_remaining included, same
    as tracker.get_all_requests() computes them) as CSV, for opening in a
    spreadsheet. Exposure checks aren't tabular in the same way (one row
    per category, not per event) so they're left to the JSON export.

    Headers are human-readable labels (CSV_HEADERS), not the raw
    dict/column keys -- this file is meant to be opened and read, not
    re-imported, so "Broker" beats "broker_name" in row 1."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)
    for row in requests:
        writer.writerow([row.get(field, "") for field in CSV_FIELDS])
    return buffer.getvalue().encode("utf-8")


def _styled_table(rows, col_widths, primary_color):
    """rows[0] is the header row. Bold white-on-brand header, alternating
    whitesmoke/white body rows, full black grid -- the "formal audit
    log" look. Every cell is a Paragraph (not a raw string) specifically
    so long text -- a broker's notes field is the one column that can run
    long -- wraps inside its column instead of overflowing it."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), primary_color),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BOX", (0, 0), (-1, -1), 1, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.whitesmoke))
    table.setStyle(TableStyle(style_cmds))
    return table


def build_pdf_export(requests: list[dict], exposure_checks: dict) -> bytes:
    """A branded, human-readable snapshot of the campaign and exposure
    history -- for handing to someone who just needs to read it (an
    attorney, a family member helping out), not re-import it.

    Built with reportlab's platypus layer (SimpleDocTemplate + Table),
    which handles pagination and cell text-wrapping itself -- no manual
    y-position/page-break bookkeeping needed, unlike drawing directly on
    a Canvas.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=45, rightMargin=45, topMargin=45, bottomMargin=45,
        title="CCPA §1798.105 Demand Log",
    )
    primary_color = colors.HexColor(config.PRIMARY_COLOR)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("NPTitle", parent=styles["Title"], textColor=primary_color, spaceAfter=2)
    meta_style = ParagraphStyle("NPMeta", parent=styles["Normal"], textColor=colors.grey, fontSize=9)
    cell_style = ParagraphStyle("NPCell", parent=styles["Normal"], fontSize=8, leading=10)
    header_cell_style = ParagraphStyle(
        "NPHeaderCell", parent=cell_style, textColor=colors.white, fontName="Helvetica-Bold"
    )

    def cell_text(value) -> str:
        return escape(str(value))

    story = []
    if os.path.exists(config.APP_LOGO_PATH):
        logo = Image(config.APP_LOGO_PATH, width=0.55 * inch, height=0.55 * inch)
        logo.hAlign = "LEFT"
        story.append(logo)
        story.append(Spacer(1, 6))

    story.append(Paragraph("CCPA §1798.105 Demand Log", title_style))
    story.append(Paragraph(
        f"{config.APP_TITLE} — generated {datetime.now().strftime('%B %d, %Y %H:%M')} — "
        "for your records, not legal advice.",
        meta_style,
    ))
    story.append(Spacer(1, 14))

    total = len(requests)
    overdue = sum(1 for r in requests if r.get("is_overdue"))
    complete = sum(1 for r in requests if r.get("status") == "Complete")
    story.append(Paragraph(
        f"<b>Summary:</b> {total} tracked &middot; {complete} complete &middot; {overdue} overdue",
        styles["Normal"],
    ))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Deletion Requests", styles["Heading2"]))
    if requests:
        headers = ["Broker", "Channel", "Date Sent", "Deadline", "Status", "Notes"]
        table_rows = [[Paragraph(h, header_cell_style) for h in headers]]
        for r in requests:
            status_text = str(r.get("status", ""))
            if r.get("is_overdue"):
                status_text = f'<font color="{config.ERROR_COLOR}"><b>{status_text} (OVERDUE)</b></font>'
            table_rows.append([
                Paragraph(cell_text(r.get("broker_name", "")), cell_style),
                Paragraph(cell_text(r.get("channel", "")), cell_style),
                Paragraph(cell_text(r.get("date_sent", "")), cell_style),
                Paragraph(cell_text(r.get("deadline", "")), cell_style),
                Paragraph(status_text, cell_style),
                Paragraph(cell_text(r.get("notes") or "-"), cell_style),
            ])
        col_widths = [1.05 * inch, 0.75 * inch, 0.85 * inch, 0.85 * inch, 1.05 * inch, 1.65 * inch]
        story.append(_styled_table(table_rows, col_widths, primary_color))
    else:
        story.append(Paragraph("Nothing logged yet.", styles["Normal"]))

    story.append(Spacer(1, 18))
    story.append(Paragraph("Self-Search Exposure History", styles["Heading2"]))
    if exposure_checks:
        exp_rows = [[Paragraph(h, header_cell_style) for h in ["Category", "Result", "Checked"]]]
        for category, entry in sorted(exposure_checks.items()):
            exp_rows.append([
                Paragraph(cell_text(category), cell_style),
                Paragraph(cell_text(entry.get("value", "")), cell_style),
                Paragraph(cell_text(entry.get("checked_at", "")), cell_style),
            ])
        story.append(_styled_table(exp_rows, [2.3 * inch, 2.3 * inch, 1.6 * inch], primary_color))
    else:
        story.append(Paragraph("Nothing checked yet.", styles["Normal"]))

    doc.build(story)
    return buffer.getvalue()
