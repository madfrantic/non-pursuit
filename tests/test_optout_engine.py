"""
Tests for utils/optout_engine.py.

No browser is started here. What these cover is the engine's refusals: an
unknown broker, a live-submission request, and a host with no Chrome all have
to come back as structured errors, because the only caller is a FastAPI
background task with nowhere to put an exception.
"""
import builtins

import optout_engine


def test_supported_brokers_matches_the_registry():
    assert optout_engine.supported_brokers() == tuple(sorted(optout_engine.BROKER_AUTOMATORS))


def test_unknown_broker_reports_what_is_supported():
    result = optout_engine.execute_optout("spokeo", {}, "masked@example.com")
    assert result["status"] == "error"
    assert "spokeo" in result["error"]
    assert result["supported"] == list(optout_engine.supported_brokers())


def test_live_submission_is_refused():
    """No automator has a signed-off submit path, so this cannot be honoured."""
    result = optout_engine.execute_optout(
        "example", {}, "masked@example.com", dry_run=False)
    assert result["status"] == "error"
    assert "not implemented" in result["error"]


def test_missing_playwright_is_an_error_not_an_import_crash(monkeypatch):
    """The API imports this module to expose the route; a browserless host must still boot."""
    real_import = builtins.__import__

    def no_playwright(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_playwright)
    result = optout_engine.execute_optout("example", {}, "masked@example.com")
    assert result == {"status": "error", "error": "playwright is not installed"}


def test_a_browser_that_will_not_start_is_reported(monkeypatch):
    class Boom:
        def __enter__(self):
            raise RuntimeError("Executable doesn't exist")

        def __exit__(self, *args):
            return False

    import playwright.sync_api as sync_api
    monkeypatch.setattr(sync_api, "sync_playwright", lambda: Boom())

    result = optout_engine.execute_optout("example", {}, "masked@example.com")
    assert result["status"] == "error"
    assert "browser unavailable" in result["error"]
