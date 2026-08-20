"""
Headless compiler for CCPA demand letters.

This is the single source of truth for legally binding text. It was
extracted out of components/letters.py so the on-screen preview and the
audit-trail ZIP render from the same code path -- two render paths for
statutory language is exactly the kind of thing that drifts silently and
then ships a letter citing the wrong subsection.

Deliberately free of Streamlit so it runs anywhere the text is needed:
the UI, the export pipeline, a test, a background job.

The template directory is resolved relative to this file rather than the
working directory. The UI happened to work with a bare relative path
because Streamlit runs from the repo root, but anything invoked from
elsewhere -- pytest from a subdirectory, a packaging script -- would have
silently failed to find the template.
"""
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

TEMPLATE_FILES = {
    "ccpa_deletion": "ccpa_deletion_demand.j2",
}

PROFILE_FIELDS = ("name", "location", "email")


def _environment() -> Environment:
    # autoescape stays off for these templates: the output is plain text
    # destined for an email body or a .txt file, and HTML-escaping a
    # name like "O'Brien" would corrupt the letter.
    # keep_trailing_newline is deliberately left at its default (off).
    # Turning it on appends a newline the pre-extraction renderer never
    # produced, which would make every compiled letter differ by a byte
    # from what this app has been generating -- not worth it for a file
    # that's about to be pasted into an email body anyway.
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
    )


def available_templates() -> list:
    return sorted(TEMPLATE_FILES)


def compile_demand_letter(broker_name: str, target_profile: dict,
                          record_url: str = None,
                          template_type: str = "ccpa_deletion",
                          current_date: str = None) -> str:
    """Render one broker's demand letter.

    target_profile carries the identity block the letter is personalized
    with: name, location, email. record_url is the specific listing being
    demanded down; it's optional so the packager can still compile a
    letter for a broker logged without one, in which case the template
    receives an empty string rather than the literal "None".

    current_date is injectable purely so tests can assert byte-identical
    output for identical inputs -- it defaults to today, which is what
    every real caller wants.
    """
    if template_type not in TEMPLATE_FILES:
        raise ValueError(
            f"Unknown template_type {template_type!r}; expected one of {available_templates()}"
        )

    template = _environment().get_template(TEMPLATE_FILES[template_type])
    return template.render(
        broker_name=broker_name,
        user_name=target_profile.get("name", ""),
        user_location=target_profile.get("location", ""),
        user_email=target_profile.get("email", ""),
        record_url=record_url or "",
        current_date=current_date or datetime.now().strftime("%B %d, %Y"),
    )


def letter_filename(broker_name: str, extension: str = "txt") -> str:
    """A filesystem-safe name for one broker's letter, stable enough to
    use as a ZIP entry."""
    slug = "".join(c if c.isalnum() else "_" for c in broker_name.strip().lower())
    slug = "_".join(part for part in slug.split("_") if part)
    return f"{slug}_demand.{extension}"
