"""
Tests for holehe_scanner: live multi-platform email intelligence probers.
All network calls are mocked to ensure zero outbound egress and deterministic test execution.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from holehe_scanner import (
    CONFIRMED,
    ERROR,
    NOT_FOUND,
    POSSIBLE,
    _make_result,
    probe_adobe,
    probe_chess,
    probe_duolingo,
    probe_github_email,
    probe_gravatar,
    probe_imgur,
    probe_libravatar,
    probe_pgp,
    probe_pinterest,
    probe_spotify,
    probe_substack,
    scan_email_async,
    scan_email_sync,
)


def run(coro):
    return asyncio.run(coro)


class MockResponse:
    def __init__(self, status=200, json_data=None, text_data=""):
        self.status = status
        self._json_data = json_data if json_data is not None else {}
        self._text_data = text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text_data


def test_probe_gravatar_found():
    mock_session = MagicMock()
    json_data = {"entry": [{"displayName": "Jane Doe", "id": "123"}]}
    mock_session.get.return_value = MockResponse(status=200, json_data=json_data)

    res = run(probe_gravatar("jane@example.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert "Gravatar" in res["platform"]
    assert "Jane Doe" in res["reason"]
    assert res["profile_url"].startswith("https://gravatar.com/")


def test_probe_gravatar_not_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=404)

    res = run(probe_gravatar("nonexistent@example.com", mock_session))
    assert res["confidence"] == NOT_FOUND


def test_probe_spotify_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=200, json_data={"status": 1, "errors": {"email": "already registered"}})

    res = run(probe_spotify("user@example.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert "Spotify" in res["platform"]


def test_probe_spotify_not_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=200, json_data={"status": 1, "errors": {}})

    res = run(probe_spotify("available@example.com", mock_session))
    assert res["confidence"] == NOT_FOUND


def test_probe_duolingo_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(
        status=200,
        json_data={"users": [{"username": "duouser", "picture": "//duolingo.com/avatar.jpg"}]}
    )

    res = run(probe_duolingo("duouser@example.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert "duouser" in res["reason"]
    assert res["avatar_url"] == "https://duolingo.com/avatar.jpg"


def test_probe_duolingo_not_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=200, json_data={"users": []})

    res = run(probe_duolingo("nouser@example.com", mock_session))
    assert res["confidence"] == NOT_FOUND


def test_probe_pinterest_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=200, json_data={"resource_response": {"data": True}})

    res = run(probe_pinterest("puser@example.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert "Pinterest" in res["platform"]


def test_probe_chess_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=200, json_data={"available": False})

    res = run(probe_chess("chessmaster@example.com", mock_session))
    assert res["confidence"] == CONFIRMED


def test_probe_chess_not_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=200, json_data={"available": True})

    res = run(probe_chess("newplayer@example.com", mock_session))
    assert res["confidence"] == NOT_FOUND


def test_probe_github_email_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(
        status=200,
        json_data={"items": [{"login": "octocat", "html_url": "https://github.com/octocat", "avatar_url": "https://github.com/avatar.png"}]}
    )

    res = run(probe_github_email("octo@github.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert "octocat" in res["reason"]


def test_probe_adobe_found():
    mock_session = MagicMock()
    mock_session.post.return_value = MockResponse(status=200, json_data=[{"id": "adobe_user"}])

    res = run(probe_adobe("designer@example.com", mock_session))
    assert res["confidence"] == CONFIRMED


def test_probe_substack_found():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(
        status=200,
        json_data={"id": 42, "name": "Author", "handle": "author"}
    )

    res = run(probe_substack("author@example.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert "author" in res["profile_url"]


def test_probe_imgur_found():
    mock_session = MagicMock()
    mock_session.post.return_value = MockResponse(status=200, json_data={"data": {"available": False}})

    res = run(probe_imgur("member@example.com", mock_session))
    assert res["confidence"] == CONFIRMED


def test_probe_rate_limit():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=429)

    res = run(probe_spotify("user@example.com", mock_session))
    assert res["confidence"] == POSSIBLE
    assert res["rate_limited"] is True


def test_scan_email_sync_rejects_empty():
    assert scan_email_sync("") == []
    assert scan_email_sync("   ") == []
    assert scan_email_sync("no-at-sign") == []


# ---------------------------------------------------------------------------
# Completeness regressions: probes that used to report a false negative
#
# Blackbird reported Adobe, Eventbrite and Chess.com hits that this scanner
# never surfaced. None of it was a filter downstream -- the probers themselves
# manufactured NOT_FOUND out of answers that were not negatives.
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from holehe_scanner import (  # noqa: E402
    PROBERS,
    _undetermined,
    probe_ebay,
    probe_eventbrite,
)


class CookieJarResponse(MockResponse):
    """MockResponse that also carries Set-Cookie values."""

    def __init__(self, status=200, json_data=None, text_data="", cookies=None):
        super().__init__(status=status, json_data=json_data, text_data=text_data)
        self.cookies = {
            name: SimpleNamespace(value=value)
            for name, value in (cookies or {}).items()
        }



def test_probe_chess_found_on_http_226():
    """A registered address answers 226 / isEmailAvailable=false, not 200."""
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(
        status=226, json_data={"isEmailAvailable": False, "reason": "Email In Use"})

    res = run(probe_chess("chessmaster@example.com", mock_session))
    assert res["confidence"] == CONFIRMED


def test_probe_chess_not_found_reads_live_field_name():
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(
        status=200, json_data={"isEmailAvailable": True, "reason": "Email Available"})

    res = run(probe_chess("newplayer@example.com", mock_session))
    assert res["confidence"] == NOT_FOUND


def test_probe_chess_226_with_unreadable_body_still_confirms():
    """226 is the "Email In Use" signal even if the body cannot be parsed."""
    mock_session = MagicMock()
    resp = MockResponse(status=226)
    resp._json_data = None
    mock_session.get.return_value = resp

    res = run(probe_chess("chessmaster@example.com", mock_session))
    assert res["confidence"] == CONFIRMED


def test_probe_adobe_sends_username_type_and_client_id():
    """Without both of these the IMS endpoint 400s for every address."""
    mock_session = MagicMock()
    mock_session.post.return_value = MockResponse(
        status=200, json_data=[{"status": {"code": "active"}}])

    run(probe_adobe("designer@example.com", mock_session))

    _, kwargs = mock_session.post.call_args
    assert kwargs["headers"]["X-Ims-Clientid"]
    assert '"usernameType": "EMAIL"' in kwargs["data"]


def test_probe_adobe_extracts_avatar():
    mock_session = MagicMock()
    mock_session.post.return_value = MockResponse(status=200, json_data=[{
        "status": {"code": "active"},
        "images": {"230": "https://pps.services.adobe.com/img/230"},
    }])

    res = run(probe_adobe("designer@example.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert res["avatar_url"] == "https://pps.services.adobe.com/img/230"


def test_probe_adobe_empty_list_is_a_real_negative():
    mock_session = MagicMock()
    mock_session.post.return_value = MockResponse(status=200, json_data=[])

    res = run(probe_adobe("nobody@example.com", mock_session))
    assert res["confidence"] == NOT_FOUND


def test_probe_eventbrite_found_with_user_id():
    mock_session = MagicMock()
    mock_session.get.return_value = CookieJarResponse(
        status=200, cookies={"csrftoken": "tok123"})
    mock_session.post.return_value = MockResponse(status=200, json_data={
        "user_id": "312969409971", "exists": True, "is_email_verified": False,
        "sign_in_methods": ["password"],
    })

    res = run(probe_eventbrite("organiser@example.com", mock_session))
    assert res["confidence"] == CONFIRMED
    assert res["others"]["user_id"] == "312969409971"

    _, kwargs = mock_session.post.call_args
    assert kwargs["headers"]["X-CSRFToken"] == "tok123"


def test_probe_eventbrite_not_found():
    mock_session = MagicMock()
    mock_session.get.return_value = CookieJarResponse(
        status=200, cookies={"csrftoken": "tok123"})
    mock_session.post.return_value = MockResponse(
        status=200, json_data={"exists": False})

    res = run(probe_eventbrite("nobody@example.com", mock_session))
    assert res["confidence"] == NOT_FOUND


def test_probe_eventbrite_without_csrf_is_undetermined_not_absent():
    mock_session = MagicMock()
    mock_session.get.return_value = CookieJarResponse(status=403, cookies={})

    res = run(probe_eventbrite("organiser@example.com", mock_session))
    assert res["confidence"] == POSSIBLE


def test_eventbrite_is_registered_in_the_sweep():
    assert "Eventbrite" in [name for name, _ in PROBERS]


def test_undetermined_never_asserts_absence_from_a_block():
    """403/405/412/418 mean the check failed, not that the account is absent."""
    for status in (400, 403, 405, 412, 418, 500, 502):
        res = _undetermined("Imgur", "media", "u@example.com", status)
        assert res["confidence"] == POSSIBLE, status


def test_undetermined_keeps_404_as_a_real_negative():
    res = _undetermined("OnlyFans", "adult", "u@example.com", 404)
    assert res["confidence"] == NOT_FOUND


def test_undetermined_flags_rate_limits():
    res = _undetermined("eBay", "commerce", "u@example.com", 429)
    assert res["confidence"] == POSSIBLE
    assert res["rate_limited"] is True


def test_blocked_probe_is_not_reported_as_a_clean_miss():
    """eBay answers 418 to unbrowsered clients; that is not "no account"."""
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse(status=418)

    res = run(probe_ebay("shopper@example.com", mock_session))
    assert res["confidence"] == POSSIBLE
