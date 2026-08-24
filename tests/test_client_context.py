"""utils/client_context.py -- who the visitor is at the network layer.

The bugs these cover are the ones that made the dossier's connection
panel wrong without ever looking wrong: a forwarded chain printed whole,
a geolocation call that described the server instead of the visitor, and
four different failures all collapsed into the word "Unavailable".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "utils"))

import client_context  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None, raises=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class FakeSession:
    """Stands in for `requests`, recording the URL it was handed."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.exc:
            raise self.exc
        return self.response


# --- client_ip -------------------------------------------------------

def test_forwarded_chain_yields_the_client_not_the_proxy():
    headers = {"X-Forwarded-For": "203.0.113.7, 10.0.0.4, 172.16.0.9"}
    assert client_context.client_ip(headers) == "203.0.113.7"


def test_header_lookup_is_case_insensitive():
    assert client_context.client_ip({"x-forwarded-for": "203.0.113.7"}) == "203.0.113.7"


@pytest.mark.parametrize("raw,expected", [
    ("203.0.113.7:41234", "203.0.113.7"),
    ("[2001:db8::1]:443", "2001:db8::1"),
    ("[2001:db8::1]", "2001:db8::1"),
    ("2001:db8::1", "2001:db8::1"),
    ("  203.0.113.7  ", "203.0.113.7"),
])
def test_address_shapes_that_turn_up_in_a_chain(raw, expected):
    assert client_context.client_ip({"X-Forwarded-For": raw}) == expected


def test_garbage_is_discarded_rather_than_displayed():
    # These headers are attacker-settable and land on screen.
    assert client_context.client_ip({"X-Forwarded-For": "<script>alert(1)</script>"}) is None
    assert client_context.client_ip({"X-Forwarded-For": "not-an-ip, 203.0.113.7"}) == "203.0.113.7"


def test_falls_through_to_the_other_proxy_headers():
    assert client_context.client_ip({"X-Real-Ip": "203.0.113.7"}) == "203.0.113.7"
    assert client_context.client_ip({"Cf-Connecting-Ip": "203.0.113.7"}) == "203.0.113.7"


def test_no_proxy_header_is_none_not_a_guess():
    assert client_context.client_ip({}) is None
    assert client_context.client_ip(None) is None


# --- lookup ----------------------------------------------------------

def test_a_real_answer_carries_the_fields_the_panel_renders():
    session = FakeSession(FakeResponse(payload={
        "ip": "8.8.8.8", "hostname": "host.example.net", "city": "New York City",
        "region": "New York", "country": "US", "org": "AS12271 Charter", "timezone": "America/New_York",
    }))
    info = client_context.lookup("8.8.8.8", session=session)
    assert info["status"] == "ok"
    assert info["hostname"] == "host.example.net"
    assert info["timezone"] == "America/New_York"
    assert client_context.location_label(info) == "New York City, New York, US"


def test_the_visitors_address_is_what_gets_looked_up():
    """Not the server's. `ipinfo.io/json` with no address geolocates
    whoever makes the call, which on a hosted build is the datacenter."""
    session = FakeSession(FakeResponse(payload={"ip": "8.8.8.8"}))
    client_context.lookup("8.8.8.8", session=session)
    assert session.urls == ["https://ipinfo.io/8.8.8.8/json"]


def test_private_addresses_never_leave_the_building():
    session = FakeSession(FakeResponse(payload={"ip": "192.168.1.1"}))
    info = client_context.lookup("192.168.1.1", session=session)
    assert info["status"] == "private"
    assert session.urls == []  # short-circuited, no outbound request


def test_bogon_response_is_private_not_an_error():
    session = FakeSession(FakeResponse(payload={"ip": "8.8.8.8", "bogon": True}))
    assert client_context.lookup("8.8.8.8", session=session)["status"] == "private"


def test_rate_limit_is_its_own_status():
    session = FakeSession(FakeResponse(status_code=429))
    assert client_context.lookup("8.8.8.8", session=session)["status"] == "rate_limited"


def test_server_error_is_unreachable():
    session = FakeSession(FakeResponse(status_code=503))
    assert client_context.lookup("8.8.8.8", session=session)["status"] == "unreachable"


def test_timeout_is_unreachable_and_does_not_raise():
    session = FakeSession(exc=OSError("timed out"))
    assert client_context.lookup("8.8.8.8", session=session)["status"] == "unreachable"


def test_a_200_that_is_not_json_is_malformed():
    session = FakeSession(FakeResponse(raises=True))
    assert client_context.lookup("8.8.8.8", session=session)["status"] == "malformed"


def test_documentation_ranges_are_treated_as_unplaceable():
    """203.0.113.0/24 (TEST-NET-3) and friends are not globally routable,
    so no registry can place them and no request should be spent trying."""
    session = FakeSession(FakeResponse(payload={"ip": "203.0.113.7"}))
    assert client_context.lookup("203.0.113.7", session=session)["status"] == "private"
    assert session.urls == []


@pytest.mark.parametrize("payload", [["not", "an", "object"], {}, {"error": "quota"}])
def test_a_200_without_an_address_is_malformed(payload):
    session = FakeSession(FakeResponse(payload=payload))
    assert client_context.lookup("8.8.8.8", session=session)["status"] == "malformed"


def test_every_failure_still_returns_the_full_shape():
    """The views index these keys unconditionally."""
    keys = {"status", "ip", "hostname", "city", "region", "country", "org", "timezone"}
    for session in (FakeSession(FakeResponse(status_code=429)),
                    FakeSession(exc=OSError("boom")),
                    FakeSession(FakeResponse(raises=True))):
        assert set(client_context.lookup("8.8.8.8", session=session)) == keys


# --- describe --------------------------------------------------------

def test_describe_geolocates_the_forwarded_address():
    seen = {}

    def fake_lookup(ip):
        seen["ip"] = ip
        return {**client_context._empty("ok", ip), "city": "New York City", "country": "US"}

    ctx = client_context.describe({"X-Forwarded-For": "8.8.8.8, 10.0.0.4"}, lookup_fn=fake_lookup)
    assert seen["ip"] == "8.8.8.8"
    assert ctx["client_ip"] == "8.8.8.8"
    assert ctx["location"] == "New York City, US"


def test_describe_skips_the_lookup_when_there_is_no_proxy_header():
    """A desktop install has no forwarded address, and asking ipinfo
    anyway would return the server's own -- which is the user, but only
    by accident, and the hosted build would answer the datacenter."""
    def explode(ip):  # pragma: no cover - must not be called
        raise AssertionError("looked up an address that was never forwarded")

    ctx = client_context.describe({}, lookup_fn=explode)
    assert ctx["status"] == "no_proxy_header"
    assert ctx["client_ip"] is None
    assert ctx["location"] == ""
