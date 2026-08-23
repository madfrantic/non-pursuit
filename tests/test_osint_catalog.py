"""
Tests for the OSINT tool catalog: URL normalisation, the dedupe/merge rules,
and each upstream's parser.

Every parser test runs against a fixture captured from the real upstream, not
against an invented shape. The last engine in this repo to be written against
an imagined response spent months reporting false negatives, so the samples
here are trimmed copies of genuine responses.

No test performs network I/O: importers take a client, and the tests pass a
fake one.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import osint_catalog as oc  # noqa: E402
from importers import github_repos, osint_framework, osint_newsletter, static_entries  # noqa: E402


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://example.com/", "https://example.com"),
    ("http://example.com", "https://example.com"),
    ("https://WWW.Example.COM/Tool/", "https://example.com/Tool"),
    ("example.com", "https://example.com"),
    ("https://example.com/path#frag", "https://example.com/path"),
    ("https://example.com/s?q=1", "https://example.com/s?q=1"),
    ("", ""),
    (None, ""),
])
def test_normalize_url(raw, expected):
    assert oc.normalize_url(raw) == expected


def test_http_and_https_collapse_to_one_tool():
    """The brief's fuzzy-fallback case: same tool, different scheme."""
    catalog = oc.Catalog()
    catalog.merge_or_create_tool(
        {"name": "Example", "url": "http://example.com", "source": "A"})
    catalog.merge_or_create_tool(
        {"name": "Example", "url": "https://example.com/", "source": "B"})
    assert len(catalog) == 1
    assert catalog.tools[0]["sources"] == ["A", "B"]


# ---------------------------------------------------------------------------
# Identity and idempotence
# ---------------------------------------------------------------------------

def test_tool_id_is_stable_across_runs():
    first = oc.tool_id_for("https://example.com/", "Example")
    second = oc.tool_id_for("http://WWW.example.com", "Example")
    assert first == second


def test_importing_the_same_record_twice_is_a_no_op():
    catalog = oc.Catalog()
    row = {"name": "Amass", "url": "https://example.com/amass", "source": "GitHub"}
    catalog.merge_or_create_tool(dict(row))
    catalog.merge_or_create_tool(dict(row))
    assert len(catalog) == 1
    assert catalog.tools[0]["sources"] == ["GitHub"]


def test_a_full_reimport_reproduces_the_same_document():
    rows = [
        {"name": "Alpha", "url": "https://alpha.example", "source": "A"},
        {"name": "Beta", "url": "https://beta.example", "source": "B"},
    ]
    first = oc.Catalog()
    second = oc.Catalog()
    for row in rows:
        first.merge_or_create_tool(dict(row))
    for row in reversed(rows):
        second.merge_or_create_tool(dict(row))
    # Order of import must not change the outcome.
    assert ([t["id"] for t in first.to_document()["tools"]] ==
            [t["id"] for t in second.to_document()["tools"]])


# ---------------------------------------------------------------------------
# Merge rules
# ---------------------------------------------------------------------------

def test_merge_fills_gaps_without_overwriting_what_is_known():
    catalog = oc.Catalog()
    catalog.merge_or_create_tool({
        "name": "Shodan", "url": "https://www.shodan.io/", "source": "Static",
        "access_type": "freemium", "input_type": "IP",
    })
    catalog.merge_or_create_tool({
        "name": "Shodan", "url": "https://shodan.io", "source": "Framework",
        "access_type": "paid", "output_type": "Device records",
    })
    row = catalog.tools[0]
    assert len(catalog) == 1
    # The gap gets filled...
    assert row["output_type"] == "Device records"
    # ...but an existing value is not overwritten by a later source.
    assert row["access_type"] == "freemium"
    assert row["input_type"] == "IP"


def test_merge_prefers_the_longer_description():
    catalog = oc.Catalog()
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "A",
        "description": "Short.",
    })
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "B",
        "description": "A considerably more informative description.",
    })
    assert catalog.tools[0]["description"].startswith("A considerably")
    assert catalog.tools[0]["provenance"]["description"] == "B"


def test_merge_unions_categories_and_records_every_source():
    catalog = oc.Catalog()
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "A",
        "categories": ["Email", "People"],
    })
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "B",
        "categories": ["People", "Phone"],
    })
    row = catalog.tools[0]
    assert row["categories"] == ["Email", "People", "Phone"]
    assert row["sources"] == ["A", "B"]


def test_provenance_attributes_each_field_to_its_source():
    catalog = oc.Catalog()
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "A",
        "input_type": "Email",
    })
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "B",
        "license": "MIT",
    })
    provenance = catalog.tools[0]["provenance"]
    assert provenance["input_type"] == "A"
    assert provenance["license"] == "B"


def test_fuzzy_name_match_groups_near_identical_names():
    catalog = oc.Catalog()
    catalog.merge_or_create_tool({"name": "SpiderFoot", "source": "A"})
    catalog.merge_or_create_tool({"name": "Spider Foot", "source": "B"})
    assert len(catalog) == 1


def test_fuzzy_match_does_not_merge_tools_with_different_urls():
    """Near-identical names with distinct addresses are distinct tools."""
    catalog = oc.Catalog()
    catalog.merge_or_create_tool(
        {"name": "Maltego", "url": "https://maltego.com", "source": "A"})
    catalog.merge_or_create_tool(
        {"name": "Maltega", "url": "https://maltega.io", "source": "B"})
    assert len(catalog) == 2


def test_unknown_is_treated_as_absent_not_as_a_value():
    catalog = oc.Catalog()
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "A",
        "access_type": "nonsense-value",
    })
    assert catalog.tools[0]["access_type"] == "unknown"
    catalog.merge_or_create_tool({
        "name": "Tool", "url": "https://t.example", "source": "B",
        "access_type": "paid",
    })
    assert catalog.tools[0]["access_type"] == "paid"


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("free", "free"), ("freemium", "freemium"), ("paid", "paid"),
    ("free/freemium", "freemium"), ("Partially Free", "freemium"),
    ("Free", "free"), ("", "unknown"), (None, "unknown"),
])
def test_normalize_access_type(raw, expected):
    assert oc.normalize_access_type(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("live", "live"), ("degraded", "degraded"), ("down", "down"),
    ("defunct", "deprecated"), (None, "unknown"),
])
def test_normalize_status(raw, expected):
    assert oc.normalize_status(raw) == expected


def test_normalize_opsec_folds_case_variants():
    assert oc.normalize_opsec("Unknown") == "unknown"
    assert oc.normalize_opsec("passive") == "passive"


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

def test_relationships_resolve_names_to_ids_and_drop_danglers():
    catalog = oc.Catalog()
    catalog.merge_or_create_tool({
        "name": "BotScout", "url": "https://botscout.com", "source": "A",
        "related_tools": ["VirusTotal", "Nonexistent Tool"],
    })
    catalog.merge_or_create_tool(
        {"name": "VirusTotal", "url": "https://virustotal.com", "source": "A"})
    edges = catalog.relationships()
    assert len(edges) == 1
    assert edges[0]["target_tool_id"] == catalog.tools[1]["id"]


# ---------------------------------------------------------------------------
# Disk round-trip
# ---------------------------------------------------------------------------

def test_save_and_load_round_trip(tmp_path):
    catalog = oc.Catalog()
    catalog.merge_or_create_tool(
        {"name": "Tool", "url": "https://t.example", "source": "A"})
    target = str(tmp_path / "catalog.json")
    oc.save_catalog(catalog, target)

    reloaded = oc.load_catalog(target)
    assert len(reloaded) == 1
    assert reloaded.tools[0]["id"] == catalog.tools[0]["id"]


def test_load_document_survives_a_missing_or_corrupt_file(tmp_path):
    assert oc.load_document(str(tmp_path / "absent.json")) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert oc.load_document(str(broken)) is None


# ---------------------------------------------------------------------------
# Parsers: OSINT Framework
# ---------------------------------------------------------------------------

ARF_SAMPLE = {
    "name": "OSINT Framework", "type": "folder",
    "children": [{
        "name": "Username", "type": "folder",
        "children": [{
            "name": "Username Search Engines", "type": "folder",
            "children": [
                {
                    "name": "WhatsMyName Web", "type": "url",
                    "url": "https://whatsmyname.app/",
                    "description": "Username enumeration across 1500+ sites.",
                    "status": "live", "pricing": "free",
                    "bestFor": "Quick username enumeration",
                    "input": "Username", "output": "List of sites",
                    "opsec": "passive", "opsecNote": "Queries each site.",
                    "localInstall": False, "googleDork": False,
                    "registration": False, "editUrl": "https://example.com/edit",
                    "api": False, "invitationOnly": False, "deprecated": False,
                },
                {
                    "name": "GHunt (T)", "type": "url",
                    "url": "https://github.com/mxrch/GHunt",
                    "description": "Google account OSINT.",
                    "status": "live", "pricing": "free", "opsec": "active",
                    "localInstall": True, "registration": False, "api": False,
                    "invitationOnly": False, "deprecated": False,
                    "googleDork": False, "bestFor": "", "input": "Email",
                    "output": "Profile", "opsecNote": "", "editUrl": "",
                },
                {"name": "A Dork", "type": "url", "url": "", "googleDork": True},
            ],
        }],
    }],
}


def test_framework_parse_flattens_tree_into_rows():
    rows = osint_framework.parse(ARF_SAMPLE)
    # The googleDork entry has no URL and is dropped.
    assert len(rows) == 2
    row = rows[0]
    assert row["name"] == "WhatsMyName Web"
    assert row["categories"] == ["Username", "Username Search Engines"]
    assert row["source"] == "OSINT Framework"
    assert row["input_type"] == "Username"
    assert row["access_type"] == "free"


def test_framework_strips_its_own_notation_from_names():
    """"GHunt (T)" must name-match the "GHunt" every other source lists."""
    assert osint_framework.split_notation("GHunt (T)") == ("GHunt", "T")
    assert osint_framework.split_notation("Amass") == ("Amass", None)
    rows = osint_framework.parse(ARF_SAMPLE)
    assert rows[1]["name"] == "GHunt"
    # The raw name stays recoverable through source_id.
    assert rows[1]["source_id"].endswith("GHunt (T)")


def test_framework_marks_github_urls_as_repositories():
    rows = osint_framework.parse(ARF_SAMPLE)
    assert rows[1]["github_repo"] == "https://github.com/mxrch/GHunt"
    assert rows[0]["github_repo"] is None


# ---------------------------------------------------------------------------
# Parsers: OSINT Tools Library
# ---------------------------------------------------------------------------

LLMS_SAMPLE = """# OSINT Tools Library

## OSINT Newsletter Docs

- [OSINT Tools Library](https://tools.osintnewsletter.com/readme.md): A resource.
- [Username OSINT](https://tools.osintnewsletter.com/tool-categories/username-osint.md)
- [Tool Template](https://tools.osintnewsletter.com/osint-tools/tool-template.md): A template.
- [BotScout](https://tools.osintnewsletter.com/osint-tools/botscout.md): Tool Description : A bot-signature lookup service.
- [Shodan](https://tools.osintnewsletter.com/osint-tools/shodan.md): Tool Description : A search engine for devices.
"""

# Two real shapes. Most category pages use a Markdown table whose link
# carries the tool's slug; a couple embed a raw <table> with opaque
# /pages/<id> links and can only be joined on the displayed name.
CATEGORY_SAMPLE = """# Username OSINT

Username OSINT focuses on discovering usernames.

<table><thead><tr><th>Tool</th><th>Link</th></tr></thead><tbody>\
<tr><td>WhatsMyName</td><td><a href="/pages/abc">Find out more</a></td></tr>\
<tr><td>Osintly</td><td><a href="/pages/def">Find out more</a></td></tr></tbody></table>
"""

CATEGORY_MD_SAMPLE = """# Email Address OSINT

Email OSINT focuses on investigating email addresses.

| Tool       | Link                                        |
| ---------- | ------------------------------------------- |
| BotScout   | [Find out more](/osint-tools/botscout.md)   |
| Epieos     | [Find out more](/osint-tools/epieos.md)     |
"""

TOOL_PAGE_SAMPLE = """# BotScout

| **BotScout**     | **Quick Overview**                                  |
| ---------------- | --------------------------------------------------- |
| URL              | <https://botscout.com/search.htm>                   |
| What it does     | Checks names, emails and IPs against bot signatures.|
| Cost             | Free for normal use.                                |
| Account required | No for basic searches.                              |

### Cost

* [x] Free
* [ ] Partially Free
* [ ] Paid

### Account Required:

* [ ] Yes
* [x] No

### Related Tools:

* VirusTotal
* GreyNoise
"""


def test_newsletter_index_separates_tools_from_docs_and_categories():
    tools, categories = osint_newsletter.parse_index(LLMS_SAMPLE)
    names = [t["name"] for t in tools]
    assert names == ["BotScout", "Shodan"]          # readme + template excluded
    assert len(categories) == 1
    assert tools[0]["description"] == "A bot-signature lookup service."


def test_newsletter_category_page_reads_the_html_table_by_name():
    category, members = osint_newsletter.parse_category_page(CATEGORY_SAMPLE)
    assert category == "Username OSINT"
    assert [m["name"] for m in members] == ["WhatsMyName", "Osintly"]
    # No slug in a /pages/<id> link, so these can only join on name.
    assert all(m["slug"] is None for m in members)


def test_newsletter_category_page_reads_the_markdown_table_by_slug():
    """The common shape. Joining these on name alone matched 17 of 252 tools."""
    category, members = osint_newsletter.parse_category_page(CATEGORY_MD_SAMPLE)
    assert category == "Email Address OSINT"
    assert [m["slug"] for m in members] == ["botscout", "epieos"]
    # The header and the |---| separator row are not tools.
    assert len(members) == 2


def test_newsletter_tool_page_extracts_the_quick_overview():
    row = osint_newsletter.parse_tool_page(TOOL_PAGE_SAMPLE, "BotScout")
    assert row["url"] == "https://botscout.com/search.htm"
    assert row["description"].startswith("Checks names")
    assert row["access_type"] == "Free"
    assert row["registration_required"] is False
    assert row["related_tools"] == ["VirusTotal", "GreyNoise"]


def test_newsletter_tool_page_survives_a_page_missing_its_table():
    assert osint_newsletter.parse_tool_page("# Nothing here", "X") == {}


class _FakeClient:
    """Serves canned bodies by URL; fails the test on an unexpected fetch."""

    def __init__(self, bodies):
        self.bodies = bodies
        self.fetched = []

    def get_text(self, url):
        self.fetched.append(url)
        if url not in self.bodies:
            raise AssertionError(f"unexpected fetch: {url}")
        return self.bodies[url]


def test_newsletter_fetch_joins_categories_onto_tools():
    base = "https://tools.osintnewsletter.com"
    client = _FakeClient({
        f"{base}/llms.txt": LLMS_SAMPLE,
        f"{base}/tool-categories/username-osint.md":
            CATEGORY_MD_SAMPLE.replace("Email Address OSINT", "Username OSINT"),
        f"{base}/osint-tools/botscout.md": TOOL_PAGE_SAMPLE,
        f"{base}/osint-tools/shodan.md": "# Shodan",
    })
    rows = osint_newsletter.fetch(client)
    assert len(rows) == 2
    botscout = next(r for r in rows if r["name"] == "BotScout")
    assert botscout["categories"] == ["Username OSINT"]
    shodan = next(r for r in rows if r["name"] == "Shodan")
    # No URL on the page, so the library's own page becomes the address.
    assert shodan["url"].endswith("/osint-tools/shodan")


def test_newsletter_fetch_limit_caps_the_expensive_pass():
    base = "https://tools.osintnewsletter.com"
    client = _FakeClient({
        f"{base}/llms.txt": LLMS_SAMPLE,
        f"{base}/tool-categories/username-osint.md": CATEGORY_SAMPLE,
        f"{base}/osint-tools/botscout.md": TOOL_PAGE_SAMPLE,
    })
    rows = osint_newsletter.fetch(client, limit=1)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Parsers: GitHub
# ---------------------------------------------------------------------------

README_SAMPLE = """# SpiderFoot

[![Build](https://img.shields.io/badge/build-passing-green)](https://example.com)
<img src="logo.png">

**SpiderFoot** is an open source intelligence automation tool. It integrates
with many data sources.

## Installation
Not this paragraph.
"""


def test_readme_summary_skips_badges_and_takes_the_first_paragraph():
    summary = github_repos.readme_summary(README_SAMPLE)
    assert summary.startswith("SpiderFoot is an open source intelligence")
    assert "Installation" not in summary


def test_readme_summary_returns_empty_for_a_badges_only_readme():
    assert github_repos.readme_summary("# Title\n\n[![b](x)](y)\n") == ""


def test_github_row_prefers_homepage_but_keeps_the_repo():
    row = github_repos.build_row(
        {"owner": "smicallef", "repo": "spiderfoot", "categories": ["Foundational"]},
        {"name": "spiderfoot", "html_url": "https://github.com/smicallef/spiderfoot",
         "homepage": "https://www.spiderfoot.net", "description": "OSINT automation.",
         "license": {"spdx_id": "MIT"}, "pushed_at": "2026-01-01T00:00:00Z"},
        README_SAMPLE)
    assert row["url"] == "https://www.spiderfoot.net"
    assert row["github_repo"] == "https://github.com/smicallef/spiderfoot"
    assert row["license"] == "MIT"
    assert row["status"] == "live"


def test_github_row_marks_an_archived_repo_deprecated():
    row = github_repos.build_row(
        {"owner": "o", "repo": "r"},
        {"name": "r", "html_url": "https://github.com/o/r", "archived": True})
    assert row["deprecated"] is True
    assert row["status"] == "deprecated"


def test_github_row_degrades_without_api_metadata():
    """An unreachable repo still produces a usable row."""
    row = github_repos.build_row({"owner": "o", "repo": "r"}, {})
    assert row["url"] == "https://github.com/o/r"
    assert row["name"] == "r"


# ---------------------------------------------------------------------------
# Static entries
# ---------------------------------------------------------------------------

def test_static_entries_cover_the_four_unscrapable_services():
    names = {row["name"] for row in static_entries.fetch()}
    assert names == {"Censys", "Shodan", "Maltego", "Epieos"}


def test_static_entries_normalise_cleanly():
    for row in static_entries.fetch():
        normalized = oc.normalize_tool(row)
        assert normalized["url"]
        assert normalized["access_type"] in oc.ACCESS_TYPES
        assert normalized["opsec"] in oc.OPSEC_VALUES


def test_static_entries_are_copies_not_shared_state():
    """A caller mutating a row must not corrupt the module constant."""
    first = static_entries.fetch()
    first[0]["name"] = "mutated"
    assert static_entries.fetch()[0]["name"] != "mutated"


# ---------------------------------------------------------------------------
# The shipped catalog
# ---------------------------------------------------------------------------

CATALOG_FILE = os.path.join(os.path.dirname(__file__), "..", "data",
                            "osint_catalog.json")


@pytest.mark.skipif(not os.path.exists(CATALOG_FILE),
                    reason="catalog not built on this checkout")
def test_shipped_catalog_is_well_formed():
    with open(CATALOG_FILE, "r", encoding="utf-8") as handle:
        document = json.load(handle)

    tools = document["tools"]
    assert len(tools) > 100

    ids = [t["id"] for t in tools]
    assert len(ids) == len(set(ids)), "duplicate ids in the shipped catalog"

    urls = [oc.normalize_url(t["url"]) for t in tools if t.get("url")]
    assert len(urls) == len(set(urls)), "duplicate URLs survived the dedupe"

    for tool in tools:
        assert tool["name"], f"unnamed tool: {tool['id']}"
        assert tool["access_type"] in oc.ACCESS_TYPES
        assert tool["status"] in oc.STATUSES
        assert tool["opsec"] in oc.OPSEC_VALUES
        assert isinstance(tool["categories"], list)
        assert tool["sources"], f"{tool['name']} has no source attribution"


@pytest.mark.skipif(not os.path.exists(CATALOG_FILE),
                    reason="catalog not built on this checkout")
def test_shipped_catalog_contains_every_configured_source():
    document = oc.load_document(CATALOG_FILE)
    listed = set(document["sources"])
    assert {"OSINT Framework", "GitHub", "OSINT Tools Library",
            "Shodan", "Censys", "Maltego", "Epieos"} <= listed


# ---------------------------------------------------------------------------
# The page renders
#
# The parsers above are unit-tested against fixtures; that says nothing about
# whether the Streamlit page actually draws. AppTest runs the real script, so
# a bad column_config or a missing key fails here rather than in the browser.
# ---------------------------------------------------------------------------

from streamlit.testing.v1 import AppTest  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402

PAGE_PATH = os.path.join(os.path.dirname(__file__), "..", "pages",
                         "10_OSINT_Toolkit.py")

SAMPLE_TOOLS = [
    {"name": "WhatsMyName", "url": "https://whatsmyname.app",
     "description": "Username enumeration.", "source": "OSINT Framework",
     "categories": ["Username OSINT"], "access_type": "free", "status": "live",
     "opsec": "passive", "input_type": "Username", "local_install": False},
    {"name": "Shodan", "url": "https://www.shodan.io/",
     "description": "Device search engine.", "source": "Shodan",
     "categories": ["Network Infrastructure OSINT"], "access_type": "freemium",
     "status": "live", "opsec": "passive", "api_available": True,
     "registration_required": True, "related_tools": ["WhatsMyName"]},
    {"name": "Dead Tool", "url": "https://dead.example", "source": "GitHub",
     "categories": ["Username OSINT"], "deprecated": True, "status": "deprecated"},
]


def _render_page(monkeypatch, tmp_path, tools=None):
    """Run the page against a throwaway catalog and an unlocked vault."""
    monkeypatch.setattr(config, "TRACKER_DB_PATH", str(tmp_path / "tracker.db"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "0")

    catalog_path = tmp_path / "osint_catalog.json"
    if tools is not None:
        catalog = oc.Catalog()
        for row in tools:
            catalog.merge_or_create_tool(dict(row))
        oc.save_catalog(catalog, str(catalog_path))
    # An absolute path wins the os.path.join against ROOT_DIR in the page.
    monkeypatch.setattr(config, "OSINT_CATALOG_PATH", str(catalog_path))

    database.lock_vault()
    database.unlock_vault("render-test-password", str(tmp_path / "tracker.db"))

    app = AppTest.from_file(PAGE_PATH, default_timeout=60)
    app.session_state["_vault_unlocked"] = True
    return app.run()


def test_page_renders_the_catalog(monkeypatch, tmp_path):
    app = _render_page(monkeypatch, tmp_path, SAMPLE_TOOLS)
    assert not app.exception
    assert any("OSINT Toolkit" in str(t.value) for t in app.title)
    # Deprecated is hidden by default, so two of the three show.
    assert any("2" in str(c.value) for c in app.caption if "of 3" in str(c.value))


def test_page_shows_an_actionable_empty_state_without_a_catalog(monkeypatch, tmp_path):
    app = _render_page(monkeypatch, tmp_path, tools=None)
    assert not app.exception
    messages = [str(e.value) for e in list(app.info) + list(app.caption)]
    assert any("import_osint_tools" in m for m in messages)


def test_page_metrics_report_the_catalog_size(monkeypatch, tmp_path):
    app = _render_page(monkeypatch, tmp_path, SAMPLE_TOOLS)
    labels = {m.label: m.value for m in app.metric}
    assert labels["Tools"] == "3"
    assert labels["Sources"] == "3"


# ---------------------------------------------------------------------------
# The runner
#
# "If one source fails, log the error and continue with the others" is a
# stated non-negotiable, so it gets a test rather than a comment.
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402

_RUNNER_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "import_osint_tools.py")
_spec = importlib.util.spec_from_file_location("import_osint_tools", _RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


class _GoodSource:
    @staticmethod
    def fetch(client):
        return [{"name": "Good", "url": "https://good.example", "source": "Good"}]


class _DeadSource:
    @staticmethod
    def fetch(client):
        raise RuntimeError("upstream is down")


def test_runner_continues_after_a_source_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner, "IMPORTERS",
                        (("Dead", _DeadSource), ("Good", _GoodSource)))
    target = str(tmp_path / "catalog.json")

    assert runner.run(delay=0, path=target) == 0

    out = capsys.readouterr().out
    assert "FAILED" in out and "upstream is down" in out
    assert oc.load_catalog(target).tools[0]["name"] == "Good"


def test_runner_refuses_to_overwrite_when_every_source_fails(monkeypatch, tmp_path):
    target = tmp_path / "catalog.json"
    target.write_text('{"tools": [{"name": "Existing", "url": "https://e.example"}]}',
                      encoding="utf-8")
    monkeypatch.setattr(runner, "IMPORTERS", (("Dead", _DeadSource),))

    assert runner.run(delay=0, path=str(target)) == 1
    # The good catalog that was already on disk survives.
    assert oc.load_catalog(str(target)).tools[0]["name"] == "Existing"


def test_runner_dry_run_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "IMPORTERS", (("Good", _GoodSource),))
    target = tmp_path / "catalog.json"
    assert runner.run(delay=0, dry_run=True, path=str(target)) == 0
    assert not target.exists()


def test_runner_rejects_an_unknown_source_name(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "IMPORTERS", (("Good", _GoodSource),))
    assert runner.run(delay=0, only=["Nonexistent"],
                      path=str(tmp_path / "c.json")) == 1
