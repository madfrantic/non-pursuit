import json

from data_export import build_json_export


def test_build_json_export_includes_all_sections():
    requests = [{"id": 1, "broker_name": "Spokeo", "status": "Sent"}]
    exposure_checks = {"email_breach": {"value": "Checked — clear", "checked_at": "2026-08-01"}}

    payload = json.loads(build_json_export(requests, exposure_checks))

    assert payload["campaign_requests"] == requests
    assert payload["exposure_checks"] == exposure_checks
    assert "exported_at" in payload


def test_build_json_export_handles_empty_data():
    payload = json.loads(build_json_export([], {}))
    assert payload["campaign_requests"] == []
    assert payload["exposure_checks"] == {}


def test_build_json_export_returns_bytes():
    result = build_json_export([], {})
    assert isinstance(result, bytes)
