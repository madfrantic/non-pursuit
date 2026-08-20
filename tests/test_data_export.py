import csv
import io
import json

from data_export import build_json_export, build_csv_export, build_pdf_export


def test_build_json_export_includes_all_sections():
    requests = [{"id": 1, "broker_name": "Spokeo", "status": "Sent"}]
    exposure_checks = {"email_breach": {"value": "Checked — clear", "checked_at": "2026-08-01"}}
    discovered = [{"platform": "Reddit", "target_identifier": "alice"}]

    payload = json.loads(build_json_export(requests, exposure_checks, discovered))

    assert payload["campaign_requests"] == requests
    assert payload["exposure_checks"] == exposure_checks
    assert payload["discovered_accounts"] == discovered
    assert "exported_at" in payload


def test_build_json_export_handles_empty_data():
    payload = json.loads(build_json_export([], {}))
    assert payload["campaign_requests"] == []
    assert payload["exposure_checks"] == {}
    assert payload["discovered_accounts"] == []


def test_build_json_export_returns_bytes():
    result = build_json_export([], {})
    assert isinstance(result, bytes)


def test_build_csv_export_returns_bytes():
    assert isinstance(build_csv_export([]), bytes)


def test_build_csv_export_uses_readable_headers():
    result = build_csv_export([]).decode("utf-8")
    header_row = result.splitlines()[0]
    assert header_row == "Broker,Channel,Date Sent,Deadline,Status,Days Remaining,Notes"


def test_build_csv_export_includes_header_and_rows():
    requests = [{
        "broker_name": "Spokeo", "channel": "Email", "date_sent": "2026-08-01",
        "deadline": "2026-09-15", "status": "Sent", "days_remaining": 10, "notes": "",
    }]
    reader = csv.DictReader(io.StringIO(build_csv_export(requests).decode("utf-8")))
    rows = list(reader)
    assert rows[0]["Broker"] == "Spokeo"
    assert rows[0]["Status"] == "Sent"


def test_build_csv_export_ignores_extra_fields():
    requests = [{
        "broker_name": "Spokeo", "channel": "Email", "date_sent": "2026-08-01",
        "deadline": "2026-09-15", "status": "Sent", "days_remaining": 10, "notes": "",
        "id": 1, "is_overdue": False,
    }]
    build_csv_export(requests)  # must not raise on unexpected keys


def test_build_pdf_export_returns_pdf_bytes():
    result = build_pdf_export([], {})
    assert isinstance(result, bytes)
    assert result.startswith(b"%PDF")


def test_build_pdf_export_handles_populated_data():
    requests = [{
        "broker_name": "Spokeo", "channel": "Email", "date_sent": "2026-08-01",
        "deadline": "2026-09-15", "status": "Sent", "days_remaining": 10,
        "notes": "Some notes", "is_overdue": False,
    }]
    exposure_checks = {"email_breach": {"value": "Checked — clear", "checked_at": "2026-08-01"}}
    result = build_pdf_export(requests, exposure_checks)
    assert result.startswith(b"%PDF")


def test_build_pdf_export_many_rows_spans_pages():
    requests = [{
        "broker_name": f"Broker {i}", "channel": "Email", "date_sent": "2026-08-01",
        "deadline": "2026-09-15", "status": "Sent", "days_remaining": 10,
        "notes": "", "is_overdue": i % 2 == 0,
    } for i in range(60)]
    result = build_pdf_export(requests, {})
    assert result.startswith(b"%PDF")


def test_build_pdf_export_wraps_long_notes_instead_of_raising():
    long_note = (
        "Opt-out form requires the record URL plus an email confirmation link, and " * 10
        + "https://example-broker.com/very/long/record/url/that/would/overflow/a/fixed/width/cell/1234567890"
    )
    requests = [{
        "broker_name": "A Data Broker With A Genuinely Long Legal Name LLC",
        "channel": "Email", "date_sent": "2026-08-01", "deadline": "2026-09-15",
        "status": "Sent", "days_remaining": 10, "notes": long_note, "is_overdue": True,
    }]
    result = build_pdf_export(requests, {})
    assert result.startswith(b"%PDF")


def test_build_pdf_export_escapes_user_text():
    requests = [{
        "broker_name": "R&D <directory>", "channel": "Email", "date_sent": "2026-08-01",
        "deadline": "2026-09-15", "status": "Sent", "days_remaining": 10,
        "notes": "5 < 10 & 10 > 5", "is_overdue": False,
    }]
    result = build_pdf_export(requests, {"note<key>": {"value": "A & B", "checked_at": "2026-08-01"}})
    assert result.startswith(b"%PDF")
