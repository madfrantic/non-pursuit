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


def test_normalize_result_email_list_retains_all_probed_results():
    from osint_aggregator import _normalize_result
    raw_checks = [
        {"platform": "Gravatar", "service": "Gravatar", "confidence": "CONFIRMED", "reason": "found"},
        {"platform": "Spotify", "service": "Spotify", "confidence": "POSSIBLE", "reason": "detected"},
        {"platform": "Pornhub", "service": "Pornhub", "confidence": "CONFIRMED", "reason": "found"},
        {"platform": "eBay", "service": "eBay", "confidence": "CONFIRMED", "reason": "found"},
    ]
    normalized = _normalize_result("email", raw_checks)
    assert normalized["status"] == STATUS_SUCCESS
    assert normalized["count"] == 4
    assert len(normalized["records"]) == 4


def test_normalize_result_syncs_dict_count_with_records_length():
    from osint_aggregator import _normalize_result
    out_of_sync = {
        "status": STATUS_SUCCESS,
        "records": [{"service": "Gravatar"}, {"service": "Spotify"}, {"service": "eBay"}],
        "count": 102,
    }
    normalized = _normalize_result("email", out_of_sync)
    assert normalized["count"] == 3
    assert len(normalized["records"]) == 3


def test_osint_sweep_score_matches_result_count_with_raw_checks():
    raw_email_checks = [
        {"platform": "Gravatar", "service": "Gravatar", "confidence": "CONFIRMED", "reason": "found"},
        {"platform": "Spotify", "service": "Spotify", "confidence": "POSSIBLE", "reason": "detected"},
        {"platform": "eBay", "service": "eBay", "confidence": "CONFIRMED", "reason": "detected"},
    ]

    raw_footprint = [
        {"platform": "GitHub", "confidence": "CONFIRMED", "reason": "exists"},
        {"platform": "Reddit", "confidence": "CONFIRMED", "reason": "exists"},
    ]

    with patch("osint_aggregator.scan_sec", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})), \
         patch("osint_aggregator.scan_courtlistener", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})), \
         patch("osint_aggregator.scan_fec", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})), \
         patch("osint_aggregator.scan_github", new=AsyncMock(return_value={"status": STATUS_EMPTY, "records": []})), \
         patch("osint_aggregator.scan_infrastructure", new=AsyncMock(return_value={"status": STATUS_EMPTY, "certificates": [], "domain_info": {}})), \
         patch("osint_aggregator._run_footprint", new=AsyncMock(return_value=raw_footprint)), \
         patch("osint_aggregator._run_email", new=AsyncMock(return_value=raw_email_checks)):
        sweep = run(run_full_osint_sweep({"name": "Jane Doe", "handle": "janedoe", "email": "jane@example.com"}))

    assert sweep["email"]["count"] == 3
    assert len(sweep["email"]["records"]) == 3
    assert sweep["footprint"]["count"] == 2
    assert len(sweep["footprint"]["records"]) == 2
    assert sweep["summary"]["total_exposures"] == 5
    assert sweep["summary"]["total_exposures"] == (
        sweep["sec"]["count"] +
        sweep["courtlistener"]["count"] +
        sweep["fec"]["count"] +
        sweep["github"]["count"] +
        sweep["infrastructure"]["count"] +
        sweep["footprint"]["count"] +
        sweep["email"]["count"]
    )




class TestFECContributorMatching:
    """The FEC contributor search is fuzzy; scan_fec must resolve it to one person.

    Regression cover for the bug where every record the API returned was
    normalized verbatim, so a "Jane Doe" sweep reported the donations of
    every unrelated Jane in the file.
    """

    SUBJECT = {"name": "Jane Doe", "state": "CA", "city": "Oakland", "zip_code": "94612"}

    @staticmethod
    def _record(name, state="CA", city="Oakland", zip_code="94612", **extra):
        # Field names match the real Schedule A response (contribution_
        # receipt_amount, not contribution_amount) -- a prior version of
        # this fixture used the wrong names and so never would have
        # caught the bug where the normalizer read them.
        record = {
            "contributor_name": name,
            "contributor_state": state,
            "contributor_city": city,
            "contributor_zip": zip_code,
            "contribution_receipt_amount": 500,
            "committee": {"name": "Some PAC"},
        }
        record.update(extra)
        return record

    def _scan(self, raw, subject=None):
        subject = subject or self.SUBJECT
        with patch.object(fec, "_search_contributions", new=AsyncMock(return_value=raw)):
            return run(fec.scan_fec(
                subject["name"],
                subject.get("state"),
                city=subject.get("city"),
                zip_code=subject.get("zip_code"),
            ))

    def test_confident_match_on_unique_individual(self):
        result = self._scan([self._record("DOE, JANE A")])
        assert result["status"] == STATUS_SUCCESS
        assert result["count"] == 1
        assert result["records"][0]["contributor_name"] == "DOE, JANE A"
        assert result["records"][0]["match_confidence"] == fec.CONFIRMED

    def test_shared_first_name_is_not_a_match(self):
        """The original bug: same first name, different surname."""
        raw = [
            self._record("SMITH, JANE"),
            self._record("ROE, JANE"),
            self._record("DOE, JANE"),
        ]
        result = self._scan(raw)
        assert result["count"] == 1
        assert result["records"][0]["contributor_name"] == "DOE, JANE"

    def test_shared_first_name_only_yields_no_match(self):
        raw = [self._record("SMITH, JANE"), self._record("ROE, JANE")]
        result = self._scan(raw)
        assert result["status"] == STATUS_EMPTY
        assert result["records"] == []
        assert "full name" in result["reason"]

    def test_same_full_name_different_state_is_rejected(self):
        """A Jane Doe in Texas is not the Jane Doe in California."""
        result = self._scan([self._record("DOE, JANE", state="TX", city="Austin", zip_code="73301")])
        assert result["status"] == STATUS_EMPTY
        assert result["records"] == []

    def test_ambiguous_namesakes_report_no_confident_match(self):
        """Two same-named people, nothing in the profile to tell them apart."""
        raw = [
            self._record("DOE, JANE", state="CA", city="Oakland", zip_code="94612"),
            self._record("DOE, JANE", state="NY", city="Albany", zip_code="12207"),
        ]
        bare = {"name": "Jane Doe"}
        result = self._scan(raw, subject=bare)
        assert result["status"] == STATUS_EMPTY
        assert result["records"] == []
        assert "share this name" in result["reason"]
        assert result["screened"] == 2

    def test_no_records_at_all_reports_empty(self):
        result = self._scan([])
        assert result["status"] == STATUS_EMPTY
        assert result["records"] == []

    def test_name_only_match_is_flagged_as_possible(self):
        """Nothing available to corroborate -> surfaced, but not as confirmed."""
        raw = [{"contributor_name": "Jane Doe", "contribution_receipt_amount": 250}]
        result = self._scan(raw, subject={"name": "Jane Doe"})
        assert result["status"] == STATUS_SUCCESS
        assert result["records"][0]["match_confidence"] == fec.POSSIBLE
        assert "no city, ZIP or employer" in result["reason"]

    def test_normalized_record_carries_real_amount_and_recipient(self):
        """Regression: the normalizer used to read contribution_amount,
        employer and occupation -- fields that don't exist on the real
        Schedule A response -- so every finding rendered as a $0 donation
        with no committee, regardless of the actual contribution."""
        raw = [self._record(
            "DOE, JANE",
            contribution_receipt_amount=2300.0,
            contribution_receipt_date="2008-12-09",
            contributor_employer="Acme Corp",
            contributor_occupation="Engineer",
        )]
        result = self._scan(raw)
        record = result["records"][0]
        assert record["amount"] == 2300.0
        assert record["date"] == "2008-12-09"
        assert record["recipient"] == "Some PAC"
        assert record["employer"] == "Acme Corp"
        assert record["occupation"] == "Engineer"

    def test_middle_initial_and_inverted_order_still_match(self):
        for variant in ("DOE, JANE A", "Jane A. Doe", "jane doe", "DOE, J"):
            result = self._scan([self._record(variant)])
            assert result["count"] == 1, variant

    def test_surname_match_alone_is_not_enough(self):
        result = self._scan([self._record("DOE, ROBERT")])
        assert result["status"] == STATUS_EMPTY
        assert result["records"] == []

    def test_employer_corroborates_when_locality_is_unknown(self):
        raw = [{"contributor_name": "DOE, JANE", "contributor_employer": "Acme Corp"}]
        with patch.object(fec, "_search_contributions", new=AsyncMock(return_value=raw)):
            result = run(fec.scan_fec("Jane Doe", employer="Acme Corp"))
        assert result["records"][0]["match_confidence"] == fec.CONFIRMED

    def test_count_always_matches_rendered_records(self):
        raw = [self._record("DOE, JANE"), self._record("SMITH, JANE"), self._record("DOE, JANE")]
        result = self._scan(raw)
        assert result["count"] == len(result["records"]) == 2
        assert result["screened"] == 3
