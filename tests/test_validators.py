from validators import is_valid_url


def test_is_valid_url_requires_http_host():
    assert is_valid_url("https://example.com/path") is True
    assert is_valid_url("https://") is False
    assert is_valid_url("https://?query") is False
    assert is_valid_url("https://#fragment") is False


def test_is_valid_url_rejects_credentials_and_whitespace():
    assert is_valid_url("https://user:pass@example.com") is False
    assert is_valid_url("https://example.com/a path") is False
    assert is_valid_url("https://example.com:bad") is False