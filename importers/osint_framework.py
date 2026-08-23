"""
OSINT Framework (github.com/lockfale/osint-framework).

The brief anticipated an arf.json per folder, discovered by walking the repo
tree. The repo actually keeps a single file -- public/arf.json, ~1.1 MB -- and
carries the folder structure *inside* it as nested `children`. So this reads
one file instead of dozens, and derives categories from the node path rather
than from a directory path. One request, no tree walk, no token needed.

Leaf nodes are the tools; interior nodes are categories. Roughly 1100 of the
1168 leaves carry the full metadata block, and the rest have only name/type/
url -- those still import, just sparsely, and other sources fill them in.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

ARF_URL = ("https://raw.githubusercontent.com/lockfale/osint-framework/"
           "master/public/arf.json")

SOURCE = "OSINT Framework"

# The root node is the framework itself, not a category anyone would filter by.
_ROOT_NAMES = {"osint framework", "osint"}

# 258 of the 1168 names end in the framework's own notation: (T) links to a
# tool, (M) requires a manual URL edit, (R) requires registration, (D) is a
# Google dork. That is a property of the framework's presentation, not part
# of the tool's name -- and leaving it on means "GHunt (T)" never name-matches
# the "GHunt" every other source lists. The letter is kept as a note so the
# annotation is recorded rather than discarded.
_NOTATION = re.compile(r"\s*\(([TMRD])\)\s*$")
_NOTATION_MEANING = {
    "T": "links directly to the tool",
    "M": "requires a manual URL edit before use",
    "R": "requires registration",
    "D": "is a Google dork rather than a hosted tool",
}


def split_notation(name: str) -> Tuple[str, Optional[str]]:
    """('GHunt (T)') -> ('GHunt', 'T')."""
    match = _NOTATION.search(name or "")
    if not match:
        return (name or "").strip(), None
    return _NOTATION.sub("", name).strip(), match.group(1)


def _walk(node: Any, path: Tuple[str, ...]) -> Iterator[Tuple[Dict[str, Any], Tuple[str, ...]]]:
    """Yield (leaf, category_path) for every tool in the tree."""
    if isinstance(node, list):
        for child in node:
            yield from _walk(child, path)
        return
    if not isinstance(node, dict):
        return
    children = node.get("children")
    if isinstance(children, list):
        name = str(node.get("name") or "").strip()
        deeper = path if name.lower() in _ROOT_NAMES or not name else path + (name,)
        for child in children:
            yield from _walk(child, deeper)
        return
    if node.get("name"):
        yield node, path


def parse(document: Any) -> List[Dict[str, Any]]:
    """Flatten an arf.json document into catalog rows."""
    rows: List[Dict[str, Any]] = []
    for leaf, categories in _walk(document, ()):
        url = str(leaf.get("url") or "").strip()
        raw_name = str(leaf.get("name") or "").strip()
        name, notation = split_notation(raw_name)
        if not name:
            continue

        # `googleDork` entries are search queries, not addressable tools; they
        # have no URL to dedupe on and would each land as an orphan row.
        if not url and leaf.get("googleDork"):
            continue

        github_repo = url if "github.com" in url.lower() else None

        opsec_note = leaf.get("opsecNote")
        if notation and notation != "T":
            annotation = f"OSINT Framework notes this entry {_NOTATION_MEANING[notation]}."
            opsec_note = f"{opsec_note} {annotation}".strip() if opsec_note else annotation

        rows.append({
            "name": name,
            "description": leaf.get("description"),
            "url": url,
            "source": SOURCE,
            "source_id": "/".join(categories + (raw_name,)),
            "categories": list(categories),
            "access_type": leaf.get("pricing"),
            "status": leaf.get("status"),
            "best_for": leaf.get("bestFor"),
            "input_type": leaf.get("input"),
            "output_type": leaf.get("output"),
            "opsec": leaf.get("opsec"),
            "opsec_note": opsec_note,
            "local_install": leaf.get("localInstall"),
            "registration_required": leaf.get("registration"),
            "api_available": leaf.get("api"),
            "invitation_only": leaf.get("invitationOnly"),
            "deprecated": leaf.get("deprecated"),
            "github_repo": github_repo,
            "documentation_url": leaf.get("editUrl"),
        })
    return rows


def fetch(client) -> List[Dict[str, Any]]:
    return parse(client.get_json(ARF_URL))
