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


# --- device -----------------------------------------------------------
#
# The panel reported "macOS 15.1 (Sequoia)" to a presenter running Linux.
# Two causes: a hardcoded mock (fixed in master.py) and a parser whose
# only macOS test was `"Mac" in ua`.

UA_UBUNTU_FIREFOX = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
UA_LINUX_CHROME = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0.0.0 Safari/537.36")
UA_MAC_SAFARI = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.6 Safari/605.1.15")
UA_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Mobile Safari/537.36")
UA_WINDOWS_EDGE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0")


@pytest.mark.parametrize("ua,expected", [
    (UA_UBUNTU_FIREFOX, "Ubuntu"),
    (UA_LINUX_CHROME, "Linux"),
    (UA_MAC_SAFARI, "macOS"),
    (UA_ANDROID, "Android"),          # "Linux; Android" -- Android must win
    (UA_WINDOWS_EDGE, "Windows"),
    ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0)", "Chrome OS"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X)", "iOS"),
    ("", "Unknown OS"),
])
def test_os_is_read_from_the_user_agent(ua, expected):
    assert client_context.os_family({}, ua) == expected


def test_a_linux_desktop_is_not_reported_as_macos():
    """The old test was `"Mac" in ua`, and every WebKit-derived UA on
    Linux carries "AppleWebKit"."""
    assert client_context.os_family({}, UA_LINUX_CHROME) != "macOS"
    assert client_context.device_profile({"User-Agent": UA_LINUX_CHROME})[1] == "Linux"


@pytest.mark.parametrize("hint,expected", [
    ('"Linux"', "Linux"),
    ('"macOS"', "macOS"),
    ('"Windows"', "Windows"),
    ('"Android"', "Android"),
    ('"Chrome OS"', "Chrome OS"),
])
def test_the_client_hint_wins_over_the_user_agent(hint, expected):
    """Sec-CH-UA-Platform is declared, not parsed out of a marketing
    string, so it is the more trustworthy of the two."""
    headers = {"Sec-CH-UA-Platform": hint, "User-Agent": UA_MAC_SAFARI}
    assert client_context.os_family(headers, UA_MAC_SAFARI) == expected


def test_the_hint_is_case_insensitive_and_tolerates_missing_quotes():
    assert client_context.os_family({"sec-ch-ua-platform": "Linux"}, "") == "Linux"
    assert client_context.os_family({"Sec-Ch-Ua-Platform": '"LINUX"'}, "") == "Linux"


def test_ubuntu_survives_a_linux_hint():
    """The hint reports the kernel; only the UA names the distribution."""
    headers = {"Sec-CH-UA-Platform": '"Linux"', "User-Agent": UA_UBUNTU_FIREFOX}
    assert client_context.os_family(headers, UA_UBUNTU_FIREFOX) == "Ubuntu"


def test_an_unrecognised_hint_falls_back_to_the_user_agent():
    headers = {"Sec-CH-UA-Platform": '"Haiku"', "User-Agent": UA_LINUX_CHROME}
    assert client_context.os_family(headers, UA_LINUX_CHROME) == "Linux"


@pytest.mark.parametrize("ua,expected", [
    (UA_UBUNTU_FIREFOX, "Gecko"),
    (UA_LINUX_CHROME, "Blink / V8"),
    (UA_WINDOWS_EDGE, "Blink / V8"),   # says Chrome AND Safari AND Edg
    (UA_MAC_SAFARI, "WebKit"),
    ("", "Unknown Engine"),
])
def test_engine_detection_survives_overlapping_tokens(ua, expected):
    assert client_context.browser_engine(ua) == expected


def test_device_profile_without_a_user_agent_says_so():
    assert client_context.device_profile({}) == ("Not disclosed", "Unknown OS", "Unknown Engine")
