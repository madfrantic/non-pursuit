"""
Mailto link builder.

Email clients cap mailto: URL length inconsistently — Outlook and several
mobile clients have been observed truncating the body well under 2,000
characters, and they do it silently, so a user can send a legal demand
letter that gets cut off mid-sentence without any warning. Percent-encoding
also roughly triples the length of the plaintext, so a 700-word letter can
blow past the safe threshold fast.

This module builds a correctly encoded mailto: link and separately reports
whether it's safe to rely on, so the caller (app.py) can steer the user to
download-and-paste instead when it isn't.
"""
from urllib.parse import quote


def build_mailto_link(to_address: str, subject: str, body: str) -> str:
    """Build a properly URL-encoded mailto: link."""
    encoded_subject = quote(subject)
    encoded_body = quote(body)
    return f"mailto:{to_address}?subject={encoded_subject}&body={encoded_body}"


def mailto_length(to_address: str, subject: str, body: str) -> int:
    """Character length of the fully encoded mailto link."""
    return len(build_mailto_link(to_address, subject, body))


def is_mailto_safe(to_address: str, subject: str, body: str, safe_length: int) -> bool:
    """Whether the encoded mailto link is short enough to rely on."""
    return mailto_length(to_address, subject, body) <= safe_length
