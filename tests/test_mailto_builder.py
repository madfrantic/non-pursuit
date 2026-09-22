from mailto_builder import build_mailto_link, is_mailto_safe, mailto_length


def test_build_mailto_link_includes_address_and_encoded_fields():
    link = build_mailto_link("privacy@example.com", "Deletion Request", "Please delete my data.")
    assert link.startswith("mailto:privacy@example.com?")
    assert "subject=Deletion%20Request" in link
    assert "body=Please%20delete%20my%20data." in link


def test_build_mailto_link_encodes_special_characters():
    # Characters like &, =, and newlines would corrupt the query string or
    # get silently dropped by some mail clients if left unencoded.
    link = build_mailto_link("a@b.com", "Q&A = tricky", "Line one\nLine two")
    assert "&" not in link.split("subject=", 1)[1].split("&body=")[0]
    assert "%0A" in link  # newline encoded, not a raw line break


def test_mailto_length_matches_built_link_length():
    to_address, subject, body = "a@b.com", "Subject", "Body text here."
    assert mailto_length(to_address, subject, body) == len(build_mailto_link(to_address, subject, body))


def test_is_mailto_safe_true_under_threshold():
    assert is_mailto_safe("a@b.com", "Short subject", "Short body.", safe_length=2000) is True


def test_is_mailto_safe_false_over_threshold():
    long_body = "x" * 5000
    assert is_mailto_safe("a@b.com", "Subject", long_body, safe_length=2000) is False


def test_is_mailto_safe_boundary_is_inclusive():
    length = mailto_length("a@b.com", "Subject", "Body")
    assert is_mailto_safe("a@b.com", "Subject", "Body", safe_length=length) is True
    assert is_mailto_safe("a@b.com", "Subject", "Body", safe_length=length - 1) is False
