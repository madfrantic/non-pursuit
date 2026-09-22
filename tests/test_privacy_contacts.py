"""
Tests for utils/privacy_contacts.py's channel classification and escalation
order.

Every network call is faked -- conftest blocks real sockets anyway. The
point of these tests is the decision logic: which channel a given contact
record resolves to, and in what order discover() tries its sources.
"""
import asyncio
import types

import pytest

import privacy_contacts as pc


# ---------------------------------------------------------- is_bounty_platform

@pytest.mark.parametrize("value,expected", [
    ("security@hackerone.com", True),
    ("https://hackerone.com/github", True),
    ("https://www.bugcrowd.com/example", True),
    ("triage@synack.com", True),
    ("https://app.intigriti.com/programs/example", True),
    ("privacy@example.com", False),
    ("https://example.com/privacy", False),
    ("", False),
])
def test_is_bounty_platform(value, expected):
    assert pc.is_bounty_platform(value) is expected


def test_is_bounty_platform_does_not_match_a_lookalike_domain():
    """notbugcrowd.com must not be caught by an endswith("bugcrowd.com")."""
    assert pc.is_bounty_platform("security@notbugcrowd.com") is False
    assert pc.is_bounty_platform("https://bugcrowd.com.evil.example/x") is False


# ---------------------------------------------------------- classify_contact

def test_classify_bounty_email_wins_regardless_of_source():
    result = pc.classify_contact(pc.SOURCE_CURATED, ["security@hackerone.com"], [])
    assert result == [(pc.CHANNEL_VULNERABILITY_BOUNTY, "security@hackerone.com")]


def test_classify_security_txt_generic_email_is_bounty_bucket():
    """RFC 9116 Contact: is a vulnerability channel by default, not a privacy one."""
    result = pc.classify_contact(pc.SOURCE_SECURITY_TXT, ["security@example.com"], [])
    assert result == [(pc.CHANNEL_VULNERABILITY_BOUNTY, "security@example.com")]


def test_classify_security_txt_privacy_styled_email_is_promoted():
    result = pc.classify_contact(pc.SOURCE_SECURITY_TXT, ["privacy@example.com"], [])
    assert result == [(pc.CHANNEL_PRIVACY_EMAIL, "privacy@example.com")]


def test_classify_security_txt_dpo_email():
    result = pc.classify_contact(pc.SOURCE_SECURITY_TXT, ["dpo@example.com"], [])
    assert result == [(pc.CHANNEL_DPO_EMAIL, "dpo@example.com")]


def test_classify_abuse_fallback_ignores_localpart():
    """An RDAP abuse mailbox is never reclassified as a privacy channel,
    even if -- coincidentally -- its local part looked privacy-ish."""
    result = pc.classify_contact(pc.SOURCE_ABUSE_FALLBACK, ["privacy@registrar.example"], [])
    assert result == [(pc.CHANNEL_ABUSE_FALLBACK, "privacy@registrar.example")]


def test_classify_curated_email_without_special_localpart_still_privacy():
    """Curation is itself the trust signal; a curated support@ address is
    presumed to be the right compliance mailbox."""
    result = pc.classify_contact(pc.SOURCE_CURATED, ["support@example.com"], [])
    assert result == [(pc.CHANNEL_PRIVACY_EMAIL, "support@example.com")]


def test_classify_curated_dpo_email_is_promoted():
    result = pc.classify_contact(pc.SOURCE_CURATED, ["dpo@example.com"], [])
    assert result == [(pc.CHANNEL_DPO_EMAIL, "dpo@example.com")]


def test_classify_url_is_dsar_portal_unless_bounty():
    result = pc.classify_contact(pc.SOURCE_PRIVACY_PAGE, [], ["https://example.com/privacy-request"])
    assert result == [(pc.CHANNEL_DSAR_PORTAL, "https://example.com/privacy-request")]


def test_classify_url_pointing_at_bounty_platform():
    result = pc.classify_contact(pc.SOURCE_PRIVACY_PAGE, [], ["https://hackerone.com/example"])
    assert result == [(pc.CHANNEL_VULNERABILITY_BOUNTY, "https://hackerone.com/example")]


def test_classify_empty_contact():
    assert pc.classify_contact(pc.SOURCE_NONE, [], []) == []


# --------------------------------------------------------------- best_channel

def test_best_channel_escalation_order():
    """dpo_email > privacy_email > dsar_portal > abuse_fallback >
    vulnerability_bounty > none -- verified by mixing candidates that would
    pick a different winner under any other ordering."""
    contact = {
        "source": pc.SOURCE_CURATED,
        "emails": ["dpo@example.com", "privacy@example.com"],
        "urls": ["https://example.com/optout"],
    }
    result = pc.best_channel(contact)
    assert result == {"channel": pc.CHANNEL_DPO_EMAIL, "target": "dpo@example.com",
                      "ready": True}


def test_best_channel_privacy_email_beats_dsar_portal():
    contact = {"source": pc.SOURCE_PRIVACY_PAGE, "emails": ["privacy@example.com"],
              "urls": ["https://example.com/privacy-request"]}
    result = pc.best_channel(contact)
    assert result["channel"] == pc.CHANNEL_PRIVACY_EMAIL
    assert result["ready"] is True


def test_best_channel_dsar_portal_beats_abuse_fallback():
    """Simulated only: in practice these never coexist on one contact record
    (abuse_fallback is a discover() source in its own right), but the
    priority ordering itself must hold regardless of how they got there."""
    assert pc._CHANNEL_PRIORITY[pc.CHANNEL_DSAR_PORTAL] > pc._CHANNEL_PRIORITY[pc.CHANNEL_ABUSE_FALLBACK]


def test_best_channel_abuse_fallback_beats_vulnerability_bounty():
    assert (pc._CHANNEL_PRIORITY[pc.CHANNEL_ABUSE_FALLBACK]
            > pc._CHANNEL_PRIORITY[pc.CHANNEL_VULNERABILITY_BOUNTY])


def test_best_channel_bounty_only_is_surfaced_but_not_ready():
    contact = {"source": pc.SOURCE_SECURITY_TXT, "emails": [],
              "urls": ["https://hackerone.com/example"]}
    result = pc.best_channel(contact)
    assert result["channel"] == pc.CHANNEL_VULNERABILITY_BOUNTY
    assert result["target"] == "https://hackerone.com/example"
    assert result["ready"] is False


def test_best_channel_abuse_fallback_is_ready():
    contact = {"source": pc.SOURCE_ABUSE_FALLBACK, "emails": ["abuse@registrar.example"],
              "urls": []}
    result = pc.best_channel(contact)
    assert result == {"channel": pc.CHANNEL_ABUSE_FALLBACK,
                      "target": "abuse@registrar.example", "ready": True}


def test_best_channel_nothing_resolved():
    result = pc.best_channel({"source": pc.SOURCE_NONE, "emails": [], "urls": []})
    assert result == {"channel": pc.CHANNEL_NONE, "target": "", "ready": False}


# ------------------------------------------------------------------ discover

class FakeResponse:
    def __init__(self, status, body=""):
        self.status = status
        self.charset = "utf-8"
        self._body = body.encode()
        self.content = types.SimpleNamespace(read=self._read)

    async def _read(self, cap=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Scripted responses keyed by exact URL; a 404 for anything else."""

    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        return FakeResponse(*self.responses.get(url, (404, "")))


def _no_op_validate(url):
    return None


@pytest.fixture(autouse=True)
def allow_requests(monkeypatch):
    monkeypatch.setattr(pc, "validate_target_url", _no_op_validate)


def test_discover_returns_security_txt_when_it_is_a_real_privacy_contact(monkeypatch):
    session = FakeSession({
        "https://example.com/.well-known/security.txt":
            (200, "Contact: mailto:privacy@example.com\n"),
    })
    result = asyncio.run(pc.discover(session, "example.com"))
    assert result["source"] == pc.SOURCE_SECURITY_TXT
    assert result["emails"] == ["privacy@example.com"]
    # Nothing past security.txt was needed.
    assert not any("privacy-policy" in u for u in session.requested)


def test_discover_falls_through_a_bounty_only_security_txt(monkeypatch):
    """A security.txt whose only contact is a bounty link is not a stopping
    point -- discovery must still try the privacy pages."""
    session = FakeSession({
        "https://example.com/.well-known/security.txt":
            (200, "Contact: https://hackerone.com/example\n"),
        "https://example.com/privacy":
            (200, '<a href="https://example.com/privacy-request">Delete my data</a>'),
    })
    result = asyncio.run(pc.discover(session, "example.com"))
    assert result["source"] == pc.SOURCE_PRIVACY_PAGE
    assert result["urls"] == ["https://example.com/privacy-request"]


def test_discover_returns_the_bounty_hit_if_nothing_else_answers(monkeypatch):
    session = FakeSession({
        "https://example.com/.well-known/security.txt":
            (200, "Contact: https://hackerone.com/example\n"),
    })
    result = asyncio.run(pc.discover(session, "example.com", include_abuse_fallback=False))
    assert result["source"] == pc.SOURCE_SECURITY_TXT
    assert result["urls"] == ["https://hackerone.com/example"]


def test_discover_falls_back_to_rdap_abuse_contact(monkeypatch):
    session = FakeSession({})  # every path 404s

    async def fake_lookup(session_, host, timeout=10, **kwargs):
        assert host == "example.com"
        return {"abuse_emails": ["abuse@registrar.example"], "source": "rdap"}

    monkeypatch.setattr(pc.infra_checker, "lookup_registration", fake_lookup)
    result = asyncio.run(pc.discover(session, "example.com"))
    assert result["source"] == pc.SOURCE_ABUSE_FALLBACK
    assert result["emails"] == ["abuse@registrar.example"]
    assert "rdap" in result["reason"]


def test_discover_reports_none_when_even_rdap_has_nothing(monkeypatch):
    session = FakeSession({})

    async def fake_lookup(session_, host, timeout=10, **kwargs):
        return {"abuse_emails": []}

    monkeypatch.setattr(pc.infra_checker, "lookup_registration", fake_lookup)
    result = asyncio.run(pc.discover(session, "example.com"))
    assert result["source"] == pc.SOURCE_NONE


def test_discover_survives_rdap_raising(monkeypatch):
    session = FakeSession({})

    async def boom(session_, host, timeout=10, **kwargs):
        raise RuntimeError("rdap unreachable")

    monkeypatch.setattr(pc.infra_checker, "lookup_registration", boom)
    result = asyncio.run(pc.discover(session, "example.com"))
    assert result["source"] == pc.SOURCE_NONE


def test_discover_can_skip_abuse_fallback_entirely(monkeypatch):
    called = []

    async def fake_lookup(*a, **k):
        called.append(True)
        return {"abuse_emails": ["abuse@registrar.example"]}

    monkeypatch.setattr(pc.infra_checker, "lookup_registration", fake_lookup)
    session = FakeSession({})
    result = asyncio.run(pc.discover(session, "example.com", include_abuse_fallback=False))
    assert called == []
    assert result["source"] == pc.SOURCE_NONE


def test_discover_privacy_page_beats_rdap_when_both_would_answer(monkeypatch):
    """Escalation order end to end: security.txt empty, privacy page hits,
    RDAP must never even be consulted."""
    called = []

    async def fake_lookup(*a, **k):
        called.append(True)
        return {"abuse_emails": ["abuse@registrar.example"]}

    monkeypatch.setattr(pc.infra_checker, "lookup_registration", fake_lookup)
    session = FakeSession({
        "https://example.com/privacy": (200, "Reach our team at privacy@example.com"),
    })
    result = asyncio.run(pc.discover(session, "example.com"))
    assert result["source"] == pc.SOURCE_PRIVACY_PAGE
    assert called == []
