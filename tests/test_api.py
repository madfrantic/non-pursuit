"""
Integration tests for api/main.py.

The pipeline's own modules are tested against their own inputs elsewhere;
what these tests cover is the seam -- that the API selects sites, hands them
to the scan, feeds the scan into the graph, feeds the graph into the letter
renderer, and returns a report whose numbers agree with each other.

Every outbound call is faked. All test targets use RFC 2606 reserved names
(example.com / .invalid), so a fake that leaks would fail loudly rather than
quietly probing a real person's account on a real platform.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api.main import app
from wmn_dataset import NSFW_CATEGORY


# ------------------------------------------------------------------ fixtures

SITES = [
    {"name": "ExampleSocial", "source": "whatsmyname", "cat": "social",
     "uri_check": "https://examplesocial.invalid/{account}",
     "uri_pretty": "https://examplesocial.invalid/{account}",
     "e_string": "profile", "e_code": 200, "m_string": "not found", "m_code": 404},
    {"name": "ExampleForum", "source": "sherlock", "cat": "forum",
     "uri_check": "https://exampleforum.invalid/u/{account}",
     "e_string": "member since", "e_code": 200, "m_code": 404},
    {"name": "ExampleAdult", "source": "maigret", "cat": NSFW_CATEGORY,
     "uri_check": "https://exampleadult.invalid/{account}",
     "e_code": 200, "m_code": 404},
]

REGISTRY = {"schema_version": "1.0", "sites": SITES,
            "counts": {"total": 3, "by_source": {"whatsmyname": 1, "sherlock": 1,
                                                 "maigret": 1}},
            "attribution": "test fixture"}

SCAN_ROWS = [
    {"platform": "ExampleSocial", "handle": "testsubject", "verdict": "CONFIRMED",
     "exists": True, "url": "https://examplesocial.invalid/testsubject",
     "profile_url": "https://examplesocial.invalid/testsubject",
     "http_status": 200, "reason": "matched", "category": "social",
     "source": "whatsmyname", "response_time_ms": 120.0,
     "metadata": {"emails": ["testsubject@example.com"],
                  "display_name": "Test Subject", "location": "Austin, TX",
                  "links": []}},
    {"platform": "ExampleForum", "handle": "testsubject", "verdict": "NOT_FOUND",
     "exists": False, "url": "https://exampleforum.invalid/u/testsubject",
     "http_status": 404, "reason": "no match", "category": "forum",
     "source": "sherlock", "response_time_ms": 90.0, "metadata": {}},
]

SUBJECT = {
    "handle": "testsubject",
    "name": "Test Subject",
    "email": "testsubject@example.com",
    "city": "Austin",
    "state": "CA",
}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """A client whose every outbound call is a local fake.

    The review queue is redirected to a tmp file too: it is the one store the
    API writes to on its own, and a test run must not leave rows in the
    developer's real data/review_queue.db.
    """
    monkeypatch.setattr(main, "REVIEW_QUEUE_DB", str(tmp_path / "review_queue.db"))

    async def fake_scan(account, sites, **kwargs):
        return [dict(row) for row in SCAN_ROWS]

    async def fake_probe(subject, brokers=None, **kwargs):
        return [{
            "broker": "ExampleData", "verdict": "RECORD_FOUND",
            "record_urls": ["https://exampledata.invalid/p/test-subject"],
            "search_url": "https://exampledata.invalid/search",
            "compliance_email": "privacy@exampledata.invalid",
            "optout_url": "https://exampledata.invalid/optout",
            "mode": "query", "last_verified": "2026-08", "reason": "match on name",
            "contact_verified": True, "notes": "", "http_status": 200,
        }]

    async def fake_contacts(exposures, **kwargs):
        return {row["platform"]: {
            "platform": row["platform"], "source": "curated",
            "emails": ["privacy@examplesocial.invalid"], "urls": [],
            "verified": True, "verified_on": "2026-08", "reason": "curated entry",
        } for row in exposures}

    async def fake_infra(hosts, **kwargs):
        return [{"host": h, "domain": h, "live": True, "mail_routable": True,
                 "operator": "Example Operator", "escalation_emails": [],
                 "tls": {"days_remaining": 200}, "fingerprint": {},
                 "dns": {"records": {}}, "registration": {}} for h in hosts]

    monkeypatch.setattr(main.recon_engine, "scan", fake_scan)
    monkeypatch.setattr(main.broker_probe, "probe_brokers", fake_probe)
    monkeypatch.setattr(main.privacy_contacts, "resolve_many", fake_contacts)
    monkeypatch.setattr(main.infra_checker, "check_hosts", fake_infra)
    monkeypatch.setattr(main.site_registry, "ensure_registry",
                        lambda data_dir="data", refresh=False, timeout=60:
                        (REGISTRY, "cached"))
    monkeypatch.setattr(main.broker_probe, "build_registry",
                        lambda *a, **k: [{"name": "ExampleData", "mode": "query"}])

    with TestClient(app) as client:
        yield client


# -------------------------------------------------------------------- health

def test_health_reports_coverage(wired):
    body = wired.get("/health").json()
    assert body["status"] in ("ok", "degraded")
    assert body["sites"] == 3
    assert body["brokers"] == 1
    assert body["version"] == main.API_VERSION
    assert body["registry_status"] == "cached"


def test_completed_job_has_report_and_payload_views(wired):
    submitted = wired.post("/api/scan", json={"subject": SUBJECT,
                                              "options": {"min_confidence": 40}})
    job_id = submitted.json()["job_id"]
    for _ in range(200):
        status = wired.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("done", "failed"):
            break
        asyncio.run(asyncio.sleep(0.01))

    assert status["status"] == "done"
    report = wired.get(f"/api/jobs/{job_id}/report")
    payloads = wired.get(f"/api/jobs/{job_id}/payloads")
    assert report.status_code == 200
    assert payloads.status_code == 200
    assert report.json()["exposures"]
    assert "payloads" in payloads.json()


def test_health_warns_when_registry_is_empty(monkeypatch):
    monkeypatch.setattr(main.site_registry, "ensure_registry",
                        lambda *a, **k: ({"sites": []}, "unavailable"))
    monkeypatch.setattr(main.broker_probe, "build_registry", lambda *a, **k: [])
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert any("site registry" in w for w in body["warnings"])


def test_health_survives_a_registry_that_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cache is corrupt")

    monkeypatch.setattr(main.site_registry, "ensure_registry", boom)
    monkeypatch.setattr(main.broker_probe, "build_registry", lambda *a, **k: [])
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/api/scan?wait=true",
                           json={"subject": SUBJECT}).status_code == 503


# --------------------------------------------------------------- /api/sites

def test_sites_coverage(wired):
    body = wired.get("/api/sites").json()
    assert body["total"] == 3
    assert body["by_source"] == {"maigret": 1, "sherlock": 1, "whatsmyname": 1}
    assert body["by_category"]["social"] == 1
    assert "WhatsMyName" in body["attribution"]      # licence notice travels
    assert body["registry_status"] == "cached"


def test_sites_filtered_by_source(wired):
    body = wired.get("/api/sites?source=sherlock").json()
    assert body["total"] == 1
    assert body["by_source"] == {"sherlock": 1}


def test_sites_refresh_failure_is_502(wired, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("upstream unreachable")

    monkeypatch.setattr(main.site_registry, "ensure_registry", boom)
    response = wired.get("/api/sites?refresh=true")
    assert response.status_code == 502
    assert "refresh failed" in response.json()["detail"]


# ---------------------------------------------------------------- /api/scan

def test_scan_end_to_end(wired):
    response = wired.post("/api/scan?wait=true",
                          json={"subject": SUBJECT,
                                "options": {"min_confidence": 40}})
    assert response.status_code == 200
    body = response.json()

    # The scan half.
    assert body["scan"]["sites_probed"] == 2
    assert body["scan"]["registry_status"] == "cached"

    # The scoring half: the confirmed account is scored, the miss is not.
    platforms = [e["platform"] for e in body["exposures"]]
    assert "ExampleSocial" in platforms
    assert "ExampleForum" not in platforms
    assert body["seeded"] is True

    # The remediation half: a letter was drafted for it.
    payloads = body["remediation"]["payloads"]
    assert payloads, "expected at least one drafted demand"
    platform_payload = next(p for p in payloads
                            if p.get("exposure_type") == "platform")
    assert "testsubject" in platform_payload["body"]
    assert "ExampleSocial" in platform_payload["body"]

    # And the infrastructure half ran on the exposed host only.
    assert body["infrastructure"]["summary"]["hosts"] == 1
    assert body["infrastructure"]["hosts"][0]["host"] == "examplesocial.invalid"


def test_scan_drafts_nothing_below_the_confidence_floor(wired):
    body = wired.post("/api/scan?wait=true",
                      json={"subject": SUBJECT,
                            "options": {"min_confidence": 99.5}}).json()
    platform_payloads = [p for p in body["remediation"]["payloads"]
                         if p.get("exposure_type") == "platform"]
    assert platform_payloads == []
    # A broker record found is still reported -- the floor is about identity
    # confidence in an account, not about brokers.
    assert body["brokers"]


def test_scan_never_marks_an_unsigned_template_ready_to_send(wired):
    """The platform template has no human sign-off; the API must say so."""
    body = wired.post("/api/scan?wait=true",
                      json={"subject": SUBJECT,
                            "options": {"min_confidence": 40}}).json()
    platform = next(p for p in body["remediation"]["payloads"]
                    if p.get("exposure_type") == "platform")
    assert platform["requires_human_signoff"] is True
    assert platform["ready_to_send"] is False
    assert body["remediation"]["awaiting_signoff"] >= 1


def test_scan_excludes_nsfw_unless_asked(wired, monkeypatch):
    seen = {}

    async def capture(account, sites, **kwargs):
        seen["sites"] = [s["name"] for s in sites]
        return []

    monkeypatch.setattr(main.recon_engine, "scan", capture)
    wired.post("/api/scan?wait=true", json={"subject": SUBJECT})
    assert "ExampleAdult" not in seen["sites"]

    wired.post("/api/scan?wait=true",
               json={"subject": SUBJECT, "options": {"include_nsfw": True}})
    assert "ExampleAdult" in seen["sites"]


def test_scan_caps_site_count(wired, monkeypatch):
    seen = {}

    async def capture(account, sites, **kwargs):
        seen["count"] = len(sites)
        return []

    monkeypatch.setattr(main.recon_engine, "scan", capture)
    wired.post("/api/scan?wait=true",
               json={"subject": SUBJECT, "options": {"max_sites": 1}})
    assert seen["count"] == 1


def test_scan_rejects_a_url_as_a_handle(wired):
    response = wired.post("/api/scan?wait=true",
                          json={"subject": {"handle": "https://x.invalid/foo"}})
    assert response.status_code == 422


def test_scan_rejects_out_of_range_options(wired):
    response = wired.post("/api/scan?wait=true",
                          json={"subject": SUBJECT, "options": {"concurrency": 5000}})
    assert response.status_code == 422


def test_scan_skips_broker_probe_without_a_name(wired, monkeypatch):
    called = []

    async def probe(*a, **k):
        called.append(True)
        return []

    monkeypatch.setattr(main.broker_probe, "probe_brokers", probe)
    body = wired.post("/api/scan?wait=true",
                      json={"subject": {"handle": "testsubject"}}).json()
    assert called == []            # a broker search needs a name, not a handle
    assert body["brokers"] == []


# ----------------------------------------------------------------- jobs

def test_scan_background_job_lifecycle(wired):
    submitted = wired.post("/api/scan", json={"subject": SUBJECT,
                                              "options": {"min_confidence": 40}})
    assert submitted.status_code == 200
    job_id = submitted.json()["job_id"]
    assert submitted.json()["status"] == "queued"

    for _ in range(200):
        status = wired.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("done", "failed"):
            break
        asyncio.run(asyncio.sleep(0.01))

    assert status["status"] == "done", status.get("error")
    result = wired.get(f"/api/jobs/{job_id}/result")
    assert result.status_code == 200
    assert result.json()["exposures"]


def test_job_result_before_completion_is_409(wired):
    app.state.jobs["pending"] = {
        "job_id": "pending", "status": "running", "submitted_at": "now",
        "progress": {}, "error": "", "result": None, "_created": 0}
    assert wired.get("/api/jobs/pending/result").status_code == 409
    app.state.jobs.pop("pending")


def test_unknown_job_is_404(wired):
    assert wired.get("/api/jobs/nope").status_code == 404
    assert wired.get("/api/jobs/nope/result").status_code == 404


def test_failed_job_reports_the_error(wired, monkeypatch):
    async def boom(account, sites, **kwargs):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(main.recon_engine, "scan", boom)
    job_id = wired.post("/api/scan", json={"subject": SUBJECT}).json()["job_id"]
    for _ in range(200):
        status = wired.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("done", "failed"):
            break
        asyncio.run(asyncio.sleep(0.01))
    assert status["status"] == "failed"
    assert "scan exploded" in status["error"]
    assert wired.get(f"/api/jobs/{job_id}/result").status_code == 500


def test_job_reaper_drops_expired_jobs():
    jobs = {"old": {"status": "done", "_created": 0},
            "fresh": {"status": "done",
                      "_created": main.datetime.now(main.timezone.utc).timestamp()}}
    main._reap_jobs(jobs)
    assert "old" not in jobs
    assert "fresh" in jobs


# ----------------------------------------------------------- /api/remediate

REVIEWED_EXPOSURE = {
    "platform": "ExampleSocial",
    "handle": "testsubject",
    "url": "https://examplesocial.invalid/testsubject",
    "confidence": 91.4,
    "band": "high",
    "verdict": "CONFIRMED",
    "evidence": [{"kind": "email", "detail": "profile email matches the seed",
                  "weight": 2.2}],
}


def test_remediate_renders_a_demand(wired):
    response = wired.post("/api/remediate", json={
        "subject": SUBJECT, "exposures": [REVIEWED_EXPOSURE]})
    assert response.status_code == 200
    body = response.json()

    payload = next(p for p in body["remediation"]["payloads"]
                   if p["exposure_type"] == "platform")
    assert "Test Subject" in payload["body"]
    assert "testsubject" in payload["body"]
    assert payload["delivery_channel"] == "privacy_email"
    assert body["dispatch"]["sent"] == 0


def test_remediate_routes_the_jurisdiction_from_the_subject(wired):
    ca = wired.post("/api/remediate", json={
        "subject": SUBJECT, "exposures": [REVIEWED_EXPOSURE]}).json()
    assert "1798.105" in ca["jurisdiction"]["statute"]
    assert ca["jurisdiction"]["response_window_days"] == 45

    eu = wired.post("/api/remediate", json={
        "subject": {**SUBJECT, "state": "", "country": "DE"},
        "exposures": [REVIEWED_EXPOSURE]}).json()
    assert "2016/679" in eu["jurisdiction"]["statute"]
    assert eu["jurisdiction"]["response_window_days"] == 30


def test_remediate_honours_the_confidence_floor(wired):
    body = wired.post("/api/remediate", json={
        "subject": SUBJECT,
        "exposures": [{**REVIEWED_EXPOSURE, "confidence": 50.0}],
        "min_confidence": 75.0,
        "probe_brokers": True}).json()
    assert [p for p in body["remediation"]["payloads"]
            if p["exposure_type"] == "platform"] == []


def test_remediate_rejects_an_empty_request(wired):
    response = wired.post("/api/remediate", json={"subject": SUBJECT})
    assert response.status_code == 422
    assert "nothing to remediate" in response.text


def test_remediate_can_run_brokers_only(wired):
    body = wired.post("/api/remediate", json={
        "subject": SUBJECT, "exposures": [], "probe_brokers": True}).json()
    assert body["brokers"]
    assert any(p["exposure_type"] == "broker"
               for p in body["remediation"]["payloads"])


def test_remediate_does_not_contact_platforms_when_asked_not_to(wired, monkeypatch):
    called = []

    async def resolver(exposures, **kwargs):
        called.append(kwargs.get("discover_missing"))
        return {}

    monkeypatch.setattr(main.privacy_contacts, "resolve_many", resolver)
    wired.post("/api/remediate", json={
        "subject": SUBJECT, "exposures": [REVIEWED_EXPOSURE],
        "discover_contacts": False})
    assert called == [False]


# ------------------------------------------------------------- /api/templates

def test_templates_expose_signoff_state(wired):
    body = wired.get("/api/templates").json()
    names = {t["name"]: t for t in body["templates"]}
    assert "ccpa_deletion_demand.j2" in names
    assert names["ccpa_deletion_demand.j2"]["signed_off"] is True
    assert names["platform_erasure_request.j2"]["signed_off"] is False


# ----------------------------------------------------------------- opt-out

def test_optout_brokers_lists_only_what_has_an_automator(wired):
    body = wired.get("/api/optout/brokers").json()
    assert body["brokers"] == list(main.optout_engine.supported_brokers())
    assert body["dry_run_only"] is True


def test_optout_trigger_queues_the_requested_broker(wired, monkeypatch):
    calls = []
    monkeypatch.setattr(main.optout_engine, "execute_optout",
                        lambda broker, target, email: calls.append(
                            (broker, target, email)) or {"status": "simulated_success"})

    response = wired.post("/api/optout/trigger", json={
        "first_name": "Test", "last_name": "Subject", "city": "Austin",
        "state": "CA", "age": "35", "email": "testsubject@example.com",
        "broker": "Example"})

    assert response.status_code == 200
    body = response.json()
    assert body["broker"] == "example"
    assert body["dry_run"] is True
    # The background task runs on the way out of the request.
    assert calls == [("example", {"first_name": "Test", "last_name": "Subject",
                                  "city": "Austin", "state": "CA", "age": "35"},
                      "testsubject@example.com")]


def test_optout_rejects_a_broker_with_no_automator(wired, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("an unsupported broker must not reach the engine")

    monkeypatch.setattr(main.optout_engine, "execute_optout", must_not_run)
    response = wired.post("/api/optout/trigger", json={
        "first_name": "Test", "last_name": "Subject", "city": "Austin",
        "state": "CA", "email": "testsubject@example.com", "broker": "spokeo"})

    assert response.status_code == 422
    assert "spokeo" in response.json()["detail"]


def test_optout_bounds_its_input(wired):
    response = wired.post("/api/optout/trigger", json={
        "first_name": "T" * 500, "last_name": "Subject",
        "email": "testsubject@example.com", "broker": "example"})
    assert response.status_code == 422


# ------------------------------------------------------ human review queue

def test_a_filled_form_leaves_a_task_for_a_person(wired, monkeypatch):
    """The engine fills and stops, so finishing the run is not finishing the job."""
    monkeypatch.setattr(main.optout_engine, "execute_optout",
                        lambda *a, **k: {"status": "simulated_success"})
    wired.post("/api/optout/trigger", json={
        "first_name": "Test", "last_name": "Subject",
        "email": "testsubject@example.com", "broker": "example"})

    pending = wired.get("/api/review").json()["pending"]
    assert [item["target"] for item in pending] == ["example"]
    assert pending[0]["reason"] == main.review_queue.SUBMIT_REQUIRES_SIGNOFF


def test_a_failed_run_queues_a_different_reason(wired, monkeypatch):
    monkeypatch.setattr(main.optout_engine, "execute_optout",
                        lambda *a, **k: {"status": "error", "error": "browser unavailable"})
    wired.post("/api/optout/trigger", json={
        "first_name": "Test", "last_name": "Subject",
        "email": "testsubject@example.com", "broker": "example"})

    pending = wired.get("/api/review").json()["pending"]
    assert pending[0]["reason"] == main.review_queue.SITE_UNREACHABLE


def test_review_queue_starts_empty_and_reports_its_vocabulary(wired):
    body = wired.get("/api/review").json()
    assert body["pending"] == []
    assert body["stats"]["total"] == 0
    assert main.review_queue.SUBMIT_REQUIRES_SIGNOFF in body["reasons"]


def test_a_review_item_is_resolved_by_id(wired, monkeypatch):
    monkeypatch.setattr(main.optout_engine, "execute_optout",
                        lambda *a, **k: {"status": "simulated_success"})
    wired.post("/api/optout/trigger", json={
        "first_name": "Test", "last_name": "Subject",
        "email": "testsubject@example.com", "broker": "example"})
    item_id = wired.get("/api/review").json()["pending"][0]["id"]

    body = wired.post(f"/api/review/{item_id}/resolve").json()
    assert body["changed"] is True
    assert body["stats"]["resolved"] == 1
    assert wired.get("/api/review").json()["pending"] == []


def test_resolving_an_unknown_review_item_is_404(wired):
    assert wired.post("/api/review/9999/resolve").status_code == 404


def test_an_unknown_resolve_outcome_is_422(wired, monkeypatch):
    monkeypatch.setattr(main.optout_engine, "execute_optout",
                        lambda *a, **k: {"status": "simulated_success"})
    wired.post("/api/optout/trigger", json={
        "first_name": "Test", "last_name": "Subject",
        "email": "testsubject@example.com", "broker": "example"})
    item_id = wired.get("/api/review").json()["pending"][0]["id"]

    response = wired.post(f"/api/review/{item_id}/resolve?outcome=mostly")
    assert response.status_code == 422


def test_a_broker_with_no_automator_never_reaches_the_queue(wired):
    """A 422 tells the caller directly; queueing it too would double-report."""
    wired.post("/api/optout/trigger", json={
        "first_name": "Test", "last_name": "Subject",
        "email": "testsubject@example.com", "broker": "spokeo"})
    assert wired.get("/api/review").json()["pending"] == []


def test_queue_rows_carry_no_subject_identity(wired, monkeypatch):
    """The queue is durable; it must not become a second store of PII."""
    monkeypatch.setattr(main.optout_engine, "execute_optout",
                        lambda *a, **k: {"status": "simulated_success"})
    wired.post("/api/optout/trigger", json={
        "first_name": "Testfirstname", "last_name": "Testlastname",
        "city": "Austin", "email": "testsubject@example.com", "broker": "example"})

    blob = json.dumps(wired.get("/api/review").json())
    for secret in ("Testfirstname", "Testlastname", "Austin", "testsubject@example.com"):
        assert secret not in blob


# -------------------------------------------------------------------- CORS

def test_cors_is_not_a_wildcard(monkeypatch):
    monkeypatch.delenv("NONPURSUIT_CORS_ORIGINS", raising=False)
    origins = main.cors_origins()
    assert "*" not in origins
    assert all(o.startswith("http://localhost") or o.startswith("http://127.0.0.1")
               for o in origins)


def test_cors_origins_configurable(monkeypatch):
    monkeypatch.setenv("NONPURSUIT_CORS_ORIGINS", "https://a.example, https://b.example")
    assert main.cors_origins() == ["https://a.example", "https://b.example"]
