"""
Coverage for the OSINT dossier PDF.

The interesting cases here are all inputs the generator has no control
over. Findings come straight off scraped pages and third-party APIs, so
they arrive with emoji in platform names, smart quotes in breach
descriptions, and profile URLs long enough to overflow the text column --
each of which raises inside fpdf2 if it reaches the renderer untouched.
The point of these tests is that none of them can take the export down
in front of an audience.
"""
import asyncio

import pdf_generator
import presentation_mode

PDF_MAGIC = b"%PDF-"

PROFILE = {
    "name": "Jane Doe",
    "handle": "janedoe_dev",
    "email": "jane.doe@example.com",
    "domain": "example.com",
}

FOOTPRINT = {
    "records": [
        {
            "platform": "GitHub",
            "category": "Development",
            "confidence": "confirmed",
            "profile_url": "https://github.com/janedoe_dev",
        },
    ],
}

EMAIL = {
    "records": [
        {"service": "Have I Been Pwned", "vector": "Email in breach database"},
    ],
}


def test_generates_a_real_pdf():
    output = pdf_generator.generate_osint_pdf(PROFILE, FOOTPRINT, EMAIL)
    assert isinstance(output, bytes)
    assert output.startswith(PDF_MAGIC)


def test_empty_scan_still_produces_a_dossier():
    """A scan that found nothing is still worth documenting -- it records
    that the search was run."""
    output = pdf_generator.generate_osint_pdf({}, None, [])
    assert output.startswith(PDF_MAGIC)


def test_non_latin1_text_does_not_raise():
    """Emoji and smart quotes are outside the core font's encoding and
    raise at render time unless sanitized first."""
    output = pdf_generator.generate_osint_pdf(
        {"name": "Zoë — 日本語 🎭", "handle": "ünïcode"},
        {"records": [{"platform": "🐙 GitHub", "reason": "It’s a “match”"}]},
        {"records": [{"service": "Have I Been Pwned ✓"}]},
    )
    assert output.startswith(PDF_MAGIC)


def test_very_long_unbroken_url_does_not_raise():
    """multi_cell() has no wrap point inside a single long token and
    raises 'not enough horizontal space' -- long tokens get soft breaks."""
    output = pdf_generator.generate_osint_pdf(
        PROFILE,
        {"records": [{"platform": "X", "profile_url": "https://x.com/" + "a" * 900}]},
        {},
    )
    assert output.startswith(PDF_MAGIC)


def test_malformed_records_are_skipped_not_fatal():
    output = pdf_generator.generate_osint_pdf(
        PROFILE,
        {"records": ["not-a-dict", None, 42, {"platform": "GitHub"}]},
        {"records": None},
    )
    assert output.startswith(PDF_MAGIC)


def test_accepts_a_bare_record_list_as_well_as_a_vector_dict():
    from_dict = pdf_generator.generate_osint_pdf(PROFILE, FOOTPRINT, EMAIL)
    from_list = pdf_generator.generate_osint_pdf(
        PROFILE, FOOTPRINT["records"], EMAIL["records"]
    )
    assert from_dict.startswith(PDF_MAGIC)
    assert from_list.startswith(PDF_MAGIC)


def test_profile_accepts_either_key_spelling():
    """Callers hold either the OSINT profile shape or the session one."""
    assert pdf_generator._profile_value({"name": "A"}, "name", "full_name") == "A"
    assert pdf_generator._profile_value({"full_name": "B"}, "name", "full_name") == "B"
    assert pdf_generator._profile_value({}, "name", "full_name") == ""


def test_build_dossier_bytes_reads_either_findings_shape():
    """run_full_osint_sweep() exposes vectors at the top level and under
    'vectors'; both must work."""
    top_level = pdf_generator.build_dossier_bytes(
        PROFILE, {"footprint": FOOTPRINT, "email": EMAIL}
    )
    nested = pdf_generator.build_dossier_bytes(
        PROFILE, {"vectors": {"footprint": FOOTPRINT, "email": EMAIL}}
    )
    assert top_level.startswith(PDF_MAGIC)
    assert nested.startswith(PDF_MAGIC)


def test_build_dossier_bytes_survives_no_findings():
    assert pdf_generator.build_dossier_bytes(PROFILE, {}).startswith(PDF_MAGIC)
    assert pdf_generator.build_dossier_bytes(PROFILE, None).startswith(PDF_MAGIC)


def test_presentation_mode_findings_produce_a_dossier():
    """The live-demo path: mock sweep straight into the download button."""
    findings = asyncio.run(presentation_mode.get_mock_osint_findings())
    output = pdf_generator.build_dossier_bytes(presentation_mode.MOCK_PROFILE, findings)
    assert output.startswith(PDF_MAGIC)


def test_mock_findings_expose_vectors_at_the_top_level():
    """The Master Dashboard's section renders read findings['footprint'],
    not findings['vectors']['footprint'] -- returning only the nested
    shape showed headline counts above four empty sections."""
    findings = asyncio.run(presentation_mode.get_mock_osint_findings())
    for vector in ("footprint", "email", "github", "sec", "fec",
                   "courtlistener", "infrastructure"):
        assert vector in findings, f"{vector} missing from top level"
        assert findings[vector] is findings["vectors"][vector]
