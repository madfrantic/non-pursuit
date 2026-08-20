"""Fully mocked coverage for the passive OSINT engine."""
import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from osint import STATUS_EMPTY, STATUS_SUCCESS, STATUS_UNAVAILABLE
from osint import courtlistener, fec, github, infrastructure, sec
from osint_aggregator import run_full_osint_sweep


@pytest.fixture(autouse=True)
def forbid_live_http(monkeypatch):
    """Any unmocked network attempt fails this test immediately."""
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda *args, **kwargs: pytest.fail("live HTTP request attempted"),
    )


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "module, scanner, private_name, raw, expected_key",
    [
        (sec, sec.scan_sec, "_search_filings", [{"_source": {"entity": {"name": "Acme"}}}], "records"),
        (courtlistener, courtlistener.scan_courtlistener, "_search_dockets", [{"case_name": "Doe v. Acme"}], "records"),
        (fec, fec.scan_fec, "_search_contributions", [{"contributor_name": "Jane Doe"}], "records"),
        (github, github.scan_github, "_fetch_public_events", [{"type": "PushEvent", "payload": {"commits": [{"author": {"email": "jane@example.com", "name": "Jane"}}]}, "repo": {"name": "acme/app"}}], "records"),
    ],
)
def test_individual_modules_normalize_mocked_responses(module, scanner, private_name, raw, expected_key):
    with patch.object(module, private_name, new=AsyncMock(return_value=raw)):
        result = run(scanner("Jane Doe" if module is not github else "janedoe"))
    assert result["status"] == STATUS_SUCCESS
    assert result[expected_key]
    assert result["count"] == len(result[expected_key])


def test_infrastructure_normalizes_mocked_crt_and_rdap():
    with patch.object(infrastructure, "_fetch_certificates", new=AsyncMock(return_value=[{"name_value": "www.example.com"}])), \
         patch.object(infrastructure, "_fetch_rdap", new=AsyncMock(return_value={"ldhName": "example.com", "status": ["active"]})):
        result = run(infrastructure.scan_infrastructure("example.com"))
    assert result["status"] == STATUS_SUCCESS
    assert result["certificates"][0]["subdomain"] == "www.example.com"
    assert result["domain_info"]["domain"] == "example.com"


def test_individual_modules_return_empty_without_identifiers():
    assert run(sec.scan_sec(""))["status"] == STATUS_EMPTY
    assert run(courtlistener.scan_courtlistener(""))["status"] == STATUS_EMPTY
    assert run(fec.scan_fec(""))["status"] == STATUS_EMPTY
    assert run(github.scan_github(""))["status"] == STATUS_EMPTY
    assert run(infrastructure.scan_infrastructure(""))["status"] == STATUS_EMPTY


def test_full_sweep_runs_all_five_and_normalizes_results():
    results = {
        "sec": {"status": STATUS_SUCCESS, "records": [{"entity_name": "Acme"}], "count": 1},
        "courtlistener": {"status": STATUS_EMPTY, "records": [], "count": 0},
        "fec": {"status": STATUS_SUCCESS, "records": [{"contributor_name": "Jane"}], "count": 1},
        "github": {"status": STATUS_EMPTY, "records": [], "count": 0},
        "infrastructure": {"status": STATUS_SUCCESS, "certificates": [{"subdomain": "www.example.com"}], "domain_info": {}, "cert_count": 1},
    }
    with patch("osint_aggregator.scan_sec", new=AsyncMock(return_value=results["sec"])), \
         patch("osint_aggregator.scan_courtlistener", new=AsyncMock(return_value=results["courtlistener"])), \
         patch("osint_aggregator.scan_fec", new=AsyncMock(return_value=results["fec"])), \
         patch("osint_aggregator.scan_github", new=AsyncMock(return_value=results["github"])), \
         patch("osint_aggregator.scan_infrastructure", new=AsyncMock(return_value=results["infrastructure"])), \
         patch("osint_aggregator._run_footprint", new=AsyncMock(return_value=[])), \
         patch("osint_aggregator._run_email", new=AsyncMock(return_value=[])):
        sweep = run(run_full_osint_sweep({"name": "Jane Doe", "handle": "janedoe", "domain": "example.com", "state": "CA"}))
    assert set(sweep) >= {"sec", "courtlistener", "fec", "github", "infrastructure", "footprint", "email", "vectors", "summary"}
    assert sweep["summary"]["total_exposures"] == 3
    assert sweep["vectors"]["corporate"]["records"] == results["sec"]["records"]


def test_full_sweep_contains_unavailable_for_scanner_failures():
    with patch("osint_aggregator.scan_sec", new=AsyncMock(side_effect=TimeoutError("SEC timeout"))), \
         patch("osint_aggregator.scan_courtlistener", new=AsyncMock(side_effect=RuntimeError("docket down"))), \
         patch("osint_aggregator.scan_fec", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})), \
         patch("osint_aggregator.scan_github", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})), \
         patch("osint_aggregator.scan_infrastructure", new=AsyncMock(side_effect=ConnectionError("crt down"))), \
         patch("osint_aggregator._run_footprint", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})), \
         patch("osint_aggregator._run_email", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})):
        sweep = run(run_full_osint_sweep({"name": "Jane Doe"}))
    assert sweep["sec"]["status"] == STATUS_UNAVAILABLE
    assert sweep["courtlistener"]["status"] == STATUS_UNAVAILABLE
    assert sweep["infrastructure"]["status"] == STATUS_UNAVAILABLE
    assert sweep["summary"]["vectors_available"] == 0


def test_certspotter_fallback_on_crtsh_504(monkeypatch):
    class MockResponse:
        def __init__(self, status, json_data=None):
            self.status = status
            self._json_data = json_data
            
        async def __aenter__(self):
            return self
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
        async def json(self):
            return self._json_data

    class MockClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url, **kwargs):
            if "crt.sh" in url:
                return MockResponse(504)
            if "api.certspotter.com" in url:
                return MockResponse(200, [{"dns_names": ["fallback.example.com"], "issuer": {"name": "Let's Encrypt"}}])
            return MockResponse(404)

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", MockClientSession)
    
    from osint.infrastructure import _fetch_certificates
    import asyncio
    certs = asyncio.run(_fetch_certificates("example.com", retries=1))
    
    assert len(certs) == 1
    assert certs[0]["name_value"] == "fallback.example.com"
