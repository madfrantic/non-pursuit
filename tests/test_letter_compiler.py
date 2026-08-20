"""
Coverage for the headless letter compiler.

This renders legally binding text, so the tests care about two things:
that identical inputs produce identical output (no hidden nondeterminism
in a document someone may rely on), and that the statutory citations
actually survive rendering.
"""
import pytest

from letter_compiler import (
    available_templates,
    compile_demand_letter,
    letter_filename,
)

PROFILE = {
    "name": "Jane Q. Doe",
    "location": "San Francisco, CA",
    "email": "jane@example.com",
}

BROKERS = ["Spokeo", "MyLife", "WhitePages", "BeenVerified", "Radaris"]


def test_identical_inputs_produce_identical_output():
    """Same profile in, byte-identical letter out -- across repeated calls."""
    first = compile_demand_letter("Spokeo", PROFILE, "https://x.test/1", current_date="August 19, 2026")
    second = compile_demand_letter("Spokeo", PROFILE, "https://x.test/1", current_date="August 19, 2026")
    assert first == second


@pytest.mark.parametrize("broker", BROKERS)
def test_every_broker_compiles_with_its_own_name(broker):
    letter = compile_demand_letter(broker, PROFILE, "https://x.test/1")
    assert broker in letter
    assert letter.count(broker) >= 2  # addressee line and the body demand


@pytest.mark.parametrize("broker", BROKERS)
def test_statutory_citations_survive_rendering(broker):
    """The whole point of the letter is the citations -- a template change
    that drops one should fail loudly."""
    letter = compile_demand_letter(broker, PROFILE, "https://x.test/1")
    assert "1798.105" in letter
    assert "1798.130" in letter
    assert "45 days" in letter


def test_profile_fields_appear_in_letter():
    letter = compile_demand_letter("Spokeo", PROFILE, "https://x.test/1")
    assert PROFILE["name"] in letter
    assert PROFILE["location"] in letter
    assert PROFILE["email"] in letter


def test_record_url_is_embedded():
    letter = compile_demand_letter("Spokeo", PROFILE, "https://spokeo.test/record/9")
    assert "https://spokeo.test/record/9" in letter


def test_missing_record_url_renders_empty_not_none():
    """A request logged without a record URL still gets a letter -- but it
    must never contain the literal string 'None'."""
    letter = compile_demand_letter("Spokeo", PROFILE, None)
    assert "None" not in letter


def test_missing_profile_fields_do_not_raise():
    letter = compile_demand_letter("Spokeo", {}, "https://x.test/1")
    assert "1798.105" in letter


def test_unknown_template_type_raises():
    with pytest.raises(ValueError):
        compile_demand_letter("Spokeo", PROFILE, template_type="not_a_template")


def test_available_templates_includes_ccpa():
    assert "ccpa_deletion" in available_templates()


def test_compiler_needs_no_working_directory(tmp_path, monkeypatch):
    """Template lookup resolves from the module's own location, so running
    from another directory must still work -- the pre-extraction code used
    a bare relative path and would have failed here."""
    monkeypatch.chdir(tmp_path)
    assert "1798.105" in compile_demand_letter("Spokeo", PROFILE, "https://x.test/1")


@pytest.mark.parametrize("broker,expected", [
    ("Spokeo", "spokeo_demand.txt"),
    ("WhitePages", "whitepages_demand.txt"),
    ("Example Data Broker", "example_data_broker_demand.txt"),
])
def test_letter_filename_is_filesystem_safe(broker, expected):
    assert letter_filename(broker) == expected
