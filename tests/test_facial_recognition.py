"""
Coverage for biometric assistance and Yandex link building.

The facial_recognition module never persists anything; it only suggests.
All tests mock DeepFace.verify() so the test suite doesn't require the
optional ~500MB TensorFlow dependency to run. Image data is generated
on-the-fly (PIL test images), not fetched from the network.
"""
import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import facial_recognition
import yandex_osint


def _make_test_image_bytes(seed: int = 42) -> bytes:
    """Generate a small test JPEG image deterministically."""
    buf = io.BytesIO()
    img = Image.new("RGB", (128, 128), color=(seed * 7 % 256, seed * 11 % 256, seed * 13 % 256))
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestYandexOsint:
    """Yandex reverse-search URL builder — pure function, no network."""

    def test_builds_url_for_valid_avatar(self):
        url = yandex_osint.build_reverse_image_search_url("https://example.com/avatar.jpg")
        assert url.startswith("https://yandex.com/images/search?rpt=imageview&url=")
        assert "example.com" in url

    def test_empty_url_returns_empty_string(self):
        assert yandex_osint.build_reverse_image_search_url("") == ""
        assert yandex_osint.build_reverse_image_search_url(None) == ""

    def test_url_encoding_handles_special_characters(self):
        url = yandex_osint.build_reverse_image_search_url("https://example.com/avatar with spaces.jpg")
        assert "%20" in url or "+" in url  # URL-encoded space

    def test_url_not_executed(self):
        """The function never makes a network request -- it just builds a URL."""
        url = yandex_osint.build_reverse_image_search_url("https://fake.invalid.host.example.com/img.jpg")
        assert url.startswith("https://yandex.com")  # It was built, no connection attempted


class TestFacialRecognition:
    """Biometric suggestion, always with mocked DeepFace.verify()."""

    def test_facial_recognition_available_returns_bool(self):
        """The check should never raise, even if the optional dependency is missing."""
        result = facial_recognition.facial_recognition_available()
        assert isinstance(result, bool)

    def test_verify_face_returns_unavailable_when_no_deepface(self):
        with patch.object(facial_recognition, "_get_deepface", return_value=None):
            result = facial_recognition.verify_face(
                _make_test_image_bytes(1),
                _make_test_image_bytes(2),
            )
            assert result["available"] is False

    def test_verify_face_with_mocked_deepface_success(self):
        """A successful comparison with high similarity."""
        mock_deepface = MagicMock()
        mock_deepface.verify.return_value = {
            "verified": True,
            "distance": 0.1,
            "threshold": 0.4,
        }
        with patch.object(facial_recognition, "_get_deepface", return_value=mock_deepface):
            result = facial_recognition.verify_face(
                _make_test_image_bytes(1),
                _make_test_image_bytes(1),
            )
            assert result["available"] is True
            assert result["verified"] is True
            assert result["distance"] == 0.1
            assert result["similarity_pct"] == 90.0

    def test_verify_face_with_mocked_deepface_no_match(self):
        """No match: high distance, low similarity."""
        mock_deepface = MagicMock()
        mock_deepface.verify.return_value = {
            "verified": False,
            "distance": 1.8,
            "threshold": 0.4,
        }
        with patch.object(facial_recognition, "_get_deepface", return_value=mock_deepface):
            result = facial_recognition.verify_face(
                _make_test_image_bytes(1),
                _make_test_image_bytes(99),
            )
            assert result["available"] is True
            assert result["verified"] is False
            assert result["similarity_pct"] == 0.0

    def test_verify_face_handles_deepface_exception(self):
        """When the model crashes or a face is undetectable, degrade gracefully."""
        mock_deepface = MagicMock()
        mock_deepface.verify.side_effect = ValueError("Face not detected")
        with patch.object(facial_recognition, "_get_deepface", return_value=mock_deepface):
            result = facial_recognition.verify_face(
                _make_test_image_bytes(1),
                _make_test_image_bytes(2),
            )
            assert result["available"] is True
            assert result["error"] is not None
            assert "Face not detected" in result["error"]

    def test_suggest_verification_no_master_photo(self):
        """Can't suggest without a master photo."""
        result = facial_recognition.suggest_verification(
            b"",
            "https://example.com/avatar.jpg",
        )
        assert result["available"] is False

    def test_suggest_verification_no_avatar_url(self):
        """Can't suggest without an avatar URL."""
        result = facial_recognition.suggest_verification(
            _make_test_image_bytes(1),
            "",
        )
        assert result["available"] is False

    def test_suggest_verification_avatar_fetch_fails(self):
        """When the avatar can't be fetched, degrade gracefully."""
        import requests
        with patch("facial_recognition.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Network error")
            result = facial_recognition.suggest_verification(
                _make_test_image_bytes(1),
                "https://example.com/avatar.jpg",
            )
            assert result["available"] is False
            assert "fetch" in result["error"].lower()

    def test_suggest_verification_with_match(self):
        """Happy path: master photo, avatar available, high match score."""
        mock_deepface = MagicMock()
        mock_deepface.verify.return_value = {
            "verified": True,
            "distance": 0.15,
            "threshold": 0.4,
        }
        with patch("facial_recognition.requests.get") as mock_fetch, \
             patch.object(facial_recognition, "_get_deepface", return_value=mock_deepface):
            mock_fetch.return_value.ok = True
            mock_fetch.return_value.content = _make_test_image_bytes(99)

            result = facial_recognition.suggest_verification(
                _make_test_image_bytes(1),
                "https://example.com/avatar.jpg",
            )
            assert result["available"] is True
            assert result["verified"] is True
            assert "suggested: confirm" in result["suggested_label"].lower()


class TestSimilarityPercent:
    """The similarity_pct display heuristic."""

    def test_perfect_match(self):
        """Distance 0 (identical) -> 100%."""
        pct = facial_recognition._similarity_pct(0.0)
        assert pct == 100.0

    def test_no_match(self):
        """Distance 1.0 or higher -> 0%."""
        assert facial_recognition._similarity_pct(1.0) == 0.0
        assert facial_recognition._similarity_pct(2.0) == 0.0

    def test_middle_range(self):
        """Distance 0.5 -> 50%."""
        pct = facial_recognition._similarity_pct(0.5)
        assert pct == 50.0
