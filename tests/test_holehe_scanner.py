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
