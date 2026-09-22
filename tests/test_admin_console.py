"""
Coverage for the owner-only console.

Two separate things are being asserted here and they fail differently. The
first is the lock itself -- utils/admin_auth.py, pure functions, testable
without a runtime. The second is that the lock is actually in front of the
page: a gate nothing calls is the failure mode that leaves every record in
the database one URL away, and no unit test of admin_auth would catch it.
"""
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import admin_auth  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
from components import nav  # noqa: E402

ADMIN_PAGE = os.path.join(ROOT, "pages", "11_Admin.py")
GOOD_KEY = "correct-horse-battery-staple"
TEST_PASSWORD = "admin-console-password"


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch):
    """The developer's own .env must not decide what these tests see."""
    monkeypatch.delenv(admin_auth.ADMIN_KEY_ENV, raising=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv(admin_auth.ADMIN_KEY_ENV, GOOD_KEY)
    return GOOD_KEY


# --- the lock ---------------------------------------------------------

def test_no_key_configured_means_no_admin_at_all():
    """An install that never set a secret has no answer that should open
    the console -- not even an empty one."""
    assert admin_auth.is_configured() is False
    assert admin_auth.verify("") is False
    assert admin_auth.verify(GOOD_KEY) is False


def test_the_configured_key_opens_it_and_nothing_else_does(configured):
    assert admin_auth.verify(GOOD_KEY) is True
    for wrong in ("", " ", GOOD_KEY.upper(), GOOD_KEY[:-1], GOOD_KEY + "x",
                  "correct-horse-battery-stapl3"):
        assert admin_auth.verify(wrong) is False, wrong


def test_a_whitespace_only_key_does_not_count_as_configured(monkeypatch):
    """`NON_PURSUIT_ADMIN_KEY=` in a .env is an unset key, not a blank
    password that anyone can leave the field empty to match."""
    monkeypatch.setenv(admin_auth.ADMIN_KEY_ENV, "   ")
    assert admin_auth.is_configured() is False
    assert admin_auth.verify("") is False
    assert admin_auth.verify("   ") is False


def test_a_short_key_is_reported_rather_than_enforced(monkeypatch):
    """Refusing a short key would lock the owner out of their own install
    over something they can fix in one line; saying so does not."""
    monkeypatch.setenv(admin_auth.ADMIN_KEY_ENV, "short")
    assert admin_auth.is_configured() is True
    assert admin_auth.key_is_weak() is True
    assert admin_auth.verify("short") is True


def test_a_long_key_is_not_flagged(configured):
    assert admin_auth.key_is_weak() is False


def test_authentication_lapses_when_the_key_is_removed(monkeypatch, configured):
    """A session that unlocked must not keep access after the secret is
    taken out of the environment."""
    state = {admin_auth.AUTHENTICATED_KEY: True}
    assert admin_auth.is_authenticated(state) is True
    monkeypatch.delenv(admin_auth.ADMIN_KEY_ENV)
    assert admin_auth.is_authenticated(state) is False


def test_sign_out_drops_the_flag(configured):
    state = {admin_auth.AUTHENTICATED_KEY: True}
    admin_auth.sign_out(state)
    assert admin_auth.is_authenticated(state) is False


def test_the_key_is_compared_as_a_digest_in_constant_time():
    """Reading the source is the only way to assert this -- a timing
    assertion would be flaky, and `==` passes every behavioural test here
    while leaking the matching prefix length."""
    import inspect

    source = inspect.getsource(admin_auth.verify)
    assert "compare_digest" in source
    assert "sha256" in source


# --- the lock is actually in front of the page ------------------------

def _run_admin_page(monkeypatch, tmp_path, unlock_vault=True):
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(config, "TRACKER_DB_PATH", db_path)
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "0")
    database.lock_vault()
    if unlock_vault:
        database.unlock_vault(TEST_PASSWORD, db_path)
    app = AppTest.from_file(ADMIN_PAGE, default_timeout=60)
    if unlock_vault:
        app.session_state["_vault_unlocked"] = True
    app.run()
    return app


def test_the_page_shows_no_records_without_the_admin_key(monkeypatch, tmp_path, configured):
    """The vault being unlocked is not enough. Someone who typed the URL
    gets the prompt, and none of the tables."""
    app = _run_admin_page(monkeypatch, tmp_path)
    assert not app.exception
    assert admin_auth.AUTHENTICATED_KEY not in app.session_state
    assert not app.dataframe, "the console rendered data before authenticating"


def test_a_wrong_key_does_not_authenticate(monkeypatch, tmp_path, configured):
    app = _run_admin_page(monkeypatch, tmp_path)
    app.text_input[0].set_value("not-the-key").run()
    app.button[0].click().run()
    assert not app.exception
    assert admin_auth.AUTHENTICATED_KEY not in app.session_state
    assert not app.dataframe


def test_the_right_key_opens_the_console(monkeypatch, tmp_path, configured):
    app = _run_admin_page(monkeypatch, tmp_path)
    app.text_input[0].set_value(GOOD_KEY).run()
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state[admin_auth.AUTHENTICATED_KEY] is True


def test_the_console_lists_a_saved_profile(monkeypatch, tmp_path, configured):
    """The point of the console: what the user typed into the profile form
    comes back out here."""
    app = _run_admin_page(monkeypatch, tmp_path)
    database.insert_target_profile(config.TRACKER_DB_PATH, {
        "first_name": "Adaeze", "last_name": "Okonkwo",
        "email_address": "adaeze@example.com", "current_city": "Brooklyn",
        "current_state": "NY",
    })
    app.text_input[0].set_value(GOOD_KEY).run()
    app.button[0].click().run()
    assert not app.exception
    assert app.dataframe, "no tables rendered after authenticating"
    frames = [d.value for d in app.dataframe]
    assert any("Adaeze" in frame.to_csv() for frame in frames)


def test_the_page_is_still_behind_the_vault_gate(monkeypatch, tmp_path, configured):
    """Two gates in series. A locked vault stops the page before the admin
    prompt is ever reached -- and before anything can be decrypted."""
    app = _run_admin_page(monkeypatch, tmp_path, unlock_vault=False)
    assert not app.exception
    assert not app.dataframe


# --- the sidebar link -------------------------------------------------

def test_the_admin_page_is_hidden_from_the_sidebar_by_default():
    """Not a permission check -- the page enforces its own gate -- but the
    console should not advertise itself to every visitor."""
    assert nav.SECTION_ADMIN in nav.ADMIN_ONLY_SECTIONS
    admin_pages = {
        path
        for section, links in nav.MPA_SECTIONS if section in nav.ADMIN_ONLY_SECTIONS
        for path, _, _ in links
    }
    assert "pages/11_Admin.py" in admin_pages


def test_the_admin_page_stays_in_the_registry_so_it_is_not_orphaned():
    """Hiding the link by deleting the MPA_SECTIONS entry would make the
    page unreachable rather than private, and would trip the reachability
    test in test_app_render.py."""
    linked = {path for _, links in nav.MPA_SECTIONS for path, _, _ in links}
    assert "pages/11_Admin.py" in linked
