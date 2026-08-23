"""
Coverage for passive email reconnaissance.

All operations are read-only: gravatar/libravatar profile checks, PGP key
server queries, and DNS validation. Tests use mocked requests to avoid
hitting real services.
"""
import pytest
from unittest.mock import patch, MagicMock

from email_scanner import (
    CONFIRMED,
    ERROR,
    NOT_FOUND,
    POSSIBLE,
    _gravatar_hash,
    _libravatar_hash,
    check_gravatar,
    check_libravatar,
    check_pgp_keys,
    check_dns_records,
    scan_email,
    summarize_email_scan,
)


class TestHashFunctions:
    """Gravatar and Libravatar use different hash algorithms."""

    def test_gravatar_hash_is_md5(self):
        """Gravatar uses MD5 of lowercased, trimmed email."""
        h = _gravatar_hash("Test@Example.COM  ")
        assert len(h) == 32  # MD5 hex length
        assert h == "55502f40dc8b7c769880b10874abc9d0"

    def test_libravatar_hash_is_sha256(self):
        """Libravatar uses SHA256 of lowercased, trimmed email."""
        h = _libravatar_hash("Test@Example.COM  ")
        assert len(h) == 64  # SHA256 hex length


class TestGravatarCheck:
    """Passive Gravatar API check via public CDN."""

    def test_gravatar_profile_found(self):
        """HTTP 200 with valid JSON means profile exists."""
        with patch("email_scanner.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"entry": [{"id": "12345"}]}
            mock_get.return_value = mock_response

            verdict, reason = check_gravatar("alice@example.com")
            assert verdict == CONFIRMED
            assert "entry_count" in reason

    def test_gravatar_not_found(self):
        """HTTP 404 means no public profile."""
        with patch("email_scanner.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            verdict, reason = check_gravatar("alice@example.com")
            assert verdict == NOT_FOUND

    def test_gravatar_invalid_email(self):
        """Non-email input returns NOT_FOUND."""
        verdict, _ = check_gravatar("not-an-email")
        assert verdict == NOT_FOUND

    def test_gravatar_request_error(self):
        """Network errors return ERROR."""
        import requests
        with patch("email_scanner.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Network error")
            verdict, reason = check_gravatar("alice@example.com")
            assert verdict == ERROR
            assert "failed" in reason.lower()


class TestLibravatarCheck:
    """Passive Libravatar API check (decentralized alternative)."""

    def test_libravatar_profile_found(self):
        """HTTP 200 means profile exists."""
        with patch("email_scanner.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            verdict, reason = check_libravatar("alice@example.com")
            assert verdict == CONFIRMED

    def test_libravatar_not_found(self):
        """HTTP 404 means no public profile."""
        with patch("email_scanner.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            verdict, _ = check_libravatar("alice@example.com")
            assert verdict == NOT_FOUND


class TestPGPKeyCheck:
    """Passive PGP key server lookup (keys.openpgp.org)."""

    def test_pgp_keys_found(self):
        """HTTP 200 with keys means encryption keys are registered."""
        with patch("email_scanner.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "keys": [{"keyid": "ABC123"}, {"keyid": "DEF456"}]
            }
            mock_get.return_value = mock_response

            verdict, reason = check_pgp_keys("alice@example.com")
            assert verdict == CONFIRMED
            assert "2 key" in reason

    def test_pgp_no_keys(self):
        """HTTP 200 but empty keys list means no PGP keys."""
        with patch("email_scanner.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"keys": []}
            mock_get.return_value = mock_response

            verdict, _ = check_pgp_keys("alice@example.com")
            assert verdict == NOT_FOUND

    def test_pgp_not_found(self):
        """HTTP 404 means no keys registered."""
        with patch("email_scanner.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            verdict, _ = check_pgp_keys("alice@example.com")
            assert verdict == NOT_FOUND


class TestDNSCheck:
    """Passive DNS validation for domain resolution."""

    def test_public_email_provider_skipped(self):
        """Well-known providers (gmail, outlook, etc.) are marked NOT_FOUND."""
        for domain in ["alice@gmail.com", "bob@outlook.com", "charlie@yahoo.com"]:
            verdict, reason = check_dns_records(domain)
            assert verdict == NOT_FOUND
            assert "public email provider" in reason

    def test_custom_domain_resolves(self):
        """Custom domains that resolve are CONFIRMED."""
        with patch("email_scanner.socket.gethostbyname") as mock_host:
            mock_host.return_value = "1.2.3.4"
            verdict, reason = check_dns_records("alice@example.com")
            assert verdict == CONFIRMED
            assert "resolves" in reason.lower()

    def test_domain_does_not_resolve(self):
        """Non-existent domains return NOT_FOUND."""
        with patch("email_scanner.socket.gethostbyname") as mock_host:
            import socket as sock
            mock_host.side_effect = sock.gaierror("Name resolution failed")
            verdict, reason = check_dns_records("alice@invalid-domain-xyz.com")
            assert verdict == NOT_FOUND
            assert "does not resolve" in reason

    def test_invalid_email(self):
        """Non-email input returns NOT_FOUND."""
        verdict, _ = check_dns_records("not-an-email")
        assert verdict == NOT_FOUND


class TestScanEmail:
    """Full email scan across all passive vectors."""

    def test_scan_runs_all_vectors(self):
        """scan_email() calls multi-platform probers and DNS validation."""
        results = scan_email("alice@example.com")
        assert len(results) >= 12
        assert all(r["identifier"] == "alice@example.com" for r in results)
        services = {r["service"] for r in results}
        assert {"Gravatar", "Libravatar", "PGP Keys", "Spotify", "Duolingo", "GitHub", "Domain DNS"}.issubset(services)

    def test_scan_empty_email(self):
        """Empty or invalid email returns empty results."""
        assert scan_email("") == []
        assert scan_email("not-an-email") == []

    def test_result_structure(self):
        """Each result has required fields."""
        with patch("email_scanner.check_gravatar") as mock_grav:
            mock_grav.return_value = (CONFIRMED, "found")
            results = scan_email("alice@example.com")

            for result in results:
                assert "service" in result
                assert "identifier" in result
                assert "confidence" in result
                assert "reason" in result
                assert "vector" in result


class TestSummarize:
    """Aggregation of scan results."""

    def test_summarize_counts(self):
        """Summarize tallies results by confidence level."""
        results = [
            {"confidence": CONFIRMED},
            {"confidence": CONFIRMED},
            {"confidence": POSSIBLE},
            {"confidence": NOT_FOUND},
            {"confidence": ERROR},
        ]
        summary = summarize_email_scan(results)
        assert summary[CONFIRMED] == 2
        assert summary[POSSIBLE] == 1
        assert summary[NOT_FOUND] == 1
        assert summary[ERROR] == 1

    def test_summarize_empty(self):
        """Empty scan summarizes to all zeros."""
        summary = summarize_email_scan([])
        assert all(count == 0 for count in summary.values())


class TestEmailDiscoveries:
    """Passive email findings routed into the discovered_accounts table."""

    def test_only_found_associations_are_persisted(self):
        """NOT_FOUND is the absence of an association -- persisting it
        would inflate every downstream count, audit summary included."""
        from email_scanner import email_discoveries
        rows = email_discoveries([
            {"service": "Gravatar", "identifier": "a@b.com", "confidence": CONFIRMED, "reason": "found", "vector": "avatar_service"},
            {"service": "Libravatar", "identifier": "a@b.com", "confidence": NOT_FOUND, "reason": "404", "vector": "avatar_service"},
            {"service": "PGP Keys", "identifier": "a@b.com", "confidence": POSSIBLE, "reason": "maybe", "vector": "encryption_key"},
            {"service": "Domain DNS", "identifier": "a@b.com", "confidence": ERROR, "reason": "boom", "vector": "domain_validation"},
        ])
        assert {r["platform"] for r in rows} == {"Gravatar", "PGP Keys"}

    def test_rows_match_the_discovered_accounts_schema(self):
        from email_scanner import email_discoveries, EMAIL_CATEGORY
        rows = email_discoveries([
            {"service": "Gravatar", "identifier": "a@b.com", "confidence": CONFIRMED, "reason": "found", "vector": "avatar_service"},
        ])
        row = rows[0]
        assert row["target_identifier"] == "a@b.com"
        assert row["category"] == EMAIL_CATEGORY
        assert row["confidence"] == CONFIRMED
        assert row["profile_url"].startswith("https://gravatar.com/")

    def test_services_without_a_public_profile_get_no_link(self):
        """Libravatar exposes only an avatar endpoint and a resolving MX
        record is not a profile -- neither should hand over a URL that
        proves nothing."""
        from email_scanner import email_discoveries
        rows = email_discoveries([
            {"service": "Libravatar", "identifier": "a@b.com", "confidence": CONFIRMED, "reason": "found", "vector": "avatar_service"},
            {"service": "Domain DNS", "identifier": "a@b.com", "confidence": CONFIRMED, "reason": "resolves", "vector": "domain_validation"},
        ])
        assert all(row["profile_url"] == "" for row in rows)

    def test_empty_scan_persists_nothing(self):
        from email_scanner import email_discoveries
        assert email_discoveries([]) == []


class TestAvatarExtraction:
    """Avatar URL extraction for visual confirmation."""

    def test_gravatar_avatar_url_format(self):
        from email_scanner import _extract_avatar_url
        url = _extract_avatar_url("Gravatar", "alice@example.com", None)
        assert url.startswith("https://www.gravatar.com/avatar/")
        assert "?s=128" in url

    def test_libravatar_avatar_url_format(self):
        from email_scanner import _extract_avatar_url
        url = _extract_avatar_url("Libravatar", "alice@example.com", None)
        assert url.startswith("https://www.libravatar.org/avatar/")
        assert "?s=128" in url

    def test_services_without_avatars_return_empty_string(self):
        from email_scanner import _extract_avatar_url
        assert _extract_avatar_url("PGP Keys", "alice@example.com", None) == ""
        assert _extract_avatar_url("Domain DNS", "alice@example.com", None) == ""

    def test_email_discoveries_include_avatars(self):
        from email_scanner import email_discoveries, CONFIRMED
        rows = email_discoveries([
            {"service": "Gravatar", "identifier": "alice@example.com",
             "confidence": CONFIRMED, "reason": "found", "vector": "avatar_service"},
        ])
        assert rows[0]["avatar_url"].startswith("https://www.gravatar.com/avatar/")


class TestExposureFindings:
    """Exposure findings returns all probed services and adult sites without restrictive filtering."""

    def test_exposure_findings_includes_adult_and_general_services(self):
        from email_scanner import exposure_findings, CONFIRMED, POSSIBLE, NOT_FOUND
        results = [
            {"service": "Pornhub", "confidence": CONFIRMED, "reason": "account found"},
            {"service": "OnlyFans", "confidence": CONFIRMED, "reason": "registered"},
            {"service": "XVideos", "confidence": POSSIBLE, "reason": "ambiguous"},
            {"service": "Spotify", "confidence": CONFIRMED, "reason": "registered"},
            {"service": "Gravatar", "confidence": CONFIRMED, "reason": "profile"},
            {"service": "eBay", "confidence": CONFIRMED, "reason": "registered"},
            {"service": "Duolingo", "confidence": NOT_FOUND, "reason": "none"},
        ]
        findings = exposure_findings(results)
        services = {f["service"] for f in findings}
        # No service whitelist: adult, marketplace and mainstream hits all survive.
        assert services == {"Pornhub", "OnlyFans", "XVideos", "Spotify", "Gravatar", "eBay"}
        # ...but a NOT_FOUND probe is not an exposure, or the score inflates.
        assert "Duolingo" not in services
        assert len(findings) == 6

    def test_exposure_findings_excludes_negative_probes(self):
        """count must track rendered findings, not the raw probe volume."""
        from email_scanner import exposure_findings, CONFIRMED, POSSIBLE, NOT_FOUND
        results = [
            {"service": "Pornhub", "confidence": CONFIRMED},
            {"service": "eBay", "confidence": POSSIBLE},
        ] + [{"service": f"Svc{i}", "confidence": NOT_FOUND} for i in range(100)]
        assert len(exposure_findings(results)) == 2


