"""Small validators shared by any page that collects contact info or URLs
-- kept in one place so an email/URL only gets validated one way, at the
one place (the Dashboard profile form) where it's actually entered."""
import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip())) if value else False


def is_valid_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://")) if value else False
