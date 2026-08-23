"""
OSINT Tools Library (tools.osintnewsletter.com).

The site publishes an llms.txt index and a Markdown rendition of every page,
so nothing here parses HTML-for-humans except the category tables, which are
raw <table> blocks embedded in the Markdown.

THREE PASSES, BECAUSE THE INDEX DOES NOT CARRY EVERYTHING

llms.txt lists all ~250 tool pages with a name and a blurb, but not the tool's
own URL and not its category. The category pages list tool *names* against
opaque /pages/<id> links that do not map to the .md slugs. So:

  1. llms.txt          -> the set of tool pages, plus a fallback description
  2. category pages    -> name -> [category]         (31 requests)
  3. each tool page    -> URL, cost, registration, related tools

Pass 3 is the expensive one -- one request per tool at the client's per-host
rate limit. `limit=` exists so a developer can exercise the parser against a
handful of pages without a five-minute run.

The upstream is a documentation site with no API and no stated rate policy;
the shared client's one-request-per-second default is what keeps this
neighbourly, and the importer never parallelises around it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE = "https://tools.osintnewsletter.com"
LLMS_TXT = f"{BASE}/llms.txt"

SOURCE = "OSINT Tools Library"

# Index entries that are documentation about the library, not tools in it.
_NOT_TOOLS = {
    "readme", "tool-categories", "osint-tools", "tool-template",
    "submission-guide", "llms",
}

_INDEX_LINE = re.compile(r"^\s*-\s*\[(?P<name>[^\]]+)\]\((?P<url>https?://[^)]+)\)\s*(?::\s*(?P<desc>.*))?$")

# Quick Overview rows: "| URL | <https://example.com> |"
_TABLE_ROW = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$")
_ANGLE_URL = re.compile(r"<(https?://[^>]+)>")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")

# "* [x] Free" / "* [ ] Paid"
_CHECKBOX = re.compile(r"^\s*[*-]\s*\[(?P<mark>[ xX])\]\s*(?P<label>.+?)\s*$")
_BULLET = re.compile(r"^\s*[*-]\s+(?P<text>.+?)\s*$")
_HEADING = re.compile(r"^\s*(?P<hashes>#{1,6})\s*(?P<text>.+?)\s*:?\s*$")

# Category pages come in two shapes. Most use a Markdown pipe table whose
# second cell links to /osint-tools/<slug>.md -- that slug is an exact join
# key back to the index, so no name matching is needed. A couple instead embed
# a raw <table> pointing at opaque /pages/<id> links, which can only be joined
# on the displayed name. Handling only the HTML form matched 17 of 252 tools.
_MD_TABLE_LINK = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*\[[^\]]*\]\((?P<href>[^)]*/osint-tools/[^)]+)\)\s*\|",
    re.M)
_HTML_TABLE_CELL = re.compile(r"<tr>\s*<td>(?P<name>.*?)</td>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")

# Entity escapes GitBook leaves in the Markdown export.
_ENTITIES = (("&#x20;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
             ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "))


def _clean(text: str) -> str:
    out = text or ""
    for entity, replacement in _ENTITIES:
        out = out.replace(entity, replacement)
    out = _TAG.sub("", out)
    return re.sub(r"\s+", " ", out).strip()


def _slug_of(page_url: str) -> str:
    return page_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")


# ---------------------------------------------------------------------------
# Pass 1: the index
# ---------------------------------------------------------------------------

def parse_index(text: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """(tool page entries, category page URLs) from llms.txt."""
    tools: List[Dict[str, str]] = []
    categories: List[str] = []
    seen: set = set()

    for line in (text or "").splitlines():
        match = _INDEX_LINE.match(line)
        if not match:
            continue
        url = match.group("url").strip()
        name = _clean(match.group("name"))
        description = _clean(match.group("desc") or "")
        slug = _slug_of(url)

        if "/tool-categories/" in url:
            if url not in categories:
                categories.append(url)
            continue
        if "/osint-tools/" not in url or slug in _NOT_TOOLS or slug in seen:
            continue
        seen.add(slug)

        # Every blurb on the site opens with this label.
        description = re.sub(r"^Tool Description\s*:\s*", "", description).strip()
        tools.append({"name": name, "page_url": url, "slug": slug,
                      "description": description})
    return tools, categories


# ---------------------------------------------------------------------------
# Pass 2: category membership
# ---------------------------------------------------------------------------

def parse_category_page(text: str,
                        fallback: str = "") -> Tuple[str, List[Dict[str, Optional[str]]]]:
    """(category name, [{"name", "slug"}]) from a category page.

    `slug` is None for the HTML-table pages, whose links do not carry one.
    """
    category = fallback
    for line in (text or "").splitlines():
        heading = _HEADING.match(line)
        if heading and len(heading.group("hashes")) == 1:
            category = _clean(heading.group("text"))
            break

    entries: List[Dict[str, Optional[str]]] = []
    seen: set = set()

    for match in _MD_TABLE_LINK.finditer(text or ""):
        name = _clean(match.group("name"))
        if not name or name.lower() == "tool" or set(name) <= set("- "):
            continue
        slug = _slug_of(match.group("href"))
        if slug in seen:
            continue
        seen.add(slug)
        entries.append({"name": name, "slug": slug})

    if not entries:
        for match in _HTML_TABLE_CELL.finditer(text or ""):
            name = _clean(match.group("name"))
            if name and name.lower() != "tool" and name.lower() not in seen:
                seen.add(name.lower())
                entries.append({"name": name, "slug": None})

    return category, entries


def _category_name_from_url(url: str) -> str:
    return _slug_of(url).replace("-", " ").title()


# ---------------------------------------------------------------------------
# Pass 3: the tool page
# ---------------------------------------------------------------------------

def _sections(text: str) -> Dict[str, List[str]]:
    """Body lines grouped under their nearest heading, lower-cased keys."""
    grouped: Dict[str, List[str]] = {"": []}
    current = ""
    for line in (text or "").splitlines():
        heading = _HEADING.match(line)
        if heading and line.lstrip().startswith("#"):
            current = _clean(heading.group("text")).lower()
            grouped.setdefault(current, [])
            continue
        grouped.setdefault(current, []).append(line)
    return grouped


def _checked_labels(lines: Iterable[str]) -> List[str]:
    return [_clean(m.group("label")) for line in lines
            if (m := _CHECKBOX.match(line)) and m.group("mark").lower() == "x"]


def parse_tool_page(text: str, name: str = "") -> Dict[str, Any]:
    """Pull the structured facts out of one tool's Markdown page."""
    row: Dict[str, Any] = {}
    overview: Dict[str, str] = {}

    for line in (text or "").splitlines():
        match = _TABLE_ROW.match(line)
        if not match:
            continue
        key = _clean(match.group("key")).strip("*").strip().lower()
        value = match.group("value")
        if not key or key.startswith("---") or not value.strip("- "):
            continue
        overview.setdefault(key, value)

    url_cell = overview.get("url", "")
    url_match = _ANGLE_URL.search(url_cell) or _MD_LINK.search(url_cell)
    if url_match:
        row["url"] = url_match.group(url_match.re.groups)

    if overview.get("what it does"):
        row["description"] = _clean(overview["what it does"])
    if overview.get("use in reporting"):
        row["best_for"] = _clean(overview["use in reporting"])

    sections = _sections(text)

    # Cost: the checkbox block is authoritative, the table cell is prose.
    cost_flags = _checked_labels(sections.get("cost", []))
    if cost_flags:
        row["access_type"] = cost_flags[0]
    elif overview.get("cost"):
        row["access_type"] = _clean(overview["cost"])

    # Account required: "Yes"/"No" checkboxes under their own heading.
    account_flags = _checked_labels(sections.get("account required", []))
    if account_flags:
        row["registration_required"] = account_flags[0].strip().lower().startswith("y")
    elif overview.get("account required"):
        answer = _clean(overview["account required"]).lower()
        if answer.startswith("yes"):
            row["registration_required"] = True
        elif answer.startswith("no"):
            row["registration_required"] = False

    related = [_clean(m.group("text")) for line in sections.get("related tools", [])
               if (m := _BULLET.match(line))]
    if related:
        row["related_tools"] = [r for r in related if r and r.lower() != name.lower()]

    if row.get("url", "").lower().startswith("https://github.com"):
        row["github_repo"] = row["url"]

    return row


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def fetch(client, limit: Optional[int] = None,
          on_progress=None) -> List[Dict[str, Any]]:
    """Every tool in the library, as catalog rows.

    `limit` caps pass 3 for development runs. `on_progress(done, total)` is
    called as tool pages come in, so the CLI can show movement across what is
    otherwise several silent minutes.
    """
    entries, category_urls = parse_index(client.get_text(LLMS_TXT))

    # Two join keys: the slug is exact, the lower-cased name is the fallback
    # for the pages that do not expose one.
    by_slug: Dict[str, List[str]] = {}
    by_name: Dict[str, List[str]] = {}
    for url in category_urls:
        try:
            body = client.get_text(url)
        except Exception:  # noqa: BLE001 - a missing category loses a label, not a tool
            continue
        category, members = parse_category_page(body, _category_name_from_url(url))
        if not category:
            continue
        for member in members:
            key_map, key = ((by_slug, member["slug"]) if member.get("slug")
                            else (by_name, (member["name"] or "").lower()))
            if not key:
                continue
            bucket = key_map.setdefault(key, [])
            if category not in bucket:
                bucket.append(category)

    if limit is not None:
        entries = entries[:limit]

    rows: List[Dict[str, Any]] = []
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        row: Dict[str, Any] = {
            "name": entry["name"],
            "description": entry["description"],
            "source": SOURCE,
            "source_id": entry["slug"],
            "documentation_url": entry["page_url"].removesuffix(".md"),
            "categories": (by_slug.get(entry["slug"])
                           or by_name.get(entry["name"].lower(), [])),
            "status": "live",
        }
        try:
            row.update(parse_tool_page(client.get_text(entry["page_url"]),
                                       entry["name"]))
        except Exception:  # noqa: BLE001 - index data is still worth keeping
            pass

        # Without a URL there is nothing to dedupe on and nothing to click;
        # the library's own page becomes the address of record.
        if not row.get("url"):
            row["url"] = row["documentation_url"]

        rows.append(row)
        if on_progress:
            on_progress(index, total)
    return rows
