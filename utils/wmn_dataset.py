"""
Local cache of the WhatsMyName site dataset -- the ~700 platform
definitions the footprint scanner checks a handle against.

The file is fetched from upstream on first use and cached under data/
rather than committed, for two reasons. The dataset is CC BY-SA 4.0
(c) Micah Hoffman: not vendoring it keeps ShareAlike'd content out of
this repo's git history entirely, so the only obligation left is
attribution, which the UI shows from the dataset's own `license` and
`authors` keys. And the list genuinely moves -- sites die, detection
strings get fixed upstream -- so a pinned copy would rot silently while
still looking authoritative.

Freshness uses the same hash-diff idea Blackbird uses: hash the cached
payload, hash the remote one, only rewrite the file when they differ.
That keeps a refresh cheap enough to offer as a button without making it
a background job nobody asked for.

FAST_SCAN_SITES is a hand-curated subset, and every name in it was
verified to resolve against the live dataset before being added -- the
names are not guessable. Twitter is listed as "X" (the "Twitter" entries
are archive-only mirrors), GitHub is "GitHub (User)", and tumblr is
lowercase. A typo here doesn't raise; it just silently scans fewer sites,
so resolve_fast_sites() reports what it couldn't find instead of quietly
dropping it. It is no longer the default scan -- see select_sites().

LinkedIn is absent from the upstream dataset, which is why EXTRA_SITES
exists: it's the one platform people most expect to see in a footprint
report, and leaving it out silently reads as "you have no LinkedIn"
rather than "this tool never looked".
"""
import hashlib
import json
from pathlib import Path

import requests

from applog import get_logger

_log = get_logger("wmn_dataset")

DATASET_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"

# The dataset's own category string for adult sites. Excluded by default:
# this app's job is helping someone find accounts they forgot they had,
# and blindly probing 39 adult sites with a real handle is a surprise
# nobody wants from a compliance tool.
NSFW_CATEGORY = "xx NSFW xx"

# ~50 high-signal platforms for the default scan. Chosen for "accounts a
# person plausibly made and forgot", weighted toward social, gaming and
# developer platforms. Every entry verified present in wmn-data.json.
FAST_SCAN_SITES = (
    # social
    "Reddit", "X", "Instagram", "Facebook", "Pinterest", "TikTok", "tumblr",
    "Snapchat", "Telegram", "Quora",
    # gaming
    "Twitch", "Steam", "Roblox", "Xbox Gamertag", "Playstation Network",
    "Chess.com", "lichess.org",
    # coding / tech
    "GitHub (User)", "GitHub (Gists)", "GitLab", "StackOverflow", "CodePen",
    "Replit", "Kaggle", "Docker Hub (User)", "npm", "pypi", "Keybase",
    # media / creative
    "YouTube Channel", "SoundCloud", "Spotify", "Bandcamp", "Last.fm",
    "DeviantArt", "ArtStation", "Behance", "Dribbble", "Flickr", "Imgur",
    "Vimeo",
    # writing / commerce / misc
    "Medium", "Substack", "Patreon", "Etsy", "eBay", "Venmo", "about.me",
    "Strava", "Duolingo", "Hacker News", "WordPress.com (Public)",
)


# Platforms worth checking that upstream doesn't define. Kept in the
# dataset's own schema so they flow through build_request/classify
# untouched rather than needing a parallel code path.
#
# LinkedIn answers automated requests with HTTP 999 (its own invented
# refusal code) or 403 -- it is not scrapeable and pretending otherwise
# would produce a confident wrong answer. So it declares no e_string,
# which caps it at POSSIBLE, and declares WAF protection, which routes a
# refusal to a manual-review row. uri_pretty points at a site-scoped
# search rather than the profile URL: the profile URL is exactly what
# won't load for a logged-out visitor, so the useful thing to hand
# someone doing manual review is the search that finds it.
EXTRA_SITES = (
    {
        "name": "LinkedIn",
        "cat": "business",
        "uri_check": "https://www.linkedin.com/in/{account}",
        "uri_pretty": "https://duckduckgo.com/?q=site%3Alinkedin.com%2Fin+%22{account}%22",
        "e_code": 200,
        "e_string": "",
        "m_code": 404,
        "m_string": "",
        "protection": ["WAF"],
    },
)


def _hash_payload(payload) -> str:
    """Stable hash of the dataset for change detection. sort_keys makes
    this independent of key ordering, so a cosmetic reserialization
    upstream doesn't read as a content change."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_cache(cache_path: str) -> dict | None:
    """Parsed dataset from disk, or None if it's absent or unreadable.
    A corrupt cache is treated the same as a missing one -- the caller
    re-downloads rather than crashing a scan the user just started."""
    path = Path(cache_path)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("Unreadable WhatsMyName cache at %s: %s", cache_path, exc)
        return None


def write_cache(cache_path: str, payload: dict) -> None:
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def fetch_remote(url: str = DATASET_URL, timeout: int = 30) -> dict:
    """Download and parse the upstream dataset. Raises on failure --
    callers decide whether that's fatal (no cache to fall back on) or
    recoverable (refresh failed, keep using what's on disk)."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def ensure_dataset(cache_path: str, url: str = DATASET_URL, refresh: bool = False,
                   timeout: int = 30) -> tuple[dict, str]:
    """Return (dataset, status) with the cache populated.

    status is one of "cached" (used what was on disk), "downloaded" (no
    usable cache existed), "updated" (refresh found upstream changes) or
    "unchanged" (refresh confirmed the cache is current). Refresh
    failures fall back to the cached copy and report "stale" rather than
    taking the scan down with them -- a slightly old site list is far
    more useful than an error page.
    """
    cached = read_cache(cache_path)

    if cached is None:
        payload = fetch_remote(url, timeout)
        write_cache(cache_path, payload)
        return payload, "downloaded"

    if not refresh:
        return cached, "cached"

    try:
        remote = fetch_remote(url, timeout)
    except Exception as exc:
        _log.warning("WhatsMyName refresh failed, using cached copy: %s", exc)
        return cached, "stale"

    if _hash_payload(remote) != _hash_payload(cached):
        write_cache(cache_path, remote)
        return remote, "updated"
    return cached, "unchanged"


def resolve_fast_sites(dataset: dict, names=FAST_SCAN_SITES) -> tuple[list, list]:
    """Split the curated fast list into (found_sites, missing_names).

    Returning the misses instead of swallowing them is the point: if
    upstream renames a platform, the fast scan silently shrinks, and the
    only way to notice is for something to say so.
    """
    by_name = {site["name"]: site for site in dataset.get("sites", [])}
    found = [by_name[name] for name in names if name in by_name]
    missing = [name for name in names if name not in by_name]
    if missing:
        _log.warning("Fast-scan sites not present in dataset: %s", ", ".join(missing))
    return found, missing


def select_sites(dataset: dict, deep: bool = True, include_nsfw: bool = False,
                 categories: list | None = None) -> list:
    """The site list a scan should actually run against.

    Deep (the full list) is the default. The curated fast subset stays
    available for anyone who wants a quick look, but a footprint tool
    whose default answer covers 7% of the platforms it knows about is
    reporting a floor as if it were a finding.

    NSFW stays excluded unless explicitly asked for: probing 39 adult
    sites with someone's real handle is not a surprise a compliance tool
    should spring on them, whatever the scan depth.
    """
    if not deep:
        sites, _ = resolve_fast_sites(dataset)
        return sites + list(EXTRA_SITES)

    sites = list(dataset.get("sites", []))
    if not include_nsfw:
        sites = [s for s in sites if s.get("cat") != NSFW_CATEGORY]
    if categories:
        wanted = set(categories)
        sites = [s for s in sites if s.get("cat") in wanted]
        return sites
    return sites + list(EXTRA_SITES)


def available_categories(dataset: dict, include_nsfw: bool = False) -> list:
    cats = {s.get("cat") for s in dataset.get("sites", []) if s.get("cat")}
    if not include_nsfw:
        cats.discard(NSFW_CATEGORY)
    return sorted(cats)


def attribution(dataset: dict) -> str:
    """One-line credit line built from the dataset's own metadata --
    required by CC BY-SA 4.0 and shown in the UI."""
    authors = dataset.get("authors") or []
    author_note = f" Contributors: {', '.join(authors)}." if authors else ""
    return (
        "Site data from the WhatsMyName project by WebBreacher (Micah Hoffman), "
        "licensed CC BY-SA 4.0." + author_note
    )
