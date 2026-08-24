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
    runtime_mode.reset_startup_default()
    yield
    runtime_mode.reset_fallback_db_path()
    runtime_mode.reset_runtime_override()
    runtime_mode.reset_startup_default()


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


# --- Streamlit Community Cloud secrets fallback -----------------------
# Community Cloud has no environment-variable UI, only a secrets editor.
# If demo mode could only be set by env var, a Community Cloud deployment
# would silently run as a *local* install: one shared tracker.db for every
# visitor, and live scans from the platform's IP.

def test_secrets_can_enable_demo_mode_when_no_env_var(monkeypatch):
    monkeypatch.setattr(runtime_mode, "_secret", lambda name: "true")
    assert runtime_mode.is_demo_mode() is True


def test_env_var_wins_over_secrets(monkeypatch):
    """A container sets the env var explicitly; it must not be overridden
    by a secrets file that happens to be baked into the image."""
    set_demo(monkeypatch, "false")
    monkeypatch.setattr(runtime_mode, "_secret", lambda name: "true")
    assert runtime_mode.is_demo_mode() is False


def test_absent_secrets_still_default_to_local(monkeypatch):
    monkeypatch.setattr(runtime_mode, "_secret", lambda name: None)
    assert runtime_mode.is_demo_mode() is False


def test_real_secret_helper_survives_absent_secrets_file():
    """The real _secret(), not a stub: with no secrets.toml on disk it must
    return None rather than propagating Streamlit's exception."""
    assert runtime_mode._secret(runtime_mode.DEMO_ENV_VAR) is None


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_secrets_truthy_values(monkeypatch, value):
    monkeypatch.setattr(runtime_mode, "_secret", lambda name: value)
    assert runtime_mode.is_demo_mode() is True


def test_demo_mode_from_secrets_routes_to_session_db(monkeypatch):
    """The whole point: a secrets-enabled demo must get an isolated
    per-session database, not the real tracker.db."""
    monkeypatch.setattr(runtime_mode, "_secret", lambda name: "true")
    assert runtime_mode.db_path() != config.TRACKER_DB_PATH
    assert runtime_mode.SESSION_DB_PREFIX in runtime_mode.db_path()


# --- the shape a session opens on -------------------------------------
# The desktop build is a separate install -- a local Python, a local
# Chrome for Playwright, a writable data/. A browser arriving at the app
# has none of that guaranteed, so a session that has chosen nothing starts
# online. What must not follow from that: the storage decisions moving,
# the badge claiming somebody forced the switch, or a later choice being
# overwritten on the next rerun.

def test_a_fresh_session_opens_on_the_online_build():
    assert runtime_mode.apply_startup_default() == runtime_mode.OVERRIDE_CLOUD
    assert runtime_mode.is_cloud_deployment() is True


def test_the_startup_default_is_not_described_as_manually_forced():
    """Nobody forced anything -- the session simply has not been switched
    off the shape it opened in, and saying otherwise in the badge is a
    claim about the user that is not true."""
    runtime_mode.apply_startup_default()
    label, help_text = runtime_mode.mode_badge()
    assert "CLOUD" in label
    assert "forced" not in help_text


def test_choosing_a_runtime_by_hand_is_still_reported_as_forced():
    runtime_mode.apply_startup_default()
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    _, help_text = runtime_mode.mode_badge()
    assert "forced" in help_text


def test_the_startup_default_does_not_fight_a_later_choice():
    """It seeds the opening position once. Re-applying on every rerun
    would drag a user who picked Desktop back to online on their next
    click."""
    runtime_mode.apply_startup_default()
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    runtime_mode.apply_startup_default()
    assert runtime_mode.get_runtime_override() == runtime_mode.OVERRIDE_DESKTOP
    assert runtime_mode.is_cloud_deployment() is False


def test_a_deliberate_return_to_auto_stays_auto():
    """The awkward case: "Auto-detect" is also what an untouched session
    reports, so a startup default that re-fired would be indistinguishable
    from the user never having chosen -- and would silently override the
    one option that means "stop overriding"."""
    runtime_mode.apply_startup_default()
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_AUTO)
    runtime_mode.apply_startup_default()
    assert runtime_mode.get_runtime_override() == runtime_mode.OVERRIDE_AUTO
    assert runtime_mode.is_cloud_deployment() is False


def test_the_startup_default_cannot_reach_demo_mode_or_the_gates(monkeypatch):
    """Same boundary every other override respects: it steers the
    deployment shape the UI describes, never where PII is written."""
    runtime_mode.apply_startup_default()
    assert runtime_mode.is_demo_mode() is False
    assert runtime_mode.db_path() == config.TRACKER_DB_PATH
    assert runtime_mode.live_scanning_enabled() is True
    assert runtime_mode.persistent_storage_enabled() is True


def test_auto_detection_itself_is_unchanged():
    """The default only decides which option a session opens on. Anyone
    who picks Auto-detect must get the same answer as before it existed."""
    runtime_mode.apply_startup_default()
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_AUTO)
    assert runtime_mode.is_cloud_deployment() is False
