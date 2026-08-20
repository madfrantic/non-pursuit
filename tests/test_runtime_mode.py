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
    monkeypatch.delenv(runtime_mode.DEPLOYMENT_ENV_VAR, raising=False)
    monkeypatch.delenv("STREAMLIT_SHARING_MODE", raising=False)
    runtime_mode.reset_fallback_db_path()
    runtime_mode.reset_runtime_override()
    yield
    runtime_mode.reset_fallback_db_path()
    runtime_mode.reset_runtime_override()


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
    assert "DESKTOP" in label or "Local" in label
    set_demo(monkeypatch, "true")
    label, _ = runtime_mode.mode_badge()
    assert "Demo" in label


# --- manual runtime override ------------------------------------------
# The presenter's switch between the desktop and cloud shapes. It steers
# what the UI reports; it must not reach the storage/PII decisions.

def test_override_defaults_to_auto():
    assert runtime_mode.get_runtime_override() == runtime_mode.OVERRIDE_AUTO


def test_forcing_cloud_overrides_local_detection():
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_CLOUD)
    assert runtime_mode.is_cloud_deployment() is True
    assert runtime_mode.is_local_mode() is False


def test_forcing_desktop_overrides_cloud_detection(monkeypatch):
    monkeypatch.setenv(runtime_mode.DEPLOYMENT_ENV_VAR, "production")
    assert runtime_mode.is_cloud_deployment() is True
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    assert runtime_mode.is_cloud_deployment() is False
    assert runtime_mode.is_local_mode() is True


def test_auto_hands_control_back_to_detection(monkeypatch):
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_CLOUD)
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_AUTO)
    assert runtime_mode.is_cloud_deployment() is False
    monkeypatch.setenv(runtime_mode.DEPLOYMENT_ENV_VAR, "cloud")
    assert runtime_mode.is_cloud_deployment() is True


@pytest.mark.parametrize("value", ["nonsense", "", None, "DESKTOP "])
def test_unrecognized_override_degrades_instead_of_raising(value):
    """A bad value out of a widget should fall back to real detection
    rather than break the page -- except for case/whitespace, which is
    normalized."""
    runtime_mode.set_runtime_override(value)
    if value == "DESKTOP ":
        assert runtime_mode.get_runtime_override() == runtime_mode.OVERRIDE_DESKTOP
    else:
        assert runtime_mode.get_runtime_override() == runtime_mode.OVERRIDE_AUTO


def test_override_cannot_enable_or_disable_demo_mode(monkeypatch):
    """Demo mode decides where PII is written and whether a shared host
    runs a real scan. A presentation toggle must not reach it."""
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_CLOUD)
    assert runtime_mode.is_demo_mode() is False

    set_demo(monkeypatch, "true")
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    assert runtime_mode.is_demo_mode() is True
    assert runtime_mode.db_path() != config.TRACKER_DB_PATH


def test_override_does_not_touch_the_feature_gates(monkeypatch):
    set_demo(monkeypatch, "true")
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    assert runtime_mode.browser_automation_enabled() is False
    assert runtime_mode.live_scanning_enabled() is False
    assert runtime_mode.persistent_storage_enabled() is False


def test_badge_and_capabilities_follow_the_override():
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_CLOUD)
    label, help_text = runtime_mode.mode_badge()
    assert "CLOUD" in label
    assert "forced" in help_text
    assert "Full username sweep (700+ sites)" in runtime_mode.get_capabilities_matrix()["🔴 Disabled"]

    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    label, help_text = runtime_mode.mode_badge()
    assert "DESKTOP" in label
    assert "forced" in help_text
    assert "🟢 Fully Enabled" in runtime_mode.get_capabilities_matrix()


def test_badge_says_nothing_about_forcing_when_auto():
    _, help_text = runtime_mode.mode_badge()
    assert "forced" not in help_text


def test_demo_badge_wins_over_a_forced_desktop(monkeypatch):
    """With demo mode on, the gates really are off -- claiming desktop
    capability here would be contradicted by the rest of the app."""
    set_demo(monkeypatch, "true")
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    label, _ = runtime_mode.mode_badge()
    assert "Demo" in label
