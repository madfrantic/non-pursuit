"""Scope rules for the merged site registry, and the page that renders it.

The default for select_sites(include_nsfw=...) was flipped to True so a
footprint sweep covers the whole registry. Two things have to hold after that
flip, and neither is obvious from reading either module alone:

  * the HTTP API must NOT inherit the new default -- a remote caller still
    opts in explicitly, because it is scanning on someone else's behalf;
  * opening the OSINT Footprint page must not rebuild the registry, because
    ensure_registry() overwrites data/sites-unified.json and can write an
    empty one when its upstreams are unreachable.
"""
import json
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAGE = os.path.join(ROOT, "pages", "8_OSINT_Footprint.py")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
import database  # noqa: E402
import site_registry  # noqa: E402
from wmn_dataset import NSFW_CATEGORY  # noqa: E402

REGISTRY = {
    "schema_version": "1.0",
    "sites": [
        {"name": "Reddit", "cat": "social", "source": "whatsmyname",
         "uri_check": "https://reddit.com/user/{account}"},
        {"name": "ExampleAdult", "cat": NSFW_CATEGORY, "source": "maigret",
         "uri_check": "https://adult.example/{account}"},
        {"name": "StrictSite", "cat": "forum", "source": "sherlock",
         "uri_check": "https://strict.example/{account}",
         "regex_check": r"^[0-9]+$"},
    ],
}


def _names(sites):
    return {s["name"] for s in sites}


# --- the default ------------------------------------------------------

def test_nsfw_is_included_by_default():
    """The flip itself. A sweep that skips adult platforms reports a clean
    result it did not earn."""
    assert "ExampleAdult" in _names(site_registry.select_sites(REGISTRY))


def test_nsfw_can_still_be_excluded_explicitly():
    """Callers scanning on someone else's behalf keep a way out."""
    sites = site_registry.select_sites(REGISTRY, include_nsfw=False)
    assert "ExampleAdult" not in _names(sites)
    assert "Reddit" in _names(sites)


def test_api_request_default_did_not_follow_the_registry_default():
    """The one place the wider default must not leak. api/main._select_sites
    always passes options.include_nsfw, so this field's default is what a
    remote caller actually gets."""
    from api.models import ScanOptions

    assert ScanOptions().include_nsfw is False


# --- the other filters still compose ----------------------------------

def test_source_and_category_filters_still_apply():
    assert _names(site_registry.select_sites(
        REGISTRY, sources=("whatsmyname",))) == {"Reddit"}
    assert _names(site_registry.select_sites(
        REGISTRY, categories=["social"])) == {"Reddit"}


def test_account_filter_drops_sites_whose_regex_rejects_the_handle():
    """A site that cannot hold the handle is excluded here rather than
    probed and counted as a clean miss."""
    assert "StrictSite" not in _names(
        site_registry.select_sites(REGISTRY, account="nonpursuit"))
    assert "StrictSite" in _names(
        site_registry.select_sites(REGISTRY, account="12345"))


# --- the page does not rebuild the registry ---------------------------

@pytest.fixture
def unlocked(monkeypatch, tmp_path):
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(config, "TRACKER_DB_PATH", db_path)
    monkeypatch.setattr(config, "REVIEW_QUEUE_DB_PATH", str(tmp_path / "review.db"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "0")
    database.lock_vault()
    database.unlock_vault("page-render-password", db_path)
    yield db_path
    database.lock_vault()


def test_opening_the_page_never_rebuilds_the_registry(unlocked, monkeypatch):
    """ensure_registry() fetches three upstreams and writes the merged file.
    When every fetch fails and no cache exists it writes an EMPTY registry --
    so a render-time call would replace the real 2971-site file with nothing
    on any machine that happened to be offline."""
    def explode(*args, **kwargs):
        raise AssertionError("ensure_registry() called during a plain render")

    monkeypatch.setattr(site_registry, "ensure_registry", explode)

    app = AppTest.from_file(PAGE, default_timeout=60)
    app.session_state["_vault_unlocked"] = True
    app.run()

    assert not app.exception


def test_page_reports_the_full_registry_in_scope(unlocked, monkeypatch):
    """End-to-end: the scope line is the operator's only signal for how wide
    the sweep is about to go, so it has to report the whole registry now that
    NSFW is in by default.

    Skipped when the registry is absent -- data/sites-unified.json is
    gitignored and built at runtime, so a clean checkout legitimately has no
    file to render and the page shows its build-instructions empty state.
    """
    registry_path = os.path.join(ROOT, "data", "sites-unified.json")
    if not os.path.exists(registry_path):
        pytest.skip("merged registry not built in this checkout")

    with open(registry_path, encoding="utf-8") as handle:
        registry = json.load(handle)
    expected = len(site_registry.select_sites(registry))

    import recon_engine

    def no_scanning(*args, **kwargs):
        raise AssertionError("a render must not start a sweep")

    monkeypatch.setattr(recon_engine, "scan_sync", no_scanning)

    app = AppTest.from_file(PAGE, default_timeout=60)
    app.session_state["_vault_unlocked"] = True
    app.run()

    assert not app.exception
    captions = " ".join(str(c.value) for c in app.caption)
    assert f"{expected:,}" in captions

    # The whole point of the default flip: nothing is filtered out up front.
    assert expected == len(registry["sites"])


# --- the sweep path, with the async engine mocked ---------------------

def _row(platform, verdict, **extra):
    row = {
        "platform": platform, "category": "social", "handle": "nonpursuit",
        "url": f"https://{platform.lower()}.example/nonpursuit",
        "source": "whatsmyname", "verdict": verdict,
        "exists": verdict in ("CONFIRMED", "POSSIBLE"),
        "http_status": 200, "response_time_ms": 120.0, "metadata": {}, "reason": "",
    }
    row.update(extra)
    return row


def test_run_sweep_calls_the_async_engine_and_renders_a_dataframe(unlocked, monkeypatch):
    """The whole point of the page. scan_sync() is stubbed, so the suite
    never opens a socket -- what is under test is the wiring: the handle and
    the selected site list reach the engine, and the rows it returns come
    back as one dataframe rather than thousands of card widgets."""
    import recon_engine

    captured = {}

    def fake_scan_sync(account, sites, **kwargs):
        captured["account"] = account
        captured["site_count"] = len(sites)
        captured["kwargs"] = kwargs
        return [
            _row("Reddit", "CONFIRMED"),
            _row("Twitter", "POSSIBLE"),
            _row("Obscure", "NOT_FOUND"),
            _row("StrictSite", "SKIPPED", url="", http_status=None,
                 response_time_ms=None, reason="handle cannot be valid"),
        ]

    monkeypatch.setattr(recon_engine, "scan_sync", fake_scan_sync)

    app = AppTest.from_file(PAGE, default_timeout=60)
    app.session_state["_vault_unlocked"] = True
    app.run()

    app.text_input(key="osint_handle_input").input("nonpursuit").run()
    app.button(key="osint_run_sweep").click().run()

    assert not app.exception
    assert captured["account"] == "nonpursuit"
    assert captured["site_count"] > 0
    # Concurrency and timeout are the engine's, not re-invented by the page.
    assert captured["kwargs"]["concurrency"] == recon_engine.DEFAULT_CONCURRENCY
    assert captured["kwargs"]["timeout"] == recon_engine.DEFAULT_TIMEOUT

    assert len(app.dataframe) == 1, "results must render as a single dataframe"
    # Defaults to hits only: CONFIRMED + POSSIBLE, not the misses.
    shown = set(app.dataframe[0].value["Platform"])
    assert shown == {"Reddit", "Twitter"}


def test_sweep_results_can_be_widened_past_the_hits(unlocked, monkeypatch):
    """'Hits only' is a default, not a ceiling -- an ERROR or SKIPPED row is
    the one a user needs to see to know a site went untested."""
    import recon_engine

    monkeypatch.setattr(recon_engine, "scan_sync", lambda account, sites, **kw: [
        _row("Reddit", "CONFIRMED"),
        _row("Broken", "ERROR", reason="timeout"),
    ])

    app = AppTest.from_file(PAGE, default_timeout=60)
    app.session_state["_vault_unlocked"] = True
    app.run()
    app.text_input(key="osint_handle_input").input("nonpursuit").run()
    app.button(key="osint_run_sweep").click().run()

    assert set(app.dataframe[0].value["Platform"]) == {"Reddit"}

    hits_toggle = next(t for t in app.toggle if t.label == "Hits only")
    hits_toggle.set_value(False).run()

    assert not app.exception
    assert set(app.dataframe[0].value["Platform"]) == {"Reddit", "Broken"}
