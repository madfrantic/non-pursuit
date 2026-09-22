"""
Tests for utils/infra_checker.py.

Every network path is either monkeypatched or fed a canned payload --
conftest blocks outbound sockets, and an infrastructure check whose tests
need the internet would be untestable on the day it matters. The
certificate tests build a real X.509 cert in memory rather than mocking the
parser, so parse_certificate is exercised against the actual format.
"""
import asyncio
import datetime as dt
import types

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import infra_checker as ic


# ---------------------------------------------------------------- helpers

def make_cert(*, cn="example.com", org="Example Inc.", sans=("example.com",),
              issuer_cn=None, issuer_org="Test CA", days_valid=90,
              starts_in_days=-1):
    """A real self-signed certificate, returned as DER."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
    ])
    issuer = subject if issuer_cn is None else x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, issuer_org),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(0x1234abcd)
        .not_valid_before(now + dt.timedelta(days=starts_in_days))
        .not_valid_after(now + dt.timedelta(days=days_valid))
    )
    if sans:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in sans]),
            critical=False)
    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.DER)


class FakeRdata:
    def __init__(self, text):
        self._text = text

    def to_text(self):
        return self._text


def fake_resolver(answers, failures=(), absent=()):
    """A stand-in for dns.resolver.Resolver with scripted answers."""
    class _Resolver:
        timeout = lifetime = 0

        def resolve(self, host, rtype):
            if rtype in failures:
                raise ic._dns_exception.Timeout()
            if rtype in absent or rtype not in answers:
                raise ic._dns_resolver.NoAnswer()
            return [FakeRdata(v) for v in answers[rtype]]

    return _Resolver


def report(host, *, ns=(), mx=(), a=(), fingerprint="", cert_domains=(),
           registrar="", operator=""):
    """A check_host-shaped report, assembled without touching the network."""
    records = {}
    if ns:
        records["NS"] = list(ns)
    if mx:
        records["MX"] = list(mx)
    if a:
        records["A"] = list(a)
    built = {
        "host": host,
        "domain": ic.registrable_domain(host),
        "dns": {"records": records, "resolves": bool(a), "accepts_mail": bool(mx)},
        "tls": {"fingerprint_sha256": fingerprint,
                "sans": list(cert_domains), "subject_org": operator},
        "registration": {"registrar": registrar, "abuse_emails": []},
        "operator": operator,
        "escalation_emails": [],
    }
    built["fingerprint"] = ic.operator_fingerprint(built)
    return built


# ------------------------------------------------------- registrable_domain

@pytest.mark.parametrize("host,expected", [
    ("example.com", "example.com"),
    ("www.example.com", "example.com"),
    ("a.b.c.example.com", "example.com"),
    ("shop.example.co.uk", "example.co.uk"),
    ("example.co.uk", "example.co.uk"),
    ("Example.COM.", "example.com"),
    ("localhost", "localhost"),
    ("", ""),
])
def test_registrable_domain(host, expected):
    assert ic.registrable_domain(host) == expected


def test_registrable_domain_leaves_ip_literals_alone():
    assert ic.registrable_domain("93.184.216.34") == "93.184.216.34"


@pytest.mark.parametrize("value,expected", [
    ("REDACTED FOR PRIVACY", True),
    ("Domains By Proxy, LLC", True),
    ("Contact Privacy Inc.", True),
    ("", False),
    ("Spokeo, Inc.", False),
])
def test_is_privacy_proxy(value, expected):
    assert ic.is_privacy_proxy(value) is expected


# ------------------------------------------------------------------- DNS

def test_resolve_dns_collects_records(monkeypatch):
    monkeypatch.setattr(ic._dns_resolver, "Resolver", fake_resolver({
        "A": ["93.184.216.34"],
        "MX": ["10 mail.example.com."],
        "NS": ["ns2.example.com.", "ns1.example.com."],
        "TXT": ['"v=spf1 -all"'],
    }))
    result = ic.resolve_dns("example.com")

    assert result["status"] == ic.OK
    assert result["resolves"] is True
    assert result["accepts_mail"] is True
    assert result["partial"] is False
    assert result["records"]["NS"] == ["ns1.example.com", "ns2.example.com"]
    assert result["records"]["TXT"] == ["v=spf1 -all"]
    assert result["records"]["MX"] == ["10 mail.example.com."]


def test_resolve_dns_absent_is_not_unknown(monkeypatch):
    """Nothing there and could-not-look must not collapse into one status."""
    monkeypatch.setattr(ic._dns_resolver, "Resolver", fake_resolver({}))
    absent = ic.resolve_dns("nonexistent.example")
    assert absent["status"] == ic.ABSENT
    assert absent["partial"] is False

    monkeypatch.setattr(ic._dns_resolver, "Resolver",
                        fake_resolver({}, failures=ic.RECORD_TYPES))
    unknown = ic.resolve_dns("example.com")
    assert unknown["status"] == ic.UNKNOWN
    assert unknown["partial"] is True


def test_resolve_dns_partial_when_one_type_fails(monkeypatch):
    monkeypatch.setattr(ic._dns_resolver, "Resolver",
                        fake_resolver({"A": ["1.2.3.4"]}, failures=("MX",)))
    result = ic.resolve_dns("example.com")
    assert result["status"] == ic.OK
    assert result["partial"] is True
    assert "MX" in result["reason"]
    # An MX lookup that failed must not be reported as "accepts no mail".
    assert result["accepts_mail"] is False


def test_resolve_dns_rejects_empty_host():
    assert ic.resolve_dns("")["status"] == ic.UNKNOWN


def test_stdlib_fallback_marks_itself_partial(monkeypatch):
    monkeypatch.setattr(ic, "_dns_resolver", None)
    monkeypatch.setattr(ic.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    result = ic.resolve_dns("example.com")
    assert result["records"] == {"A": ["93.184.216.34"]}
    assert result["partial"] is True
    assert "dnspython" in result["reason"]


def test_stdlib_fallback_nxdomain(monkeypatch):
    monkeypatch.setattr(ic, "_dns_resolver", None)

    def boom(*a, **k):
        raise ic.socket.gaierror("no such host")

    monkeypatch.setattr(ic.socket, "getaddrinfo", boom)
    result = ic.resolve_dns("nope.example")
    assert result["status"] == ic.ABSENT
    assert result["resolves"] is False


# ------------------------------------------------------------ certificates

def test_parse_certificate_extracts_operator_fields():
    parsed = ic.parse_certificate(make_cert(
        cn="www.example.com", org="Example Inc.",
        sans=("www.example.com", "*.cdn.example.net"),
        issuer_cn="Test Issuing CA", days_valid=45))

    assert parsed["subject_cn"] == "www.example.com"
    assert parsed["subject_org"] == "Example Inc."
    assert parsed["issuer_org"] == "Test CA"
    assert parsed["sans"] == ["*.cdn.example.net", "www.example.com"]
    assert parsed["self_signed"] is False
    assert parsed["expired"] is False
    assert parsed["not_yet_valid"] is False
    assert 43 <= parsed["days_remaining"] <= 45
    assert len(parsed["fingerprint_sha256"]) == 64


def test_parse_certificate_flags_expired_and_self_signed():
    parsed = ic.parse_certificate(make_cert(days_valid=-1, starts_in_days=-30))
    assert parsed["expired"] is True
    assert parsed["self_signed"] is True
    assert parsed["days_remaining"] < 0


def test_parse_certificate_without_san_extension():
    parsed = ic.parse_certificate(make_cert(sans=()))
    assert parsed["sans"] == []


def test_fetch_certificate_refuses_private_targets():
    """The SSRF guard applies to the TLS path, not just to HTTP requests."""
    result = ic.fetch_certificate("localhost")
    assert result["status"] == ic.UNKNOWN
    assert "private" in result["reason"] or "unsafe" in result["reason"]


def test_fetch_certificate_records_verification_failure(monkeypatch):
    der = make_cert(cn="wrong.example.com")
    calls = []

    def fake_handshake(host, port, timeout, *, verify):
        calls.append(verify)
        if verify:
            raise ic.ssl.SSLCertVerificationError("hostname mismatch")
        return der

    monkeypatch.setattr(ic, "validate_target_url", lambda url: None)
    monkeypatch.setattr(ic, "_handshake", fake_handshake)

    result = ic.fetch_certificate("example.com")
    assert calls == [True, False]           # verified attempt first, then a read
    assert result["status"] == ic.OK
    assert result["verified"] is False
    assert result["verify_error"]
    assert result["subject_cn"] == "wrong.example.com"


def test_fetch_certificate_handshake_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(ic, "validate_target_url", lambda url: None)

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(ic, "_handshake", boom)
    result = ic.fetch_certificate("example.com")
    assert result["status"] == ic.UNKNOWN
    assert "handshake failed" in result["reason"]


# -------------------------------------------------------------- RDAP/WHOIS

RDAP_PAYLOAD = {
    "ldhName": "EXAMPLE.COM",
    "status": ["client transfer prohibited"],
    "events": [
        {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
        {"eventAction": "last changed", "eventDate": "2026-08-01T00:00:00Z"},
    ],
    "nameservers": [{"ldhName": "NS1.EXAMPLE.COM."}, {"ldhName": "ns2.example.com"}],
    "entities": [{
        "roles": ["registrar"],
        "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                 ["fn", {}, "text", "MarkMonitor Inc."]]],
        "entities": [{
            "roles": ["abuse"],
            "vcardArray": ["vcard", [["email", {}, "text", "Abuse@MarkMonitor.com"]]],
        }],
    }, {
        "roles": ["registrant"],
        "vcardArray": ["vcard", [["fn", {}, "text", "REDACTED FOR PRIVACY"]]],
    }],
}


def test_parse_rdap():
    parsed = ic.parse_rdap(RDAP_PAYLOAD)
    assert parsed["domain"] == "example.com"
    assert parsed["registrar"] == "MarkMonitor Inc."
    assert parsed["abuse_emails"] == ["abuse@markmonitor.com"]
    assert parsed["registrant_redacted"] is True
    assert parsed["created"].startswith("1995")
    assert parsed["expires"].startswith("2027")
    assert parsed["updated"].startswith("2026")
    assert parsed["nameservers"] == ["ns1.example.com", "ns2.example.com"]
    assert parsed["source"] == "rdap"


def test_parse_rdap_tolerates_malformed_vcards():
    parsed = ic.parse_rdap({
        "ldhName": "broken.example",
        "entities": [{"roles": ["registrar"], "vcardArray": ["vcard"]},
                     {"roles": ["abuse"], "vcardArray": ["vcard", [["email"]]]},
                     "not-a-dict"],
    })
    assert parsed["registrar"] == ""
    assert parsed["abuse_emails"] == []


def test_parse_rdap_takes_abuse_address_off_the_registrar_entity():
    parsed = ic.parse_rdap({
        "ldhName": "example.net",
        "entities": [{
            "roles": ["registrar"],
            "vcardArray": ["vcard", [["fn", {}, "text", "Registrar LLC"],
                                     ["email", {}, "text", "sales@registrar.example"],
                                     ["email", {}, "text", "abuse@registrar.example"]]],
        }],
    })
    # Only the abuse mailbox -- a sales address is not an escalation channel.
    assert parsed["abuse_emails"] == ["abuse@registrar.example"]


def test_from_whois_matches_the_rdap_shape():
    record = types.SimpleNamespace(
        domain_name=["EXAMPLE.COM", "example.com"],
        registrar="MarkMonitor Inc.",
        org="Example Inc.",
        emails=["abuse@markmonitor.com", "noc@example.com"],
        creation_date=dt.datetime(1995, 8, 14),
        updated_date=None,
        expiration_date=dt.datetime(2027, 8, 13),
        status="clientTransferProhibited",
        name_servers=["NS1.EXAMPLE.COM", "ns1.example.com."],
    )
    parsed = ic._from_whois(record)
    assert parsed["domain"] == "example.com"
    assert parsed["registrant"] == "Example Inc."
    assert parsed["registrant_redacted"] is False
    assert parsed["abuse_emails"] == ["abuse@markmonitor.com"]
    assert parsed["nameservers"] == ["ns1.example.com"]
    assert parsed["statuses"] == ["clientTransferProhibited"]
    assert parsed["source"] == "whois"
    assert set(parsed) == set(ic.parse_rdap(RDAP_PAYLOAD))


# ------------------------------------------------------- lookup_registration

class FakeResponse:
    def __init__(self, status, body=b""):
        self.status = status
        self.content = types.SimpleNamespace(
            read=lambda cap=None: _immediate(body))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def _immediate(value):
    return value


class FakeSession:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        if self._error:
            raise self._error
        return self._response


def test_lookup_registration_parses_rdap():
    import json
    session = FakeSession(FakeResponse(200, json.dumps(RDAP_PAYLOAD).encode()))
    result = asyncio.run(ic.lookup_registration(session, "www.example.com"))
    assert result["status"] == ic.OK
    assert result["registrar"] == "MarkMonitor Inc."
    assert session.requests == [ic.RDAP_BOOTSTRAP + "example.com"]


def test_lookup_registration_404_is_absent():
    session = FakeSession(FakeResponse(404))
    result = asyncio.run(ic.lookup_registration(session, "nope.example"))
    assert result["status"] == ic.ABSENT
    assert "not registered" in result["reason"]


def test_lookup_registration_falls_back_to_whois(monkeypatch):
    record = types.SimpleNamespace(
        domain_name="example.com", registrar="Fallback Registrar", org="",
        emails=[], creation_date=None, updated_date=None, expiration_date=None,
        status=None, name_servers=[])
    monkeypatch.setattr(ic, "_whois", types.SimpleNamespace(whois=lambda d: record))
    session = FakeSession(error=RuntimeError("rdap down"))

    result = asyncio.run(ic.lookup_registration(session, "example.com"))
    assert result["status"] == ic.OK
    assert result["source"] == "whois"
    assert result["registrar"] == "Fallback Registrar"


def test_lookup_registration_unknown_when_everything_fails(monkeypatch):
    monkeypatch.setattr(ic, "_whois", None)
    session = FakeSession(error=RuntimeError("rdap down"))
    result = asyncio.run(ic.lookup_registration(session, "example.com"))
    assert result["status"] == ic.UNKNOWN
    assert result["abuse_emails"] == []


def test_lookup_registration_skips_ip_literals():
    session = FakeSession()
    result = asyncio.run(ic.lookup_registration(session, "93.184.216.34"))
    assert result["status"] == ic.UNKNOWN
    assert session.requests == []


# ---------------------------------------------------------------- check_host

def test_check_host_gathers_all_three(monkeypatch):
    monkeypatch.setattr(ic, "resolve_dns", lambda host, **k: {
        "status": ic.OK, "records": {"NS": ["ns1.host.example"]},
        "resolves": True, "accepts_mail": True})
    monkeypatch.setattr(ic, "fetch_certificate", lambda host, **k: {
        "status": ic.OK, "subject_org": "Example Inc.",
        "fingerprint_sha256": "ab" * 32, "sans": ["example.com"]})

    async def fake_registration(session, host, **k):
        return {"status": ic.OK, "registrar": "MarkMonitor Inc.",
                "abuse_emails": ["abuse@markmonitor.com"], "registrant": ""}

    monkeypatch.setattr(ic, "lookup_registration", fake_registration)

    result = asyncio.run(ic.check_host(FakeSession(), "www.example.com"))
    assert result["host"] == "www.example.com"
    assert result["domain"] == "example.com"
    assert result["live"] is True
    assert result["mail_routable"] is True
    assert result["operator"] == "Example Inc."
    assert result["escalation_emails"] == ["abuse@markmonitor.com"]
    assert result["fingerprint"]["nameservers"] == ["ns1.host.example"]


def test_check_host_survives_one_check_raising(monkeypatch):
    def boom(host, **k):
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(ic, "resolve_dns", boom)
    monkeypatch.setattr(ic, "fetch_certificate",
                        lambda host, **k: {"status": ic.OK, "subject_org": "Example Inc."})

    async def fake_registration(session, host, **k):
        return {"status": ic.OK, "abuse_emails": []}

    monkeypatch.setattr(ic, "lookup_registration", fake_registration)

    result = asyncio.run(ic.check_host(FakeSession(), "example.com"))
    assert result["dns"]["status"] == ic.UNKNOWN
    assert result["live"] is False
    assert result["operator"] == "Example Inc."


def test_check_host_can_skip_optional_checks(monkeypatch):
    monkeypatch.setattr(ic, "resolve_dns", lambda host, **k: {
        "status": ic.OK, "records": {}, "resolves": True, "accepts_mail": False})

    result = asyncio.run(ic.check_host(FakeSession(), "example.com",
                                       with_tls=False, with_registration=False))
    assert "tls" not in result
    assert "registration" not in result
    assert result["operator"] == ""


def test_check_hosts_deduplicates(monkeypatch):
    seen = []

    async def fake_check(session, host, **k):
        seen.append(host)
        return report(host)

    monkeypatch.setattr(ic, "check_host", fake_check)
    results = asyncio.run(ic.check_hosts(
        ["example.com", "EXAMPLE.COM", "example.com.", "", None, "other.example"],
        session=FakeSession()))
    assert sorted(seen) == ["example.com", "other.example"]
    assert len(results) == 2


# ------------------------------------------------------- operator grouping

def test_shared_operator_needs_more_than_a_registrar():
    a = report("a.example", registrar="GoDaddy", a=["1.2.3.4"])
    b = report("b.example", registrar="GoDaddy", a=["1.2.3.4"])
    strong, reasons = ic.shared_operator(a, b)
    assert strong is False
    assert reasons                      # the weak links are still reported


def test_shared_operator_on_a_shared_certificate():
    a = report("a.example", fingerprint="cd" * 32)
    b = report("b.example", fingerprint="cd" * 32)
    strong, reasons = ic.shared_operator(a, b)
    assert strong is True
    assert any("certificate" in r for r in reasons)


def test_shared_operator_on_shared_nameservers():
    a = report("a.example", ns=["ns1.host.example", "ns2.host.example"])
    b = report("b.example", ns=["ns2.host.example"])
    strong, reasons = ic.shared_operator(a, b)
    assert strong is True
    assert any("nameserver" in r for r in reasons)


def test_shared_operator_ignores_missing_data():
    strong, reasons = ic.shared_operator(report("a.example"), report("b.example"))
    assert strong is False
    assert reasons == []


def test_operator_fingerprint_folds_wildcard_sans():
    built = report("a.example", cert_domains=["*.example.com", "www.example.com"])
    assert built["fingerprint"]["cert_domains"] == ["example.com"]


def test_group_by_operator_clusters_related_hosts():
    groups = ic.group_by_operator([
        report("brand1.example", ns=["ns1.host.example"], operator="Data Corp"),
        report("brand2.example", ns=["ns1.host.example"]),
        report("unrelated.example", ns=["ns9.other.example"]),
    ])
    assert len(groups) == 2
    clustered = next(g for g in groups if len(g["members"]) == 2)
    assert clustered["hosts"] == ["brand1.example", "brand2.example"]
    assert clustered["operator"] == "Data Corp"
    assert clustered["evidence"]


def test_summarize():
    reports = [
        {"host": "live.example", "live": True, "mail_routable": True,
         "escalation_emails": ["abuse@x.example"], "tls": {"days_remaining": 12},
         "fingerprint": {}},
        {"host": "dead.example", "live": False, "mail_routable": False,
         "escalation_emails": [], "tls": {"days_remaining": 300},
         "fingerprint": {}},
    ]
    summary = ic.summarize(reports)
    assert summary["hosts"] == 2
    assert summary["live"] == 1
    assert summary["dead"] == 1
    assert summary["mail_routable"] == 1
    assert summary["with_escalation_contact"] == 1
    assert summary["tls_expiring_soon"] == ["live.example"]


def test_summarize_empty():
    assert ic.summarize([])["hosts"] == 0


# ------------------------------------------------------------- null MX

@pytest.mark.parametrize("mx,expected", [
    (["10 mail.example.com."], True),
    (["0 ."], False),                      # RFC 7505 null MX
    ([], False),
    (None, False),
    (["0 ."], False),
    (["0 .", "10 mail.example.com."], True),
])
def test_accepts_mail(mx, expected):
    assert ic.accepts_mail(mx) is expected


def test_resolve_dns_honours_null_mx(monkeypatch):
    """A domain that publishes "we take no mail" is not mail-routable."""
    monkeypatch.setattr(ic._dns_resolver, "Resolver", fake_resolver({
        "A": ["93.184.216.34"], "MX": ["0 ."]}))
    result = ic.resolve_dns("example.com")
    assert result["records"]["MX"] == ["0 ."]
    assert result["accepts_mail"] is False
