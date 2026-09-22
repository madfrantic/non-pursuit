"""GitHub public event stream: surface unmasked personal emails from commit headers."""
import asyncio
import aiohttp
from typing import Any, Dict, List

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY

_log = get_logger("osint_github")

UA = {"User-Agent": "Non-Pursuit/1.0 (privacy self-audit; contact: user@example.com)"}
GITHUB_API = "https://api.github.com"


async def _fetch_public_events(username: str) -> List[Dict[str, Any]]:
    """Fetch public events for a GitHub username. Rate-limited to 60/hr anon."""
    if not username:
        return []
    url = f"{GITHUB_API}/users/{username}/events/public"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=UA, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    return await r.json() or []
                elif r.status == 404:
                    return []  # user doesn't exist
                else:
                    _log.warning("GitHub API returned %d for %s", r.status, username)
                    return []
    except asyncio.TimeoutError:
        _log.info("GitHub API timeout for %s", username)
    except Exception as exc:
        _log.info("GitHub API error for %s: %s", username, exc)
    return []


def _extract_emails_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Parse commit payloads for unmasked personal email addresses in headers."""
    emails = []
    seen = set()

    for event in events:
        if event.get("type") != "PushEvent":
            continue
        payload = event.get("payload", {})
        commits = payload.get("commits", [])
        for commit in commits:
            author = commit.get("author", {})
            email = author.get("email", "").strip()
            if email and email not in seen and not email.endswith("@users.noreply.github.com"):
                seen.add(email)
                emails.append({
                    "email": email,
                    "name": author.get("name", ""),
                    "repository": event.get("repo", {}).get("name", ""),
                    "timestamp": event.get("created_at", ""),
                })
    return emails


async def scan_github(handle: str) -> Dict[str, Any]:
    """
    Scan public GitHub events for personal email exposure in commit headers.
    """
    if not handle:
        return {
            "status": STATUS_EMPTY,
            "module": "github",
            "records": [],
            "error": "No handle provided",
        }

    events = await _fetch_public_events(handle)
    if not events:
        return {
            "status": STATUS_EMPTY,
            "module": "github",
            "records": [],
            "note": f"No public events found for {handle}",
        }

    emails = _extract_emails_from_events(events)
    return {
        "status": STATUS_SUCCESS if emails else STATUS_EMPTY,
        "module": "github",
        "records": emails,
        "count": len(emails),
    }
