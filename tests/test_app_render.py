"""
Render-level coverage: the app script actually executes without raising.

Everything else in tests/ is a pure-function test, which is where the
real logic lives -- but the Master Dashboard merged two pages into one
script path, and the failure mode for that is a Streamlit exception at
render time that no unit test would ever see. AppTest runs the real
script the same way the server does.

The scan itself is never triggered here, so nothing in this file makes a
network request. Both runtime modes are exercised because they take
different branches: demo mode swaps in mock scan results and hides
browser automation.
"""
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_PATH = os.path.join(ROOT, "app.py")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402  -- needs ROOT on the path first

MASTER = "🛰️ Master Dashboard"


def _run(monkeypatch, tmp_path, demo=False):
    """Run the app against a throwaway database.

    Local mode resolves every store to config.TRACKER_DB_PATH, so that is
    the one knob that has to be redirected -- without it these tests
    would read and write the user's real campaign data. Demo mode routes
    to its own temp file already.
    """
    monkeypatch.setattr(config, "TRACKER_DB_PATH", str(tmp_path / "tracker.db"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "1" if demo else "0")
    app = AppTest.from_file(APP_PATH, default_timeout=60)
    app.run()
    return app


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_app_renders_without_exception(monkeypatch, tmp_path, demo):
    app = _run(monkeypatch, tmp_path, demo)
    assert not app.exception


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_master_dashboard_renders_without_exception(monkeypatch, tmp_path, demo):
    """The merged page is the whole point of this refactor -- if the two
    component renders can't coexist in one script run, it fails here."""
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value(MASTER).run()
    assert not app.exception


def test_master_dashboard_replaces_the_two_old_pages(monkeypatch, tmp_path):
    labels = _run(monkeypatch, tmp_path).radio(key="nav_mode").options
    assert MASTER in labels
    assert "🔍 Results" not in labels
    assert "🌐 Online Footprint" not in labels


def test_city_and_state_default_to_new_york(monkeypatch, tmp_path):
    app = _run(monkeypatch, tmp_path)
    assert app.session_state["pf_city"] == "New York"
    assert app.session_state["pf_state"] == "NY"


def test_a_saved_profile_beats_the_default(monkeypatch, tmp_path):
    """The prefill is a convenience for an empty form, not an override --
    it must never quietly replace a location the user actually saved."""
    import database

    db = str(tmp_path / "tracker.db")
    database.init_db(db)
    database.insert_target_profile(db, {
        "first_name": "Jane", "last_name": "Doe",
        "current_city": "Austin", "current_state": "TX",
    })

    app = _run(monkeypatch, tmp_path)
    assert app.session_state["pf_city"] == "Austin"
    assert app.session_state["pf_state"] == "TX"
