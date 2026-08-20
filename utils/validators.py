"""Small validators shared by any page that collects contact info or URLs
-- kept in one place so an email/URL only gets validated one way, at the
one place (the Dashboard profile form) where it's actually entered."""
import re
from urllib.parse import urlsplit

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip())) if value else False


def is_valid_url(value: str) -> bool:
    if not value:
        return False
    candidate = value.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return False
    try:
        parsed = urlsplit(candidate)
        parsed.port  # Force malformed port values to raise ValueError.
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )
