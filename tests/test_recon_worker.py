"""
Recon worker tests.

Every check runs through an injected fake fetcher -- the suite blocks
outbound sockets (conftest.py), and a privacy tool's own test run has no
business contacting data brokers regardless.
"""
from datetime import date, timedelta

import pytest

import campaign_manager
import recon_worker


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_recon.db")


@pytest.fixture
def campaign(db_path):
    campaign_id = campaign_manager.create_or_update_campaign(
        db_path, "Spokeo", "spokeo.com", "https://spokeo.com/p/123"
    )
    campaign_manager.mark_dispatched(
        db_path, campaign_id,
        dispatch_date=(date.today() - timedelta(days=10)).strftime(campaign_manager.DATE_FORMAT),
    )
    return campaign_id


def _fetcher(status_code):
    def fetch(url, timeout=None):
        return status_code
    return fetch


def _raising_fetcher(exc):
    def fetch(url, timeout=None):
        raise exc
    return fetch


# --- classification ---------------------------------------------------


@pytest.mark.parametrize("code", [404, 410])
def test_gone_status_codes_mean_removed(code):
    assert recon_worker.classify_status_code(code) == recon_worker.RESULT_REMOVED


def test_two_hundred_means_still_listed():
    assert recon_worker.classify_status_code(200) == recon_worker.RESULT_STILL_LISTED


@pytest.mark.parametrize("code", [403, 429, 500, 503, 301, None])
def test_ambiguous_codes_are_inconclusive_not_removed(code):
    """A Cloudflare 403 must never be mistaken for a successful delisting."""
    assert recon_worker.classify_status_code(code) == recon_worker.RESULT_INCONCLUSIVE


# --- endpoint checks --------------------------------------------------


def test_check_endpoint_reports_removed():
    result = recon_worker.check_endpoint("https://spokeo.com/p/1", fetcher=_fetcher(404))
    assert result["result"] == recon_worker.RESULT_REMOVED
    assert result["status_code"] == 404


def test_check_endpoint_swallows_network_errors():
    result = recon_worker.check_endpoint(
        "https://spokeo.com/p/1", fetcher=_raising_fetcher(TimeoutError("timed out"))
    )
    assert result["result"] == recon_worker.RESULT_INCONCLUSIVE
    assert "TimeoutError" in result["detail"]


@pytest.mark.parametrize("url", ["", None, "not-a-url"])
def test_check_endpoint_handles_missing_url(url):
    result = recon_worker.check_endpoint(url, fetcher=_fetcher(200))
    assert result["result"] == recon_worker.RESULT_INCONCLUSIVE


# --- ledger writes ----------------------------------------------------


def test_verify_delists_on_404(db_path, campaign):
    record = campaign_manager.get_campaign(db_path, campaign)
    recon_worker.verify_campaign(db_path, record, fetcher=_fetcher(404))

    updated = campaign_manager.get_campaign(db_path, campaign)
    assert updated["effective_status"] == campaign_manager.STATUS_DELISTED
    assert updated["date_verified_removed"] is not None
    assert "404" in updated["evidence_log"]


def test_verify_keeps_clock_running_on_200(db_path, campaign):
    record = campaign_manager.get_campaign(db_path, campaign)
    recon_worker.verify_campaign(db_path, record, fetcher=_fetcher(200))

    updated = campaign_manager.get_campaign(db_path, campaign)
    assert updated["effective_status"] == campaign_manager.STATUS_CLOCK_ACTIVE
    assert updated["last_recon_check"] == date.today().strftime(campaign_manager.DATE_FORMAT)


def test_inconclusive_check_does_not_close_a_live_case(db_path, campaign):
    record = campaign_manager.get_campaign(db_path, campaign)
    recon_worker.verify_campaign(db_path, record, fetcher=_fetcher(403))

    updated = campaign_manager.get_campaign(db_path, campaign)
    assert updated["effective_status"] == campaign_manager.STATUS_CLOCK_ACTIVE
    assert updated["date_verified_removed"] is None
    assert updated["last_recon_check"] is not None


# --- sweep ------------------------------------------------------------


def test_sweep_checks_every_active_campaign(db_path, campaign):
    second = campaign_manager.create_or_update_campaign(
        db_path, "MyLife", "mylife.com", "https://mylife.com/p/9"
    )
    campaign_manager.mark_dispatched(db_path, second)

    results = recon_worker.run_sweep(db_path, fetcher=_fetcher(404))
    assert len(results) == 2
    assert {r["result"] for r in results} == {recon_worker.RESULT_REMOVED}
    assert campaign_manager.get_active_campaigns(db_path) == []


def test_sweep_skips_already_delisted_campaigns(db_path, campaign):
    campaign_manager.record_delisting(db_path, campaign)
    assert recon_worker.run_sweep(db_path, fetcher=_fetcher(200)) == []


def test_sweep_respects_max_checks(db_path, campaign):
    campaign_manager.create_or_update_campaign(db_path, "MyLife", "mylife.com", "https://mylife.com/p/9")
    results = recon_worker.run_sweep(db_path, fetcher=_fetcher(200), max_checks=1)
    assert len(results) == 1


def test_sweep_is_gated_off_when_live_scanning_is_disabled(db_path, campaign, monkeypatch):
    """The hosted build must not make outbound requests for a visitor."""
    monkeypatch.setattr(recon_worker.runtime_mode, "live_scanning_enabled", lambda: False)
    assert recon_worker.run_sweep(db_path) == []


def test_sweep_results_carry_broker_identity(db_path, campaign):
    results = recon_worker.run_sweep(db_path, fetcher=_fetcher(200))
    assert results[0]["campaign_id"] == campaign
    assert results[0]["broker_name"] == "Spokeo"


# --- dork helper ------------------------------------------------------


def test_build_dork_url_targets_the_campaign_domain(db_path, campaign):
    record = campaign_manager.get_campaign(db_path, campaign)
    url = recon_worker.build_dork_url(record, "Jane Doe", "New York, NY")
    assert "spokeo.com" in url
    assert url.startswith("https://www.google.com/search?q=")
