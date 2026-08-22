import asyncio
import importlib.util
import json
from pathlib import Path

import data_broker_scanner


def _load_extractor():
    path = Path(__file__).parents[1] / "scripts" / "extract_platform_data.py"
    spec = importlib.util.spec_from_file_location("extract_platform_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_result_preserves_tri_state_and_contact_metadata():
    result = data_broker_scanner.normalize_result({
        "broker": "Example Broker",
        "verdict": "RECORD_FOUND",
        "record_urls": ["https://example.test/record/1"],
        "http_status": 200,
        "response_time_ms": 12.5,
        "compliance_email": "privacy@example.test",
        "contact_verified": True,
    })

    assert result["platform"] == "Example Broker"
    assert result["url"].endswith("/record/1")
    assert result["exists"] is True
    assert result["metadata"]["contact_verified"] is True


def test_manual_broker_result_is_not_false_negative():
    result = data_broker_scanner.normalize_result({
        "broker": "Blocked Broker",
        "verdict": "MANUAL_CHECK",
        "search_url": "https://example.test/search",
    })

    assert result["exists"] is None
    assert result["metadata"]["verdict"] == "MANUAL_CHECK"


def test_async_scanner_normalizes_injected_probe():
    async def probe(subject, brokers=None, **kwargs):
        return [{"broker": "Example", "verdict": "NO_RECORD", "search_url": "https://example.test"}]

    results = asyncio.run(data_broker_scanner.BrokerScanner(probe).scan({"name": "Jane Doe"}))

    assert results[0]["exists"] is False
    assert results[0]["platform"] == "Example"


def test_extractor_normalizes_local_snapshots(tmp_path):
    (tmp_path / "wmn-data.json").write_text(json.dumps({"sites": []}), encoding="utf-8")
    (tmp_path / "sherlock-data.json").write_text(json.dumps({
        "Example": {"url": "https://example.test/{}", "errorType": "message",
                     "errorMsg": "not found"}
    }), encoding="utf-8")
    (tmp_path / "maigret-data.json").write_text(json.dumps({"sites": {}}), encoding="utf-8")

    payload = _load_extractor().extract(tmp_path)

    site = next(site for site in payload["sites"] if site["platform"] == "Example")
    assert site["detection_type"] == "message"
    assert site["error_indicators"]["missing"] == ["not found"]