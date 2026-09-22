"""
The OSINT tool catalog: a merged, deduplicated index of tools and datasets
pulled from ten upstream sources.

WHY JSON AND NOT tracker.db

Everything else in this app persists to data/tracker.db. This does not, and
deliberately so. tracker.db holds real broker requests, exposure findings and
identity PII; it carries retention-based purge logic, sits behind the vault
gate, and any schema change to it is a human-sign-off zone. The catalog is the
opposite kind of data -- public, non-personal, reproducible from upstream at
any time, and useless to an attacker. Mixing the two would put reference data
under a retention policy written for personal data, and would put a routine
importer inside the migration path of the database that holds the user's case
file. So the catalog is a plain JSON document that the importers rewrite whole
and the UI reads.

IDENTITY AND IDEMPOTENCE

A tool's id is a UUID5 over its normalised URL, not a random UUID4. Re-running
the whole import therefore reproduces the same ids rather than churning them,
which is what makes the pipeline safe to run on a schedule.

PROVENANCE

Ten sources describe overlapping tools with differing completeness, so a merged
record is a composite. `sources` lists every upstream that contributed, and
`provenance` records which one supplied each individual field -- without it a
merged row is an unattributable claim, and there would be no way to tell a
value the OSINT Framework asserted from one a scraped page implied.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

CATALOG_VERSION = 1

# Namespace for deterministic tool ids. Fixed forever: changing it renumbers
# every tool in every existing catalog file.
_ID_NAMESPACE = uuid.UUID("6f3c1c9e-1d2b-5a44-9d3e-0b6a7c5e8f21")

# Levenshtein-style similarity above which two differently-URL'd tools are
# taken to be the same tool. The brief specifies 0.9; difflib's ratio is used
# rather than python-Levenshtein so the pipeline needs no new dependency.
NAME_MATCH_THRESHOLD = 0.9

ACCESS_TYPES = ("free", "freemium", "paid", "unknown")
STATUSES = ("live", "degraded", "down", "deprecated", "unknown")
OPSEC_VALUES = ("passive", "active", "unknown")

# The schema from the integration brief. Order is the column order the UI and
# any CSV export inherit, so it is written once here.
TOOL_FIELDS = (
    "id", "name", "description", "url", "source", "sources", "source_id",
    "categories", "access_type", "status", "best_for", "input_type",
    "output_type", "opsec", "opsec_note", "local_install",
    "registration_required", "api_available", "api_docs_url",
    "invitation_only", "deprecated", "github_repo", "license",
    "documentation_url", "last_updated", "imported_at", "provenance",
    "related_tools",
)

_BOOL_FIELDS = ("local_install", "registration_required", "api_available",
                "invitation_only", "deprecated")
_LIST_FIELDS = ("categories", "sources", "related_tools")

# Fields a later source may fill in but must never silently overwrite once a
# value exists -- see merge_or_create_tool.
_SCALAR_FIELDS = tuple(
    f for f in TOOL_FIELDS
    if f not in _LIST_FIELDS + ("id", "provenance", "imported_at")
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Canonical form of a tool URL, for equality testing.

    Lower-cases the hostname, forces https, drops a leading www., strips a
    trailing slash and discards fragments. Two records pointing at the same
    service must normalise identically or the dedupe pass will not see them
    as the same tool.
    """
    if not url or not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""
    if "//" not in raw:
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = (parts.path or "").rstrip("/")
    # Fragments are never meaningful for tool identity; query strings can be
    # (a tool that lives at ?page=search), so they are kept.
    return urlunsplit(("https", host, path, parts.query, ""))


def _name_key(name: str) -> str:
    """Aggressively normalised name, for the exact-match bucket."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def name_similarity(left: str, right: str) -> float:
    """0.0-1.0 similarity of two tool names."""
    a, b = (left or "").strip().lower(), (right or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def tool_id_for(url: str, name: str = "") -> str:
    """Deterministic id: same tool, same id, every run.

    Keyed on the normalised URL where there is one, falling back to the name
    so that a URL-less entry still gets a stable id instead of a fresh one
    on each import.
    """
    seed = normalize_url(url) or f"name:{_name_key(name)}"
    return str(uuid.uuid5(_ID_NAMESPACE, seed))


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in ("true", "yes", "y", "1"):
        return True
    if text in ("false", "no", "n", "0"):
        return False
    return None


def normalize_access_type(value: Any) -> str:
    """Map an upstream pricing string onto the closed access_type vocabulary."""
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if "/" in text or text in ("partially free", "partial"):
        # "free/freemium" and the newsletter's "Partially Free" both mean the
        # same thing: usable without paying, up to a limit.
        return "freemium"
    if text in ACCESS_TYPES:
        return text
    if "freemium" in text:
        return "freemium"
    if "paid" in text or "subscription" in text:
        return "paid"
    if "free" in text:
        return "free"
    return "unknown"


def normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in STATUSES:
        return text
    if text in ("defunct", "dead", "discontinued", "retired"):
        return "deprecated"
    return "unknown"


def normalize_opsec(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in OPSEC_VALUES else "unknown"


def blank_tool() -> Dict[str, Any]:
    row: Dict[str, Any] = {field: None for field in TOOL_FIELDS}
    for field in _LIST_FIELDS:
        row[field] = []
    row["provenance"] = {}
    row["access_type"] = "unknown"
    row["status"] = "unknown"
    row["opsec"] = "unknown"
    return row


def normalize_tool(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce one importer's dict into a full, well-typed catalog row."""
    row = blank_tool()
    for field in TOOL_FIELDS:
        if field in data and data[field] is not None:
            row[field] = data[field]

    row["name"] = (row.get("name") or "").strip()
    row["url"] = (row.get("url") or "").strip()
    row["access_type"] = normalize_access_type(row.get("access_type"))
    row["status"] = normalize_status(row.get("status"))
    row["opsec"] = normalize_opsec(row.get("opsec"))

    for field in _BOOL_FIELDS:
        row[field] = _coerce_bool(row.get(field))

    for field in _LIST_FIELDS:
        value = row.get(field) or []
        if isinstance(value, str):
            value = [value]
        # De-duplicate while preserving order; upstream category paths repeat.
        seen, cleaned = set(), []
        for item in value:
            text = str(item).strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                cleaned.append(text)
        row[field] = cleaned

    if row.get("source") and not row["sources"]:
        row["sources"] = [row["source"]]
    if row["sources"] and not row.get("source"):
        row["source"] = row["sources"][0]

    row["id"] = row.get("id") or tool_id_for(row["url"], row["name"])
    if not row.get("provenance"):
        origin = row.get("source") or "unknown"
        row["provenance"] = {
            field: origin
            for field in _SCALAR_FIELDS
            if row.get(field) not in (None, "", "unknown")
        }
    return row


# ---------------------------------------------------------------------------
# The catalog and its dedupe pass
# ---------------------------------------------------------------------------

class Catalog:
    """An in-memory catalog with URL and name indexes for the merge pass."""

    def __init__(self, tools: Optional[Iterable[Dict[str, Any]]] = None):
        self.tools: List[Dict[str, Any]] = []
        self._by_url: Dict[str, Dict[str, Any]] = {}
        self._by_name: Dict[str, Dict[str, Any]] = {}
        # First letter -> rows, so the fuzzy fallback compares against a
        # bucket instead of every row in the catalog.
        self._name_buckets: Dict[str, List[Dict[str, Any]]] = {}
        self.merges: List[str] = []
        for tool in tools or []:
            self._index(normalize_tool(tool))

    def __len__(self) -> int:
        return len(self.tools)

    def _index(self, row: Dict[str, Any]) -> Dict[str, Any]:
        self.tools.append(row)
        key = normalize_url(row.get("url", ""))
        if key:
            self._by_url.setdefault(key, row)
        name_key = _name_key(row.get("name", ""))
        if name_key:
            self._by_name.setdefault(name_key, row)
            self._name_buckets.setdefault(name_key[0], []).append(row)
        return row

    def find(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The existing row this data describes, or None.

        URL first, since it is an identity rather than a label. Only when
        that misses -- a tool listed under http on one source and https on
        another, or with no URL at all -- does the name fallback run.
        """
        url_key = normalize_url(data.get("url", ""))
        if url_key and url_key in self._by_url:
            return self._by_url[url_key]

        name = (data.get("name") or "").strip()
        name_key = _name_key(name)
        if not name_key:
            return None
        if name_key in self._by_name:
            return self._by_name[name_key]

        best, best_score = None, 0.0
        for candidate in self._name_buckets.get(name_key[0], []):
            score = name_similarity(name, candidate.get("name", ""))
            if score > best_score:
                best, best_score = candidate, score
        if best is not None and best_score > NAME_MATCH_THRESHOLD:
            # Two tools can share a near-identical name and be unrelated
            # ("Maltego" vs "Maltego CE"). Requiring that neither side
            # asserts a *different* URL keeps those apart.
            incoming_url = normalize_url(data.get("url", ""))
            existing_url = normalize_url(best.get("url", ""))
            if not incoming_url or not existing_url or incoming_url == existing_url:
                return best
        return None

    def merge_or_create_tool(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Fold one importer's record into the catalog. Returns the master row.

        Idempotent: importing the same record twice leaves one row, with the
        source recorded once.
        """
        incoming = normalize_tool(data)
        existing = self.find(incoming)
        if existing is None:
            return self._index(incoming)

        origin = incoming.get("source") or "unknown"

        for source in incoming["sources"]:
            if source not in existing["sources"]:
                existing["sources"].append(source)

        for field in _LIST_FIELDS:
            if field == "sources":
                continue
            known = {item.lower() for item in existing[field]}
            for item in incoming[field]:
                if item.lower() not in known:
                    known.add(item.lower())
                    existing[field].append(item)

        for field in _SCALAR_FIELDS:
            new_value = incoming.get(field)
            if new_value in (None, "", "unknown"):
                continue
            current = existing.get(field)
            # An empty slot is filled by whoever can fill it. A populated
            # slot is only replaced when the newcomer is strictly more
            # informative, so import order cannot change the outcome.
            if current in (None, "", "unknown"):
                existing[field] = new_value
                existing["provenance"][field] = origin
            elif field == "description" and len(str(new_value)) > len(str(current)):
                existing[field] = new_value
                existing["provenance"][field] = origin

        if not existing.get("url") and incoming.get("url"):
            existing["url"] = incoming["url"]
            key = normalize_url(existing["url"])
            if key:
                self._by_url.setdefault(key, existing)

        self.merges.append(f"{existing['name']} <- {origin}")
        return existing

    def relationships(self) -> List[Dict[str, Any]]:
        """`related_tools` name references resolved to id-to-id edges.

        Upstream states relationships as bare names ("VirusTotal"), so an edge
        can only be drawn once both ends are in the catalog. Names that never
        resolve are dropped here rather than stored as dangling references.
        """
        edges, seen = [], set()
        for tool in self.tools:
            for related in tool.get("related_tools") or []:
                match = self._by_name.get(_name_key(related))
                if match is None or match["id"] == tool["id"]:
                    continue
                pair = (tool["id"], match["id"])
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append({
                    "source_tool_id": tool["id"],
                    "target_tool_id": match["id"],
                    "relationship_type": "related_to",
                    "description": f"{tool['name']} lists {match['name']} as a related tool",
                })
        return edges

    def to_document(self) -> Dict[str, Any]:
        tools = sorted(self.tools, key=lambda t: (t.get("name") or "").lower())
        return {
            "version": CATALOG_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool_count": len(tools),
            "sources": sorted({s for t in tools for s in t.get("sources") or []}),
            "tools": tools,
            "tool_relationships": self.relationships(),
        }


def merge_or_create_tool(catalog: Catalog, data: Dict[str, Any]) -> Dict[str, Any]:
    """Module-level form of Catalog.merge_or_create_tool."""
    return catalog.merge_or_create_tool(data)


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------

def default_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "osint_catalog.json")


def load_document(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The catalog document on disk, or None when it has not been built.

    Never raises for a missing or corrupt file: the page that reads this has
    to render an actionable empty state rather than a traceback.
    """
    target = path or default_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) and "tools" in document else None


def load_catalog(path: Optional[str] = None) -> Catalog:
    document = load_document(path)
    return Catalog((document or {}).get("tools", []))


def save_catalog(catalog: Catalog, path: Optional[str] = None) -> str:
    target = path or default_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    document = catalog.to_document()
    stamp = document["generated_at"]
    for tool in document["tools"]:
        tool["imported_at"] = tool.get("imported_at") or stamp
    # Write-then-rename, so an interrupted import cannot leave the page
    # reading a half-written catalog.
    tmp = f"{target}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, target)
    return target


def summarize(document: Dict[str, Any]) -> Dict[str, Any]:
    """Counts the UI puts in its header row."""
    tools = document.get("tools", [])
    by_source: Dict[str, int] = {}
    by_access: Dict[str, int] = {}
    categories = set()
    for tool in tools:
        for source in tool.get("sources") or []:
            by_source[source] = by_source.get(source, 0) + 1
        access = tool.get("access_type") or "unknown"
        by_access[access] = by_access.get(access, 0) + 1
        categories.update(tool.get("categories") or [])
    return {
        "tools": len(tools),
        "sources": len(by_source),
        "categories": len(categories),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        "by_access": by_access,
        "multi_source": sum(1 for t in tools if len(t.get("sources") or []) > 1),
        "generated_at": document.get("generated_at"),
        "relationships": len(document.get("tool_relationships") or []),
    }
