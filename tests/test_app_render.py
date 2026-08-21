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

MASTER = "🔍 Intelligence Dossier"


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


def test_app_starts_fresh_without_cached_profile(monkeypatch, tmp_path):
    """Each new session starts with a blank profile for privacy.
    Previously saved profiles are not automatically loaded."""
    import database

    db = str(tmp_path / "tracker.db")
    database.init_db(db)
    database.insert_target_profile(db, {
        "first_name": "Jane", "last_name": "Doe",
        "current_city": "Austin", "current_state": "TX",
    })

    app = _run(monkeypatch, tmp_path)
    # Form should start with defaults, not loaded profile
    assert app.session_state["pf_city"] == "New York"  # Default
    assert app.session_state["pf_state"] == "NY"  # Default
    # Not the saved profile values
    assert app.session_state["pf_first_name"] == ""  # Empty, not "Jane"
    assert app.session_state["pf_last_name"] == ""  # Empty, not "Doe"


# --- Deletion Timeline ------------------------------------------------
# Same reasoning as the Master Dashboard tests above: the timeline's
# failure mode is a render-time Streamlit exception (a bad column count, a
# missing session key) that the campaign_manager unit tests would never
# see, because they never run the component.

TIMELINE = "🗓️ Deletion Timeline"


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_deletion_timeline_renders_without_exception(monkeypatch, tmp_path, demo):
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    assert not app.exception


def test_deletion_timeline_is_in_the_nav(monkeypatch, tmp_path):
    assert TIMELINE in _run(monkeypatch, tmp_path).radio(key="nav_mode").options


def test_deletion_timeline_renders_populated_campaigns(monkeypatch, tmp_path):
    """An empty pipeline takes the early-return path, so the table body
    only actually executes once there are rows to draw."""
    import campaign_manager

    db = str(tmp_path / "tracker.db")
    overdue = campaign_manager.create_or_update_campaign(
        db, "BeenVerified", "beenverified.com", "https://beenverified.com/p/1"
    )
    campaign_manager.mark_dispatched(db, overdue, dispatch_date="2026-01-01")
    delisted = campaign_manager.create_or_update_campaign(db, "TruePeople", "truepeoplesearch.com")
    campaign_manager.record_delisting(db, delisted)
    campaign_manager.create_or_update_campaign(db, "Nuwber", "nuwber.com")

    app = _run(monkeypatch, tmp_path)
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    assert not app.exception

    # Assert the rows were really drawn. Without this the test would still
    # pass if the db path failed to line up -- the page would just take the
    # empty early-return and prove nothing about the table body.
    metric_values = [m.value for m in app.metric]
    assert "3" in metric_values, f"expected 3 tracked campaigns, got {metric_values}"


def test_existing_nav_pages_all_survive_the_timeline_addition(monkeypatch, tmp_path):
    """The timeline is additive -- nothing already in the nav may vanish."""
    labels = _run(monkeypatch, tmp_path).radio(key="nav_mode").options
    for existing in [
        "🛡️ Profile",
        MASTER,
        "✉️ Data Broker Deletion",
        "⚖️ NY Expungement",
        "🚫 Google De-Indexing",
        "📬 Opt-Out Tracker",
    ]:
        assert existing in labels


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_presentation_mode_still_works_on_the_timeline(monkeypatch, tmp_path, demo):
    """Presentation mode seeds the demo profile from the sidebar, which
    renders before the page body -- the new page must not disturb that."""
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert app.session_state["presentation_mode"] is True
    assert app.session_state["pf_first_name"] == "Jane"


def test_opt_out_tracker_still_renders_alongside_the_new_ledger(monkeypatch, tmp_path):
    """Both tables live in one SQLite file; neither may break the other."""
    import campaign_manager
    import tracker

    db = str(tmp_path / "tracker.db")
    tracker.add_request(db, "Spokeo", "Email", config.CCPA_RESPONSE_WINDOW_DAYS)
    campaign_manager.create_or_update_campaign(db, "Spokeo", "spokeo.com")

    app = _run(monkeypatch, tmp_path)
    app.radio(key="nav_mode").set_value("📬 Opt-Out Tracker").run()
    assert not app.exception
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    assert not app.exception
