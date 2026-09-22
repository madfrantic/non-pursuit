"""
The four OSINT tools imported straight from their GitHub repositories.

Repo metadata answers the questions the catalog cares about (licence, homepage,
last push, description) authoritatively and in one call. The README supplies a
human summary the API's one-line description usually lacks; only the first real
paragraph is taken, because READMEs open with badge walls and the rest of the
file is installation instructions.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

SOURCE = "GitHub"

# Categories are asserted here rather than derived: GitHub topics are noisy and
# frequently absent, and these four tools have well-understood roles. The
# dedupe pass will union these with whatever the other sources say.
REPOS = [
    {
        "owner": "owasp-amass", "repo": "amass",
        "categories": ["Network Infrastructure OSINT", "Domain Name OSINT"],
        "input_type": "Domain",
        "output_type": "Subdomains, IPs, ASNs",
        "opsec": "active",
        "opsec_note": "Performs DNS enumeration and can touch target infrastructure "
                      "directly unless run in passive mode.",
    },
    {
        "owner": "smicallef", "repo": "spiderfoot",
        "categories": ["Foundational OSINT Tools", "Network Infrastructure OSINT"],
        "input_type": "Domain, IP, Email, Username",
        "output_type": "Correlated entity report",
        "opsec": "active",
        "opsec_note": "Module-dependent; many modules query the target directly.",
    },
    {
        "owner": "qeeqbox", "repo": "social-analyzer",
        "categories": ["Social Media OSINT", "Username OSINT"],
        "input_type": "Username",
        "output_type": "Matched profiles with confidence ratings",
        "opsec": "active",
        "opsec_note": "Requests profile pages on each platform being checked.",
    },
    {
        "owner": "mxrch", "repo": "GHunt",
        "categories": ["Email Address OSINT", "People OSINT"],
        "input_type": "Email, Google ID",
        "output_type": "Google account profile data",
        "opsec": "active",
        "opsec_note": "Requires authenticated Google cookies; queries Google's "
                      "endpoints as a logged-in user.",
    },
]

# Badges, HTML blocks, headings and blockquotes -- everything a README opens
# with before it says what the tool is.
_SKIP_LINE = re.compile(
    r"^\s*(?:#|>|<|!\[|\[!\[|\||-{3,}|={3,}|\*{3,}|```)"
)
_INLINE_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")


def readme_summary(text: str, limit: int = 400) -> str:
    """The first prose paragraph of a README, stripped of markup.

    Returns "" when the README is all badges and headings, which is common
    enough that the caller must treat an empty result as normal.
    """
    if not text:
        return ""
    paragraph: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if paragraph:
                break
            continue
        if _SKIP_LINE.match(line):
            if paragraph:
                break
            continue
        cleaned = _INLINE_IMAGE.sub("", line)
        cleaned = _MD_LINK.sub(r"\1", cleaned)
        cleaned = _HTML_TAG.sub("", cleaned)
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned).strip()
        # A line that was nothing but badges collapses to punctuation.
        if not cleaned or not re.search(r"[A-Za-z]{3}", cleaned):
            if paragraph:
                break
            continue
        paragraph.append(cleaned)

    summary = " ".join(paragraph).strip()
    summary = re.sub(r"\s+", " ", summary)
    if len(summary) > limit:
        cut = summary[:limit].rsplit(" ", 1)[0]
        summary = f"{cut}…"
    return summary


def build_row(spec: Dict[str, Any], meta: Dict[str, Any],
              readme: str = "") -> Dict[str, Any]:
    """Compose one catalog row from repo metadata plus README prose."""
    owner, repo = spec["owner"], spec["repo"]
    repo_url = meta.get("html_url") or f"https://github.com/{owner}/{repo}"
    homepage = (meta.get("homepage") or "").strip()

    api_description = (meta.get("description") or "").strip()
    summary = readme_summary(readme)
    # Prefer whichever actually says something; the API blurb is often a
    # single clause, the README paragraph a real sentence.
    description = max((api_description, summary), key=len) or api_description

    licence: Optional[str] = None
    if isinstance(meta.get("license"), dict):
        licence = meta["license"].get("spdx_id") or meta["license"].get("name")
        if licence in ("NOASSERTION", "null"):
            licence = meta["license"].get("name")

    archived = bool(meta.get("archived"))

    return {
        "name": meta.get("name") or repo,
        "description": description,
        # The project's own site is the better front door when it has one;
        # the repo is always recorded separately in github_repo.
        "url": homepage or repo_url,
        "source": SOURCE,
        "source_id": f"{owner}/{repo}",
        "categories": list(spec.get("categories") or []),
        "access_type": "free",
        "status": "deprecated" if archived else "live",
        "deprecated": archived,
        "local_install": True,
        "registration_required": False,
        "github_repo": repo_url,
        "license": licence,
        "documentation_url": homepage or repo_url,
        "last_updated": meta.get("pushed_at") or meta.get("updated_at"),
        "input_type": spec.get("input_type"),
        "output_type": spec.get("output_type"),
        "opsec": spec.get("opsec"),
        "opsec_note": spec.get("opsec_note"),
        "best_for": spec.get("best_for") or api_description or None,
    }


def fetch(client) -> List[Dict[str, Any]]:
    """Repo metadata + README summary for each configured repository.

    A single unreachable repo yields a row from what is known statically
    rather than aborting the source; the runner's error handling is for a
    dead upstream, not for one missing project.
    """
    rows: List[Dict[str, Any]] = []
    for spec in REPOS:
        owner, repo = spec["owner"], spec["repo"]
        try:
            meta = client.github_json(f"/repos/{owner}/{repo}")
        except Exception:  # noqa: BLE001 - degraded row beats no row
            meta = {}
        readme = client.github_readme(owner, repo) if meta else ""
        rows.append(build_row(spec, meta if isinstance(meta, dict) else {}, readme))
    return rows
