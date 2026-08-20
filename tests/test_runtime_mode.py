"""
Coverage for dual-mode runtime routing.

The load-bearing guarantee here is that local mode is completely
unaffected by any of this -- it must keep pointing at data/tracker.db --
while demo mode hands out an isolated database per session and turns off
the paths that are unsafe on a shared host.
"""
import pytest

import config
import runtime_mode


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(runtime_mode.DEMO_ENV_VAR, raising=False)
    runtime_mode.reset_fallback_db_path()
    yield
    runtime_mode.reset_fallback_db_path()


def set_demo(monkeypatch, value):
    monkeypatch.setenv(runtime_mode.DEMO_ENV_VAR, value)


# --- mode detection ---------------------------------------------------

def test_defaults_to_local_mode():
    assert runtime_mode.is_demo_mode() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", " true "])
def test_truthy_values_enable_demo_mode(monkeypatch, value):
    set_demo(monkeypatch, value)
    assert runtime_mode.is_demo_mode() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", ""])
def test_falsy_values_stay_local(monkeypatch, value):
    set_demo(monkeypatch, value)
    assert runtime_mode.is_demo_mode() is False


# --- database routing -------------------------------------------------

def test_local_mode_uses_the_real_tracker_db():
    assert runtime_mode.db_path() == config.TRACKER_DB_PATH


def test_demo_mode_never_uses_the_real_tracker_db(monkeypatch):
    set_demo(monkeypatch, "true")
    assert runtime_mode.db_path() != config.TRACKER_DB_PATH


def test_demo_mode_path_is_stable_within_a_session(monkeypatch):
    """Sequential _connect() calls must reach the same file, or writes
    vanish -- which is exactly how ':memory:' fails here."""
    set_demo(monkeypatch, "true")
    assert runtime_mode.db_path() == runtime_mode.db_path()


def test_new_session_paths_are_unique():
    first = runtime_mode.new_session_db_path()
    second = runtime_mode.new_session_db_path()
    assert first != second
    assert first.endswith(".db")
    assert runtime_mode.SESSION_DB_PREFIX in first


def test_demo_database_actually_persists_writes(monkeypatch):
    """The regression that motivated temp files over ':memory:'. Two
    writes through separate connections must both read back."""
    set_demo(monkeypatch, "true")
    import tracker

    path = runtime_mode.db_path()
    tracker.add_request(path, "Spokeo", "Email", 45, "")
    tracker.add_request(path, "MyLife", "Email", 45, "")
    assert len(tracker.get_all_requests(path)) == 2


def test_demo_sessions_are_isolated_from_each_other(monkeypatch):
    """One visitor's records must not be visible to the next."""
    set_demo(monkeypatch, "true")
    import tracker

    first = runtime_mode.new_session_db_path()
    second = runtime_mode.new_session_db_path()
    tracker.add_request(first, "Spokeo", "Email", 45, "")
    assert len(tracker.get_all_requests(first)) == 1
    assert len(tracker.get_all_requests(second)) == 0


# --- feature gates ----------------------------------------------------

def test_local_mode_enables_every_feature():
    assert runtime_mode.browser_automation_enabled() is True
    assert runtime_mode.live_scanning_enabled() is True
    assert runtime_mode.persistent_storage_enabled() is True


def test_demo_mode_disables_unsafe_features(monkeypatch):
    set_demo(monkeypatch, "true")
    assert runtime_mode.browser_automation_enabled() is False
    assert runtime_mode.live_scanning_enabled() is False
    assert runtime_mode.persistent_storage_enabled() is False


def test_mode_badge_reflects_current_mode(monkeypatch):
    label, _ = runtime_mode.mode_badge()
    assert "Local" in label
    set_demo(monkeypatch, "true")
    label, _ = runtime_mode.mode_badge()
    assert "Demo" in label
