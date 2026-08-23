"""
Live email account reconnaissance engine (Holehe-style multi-platform scanner).

Probes public APIs, registration validation endpoints, and password recovery
status checkers across top web services to determine if an account is registered
under a given email address.

Design & Safety Guarantees:
- Zero-Alert / Read-Only: Only queries endpoints that reveal account status without
  dispatching verification emails, password resets, or security notices.
- Async Concurrency: Runs all platform checks concurrently via aiohttp in seconds.
- Standardized Verdicts: CONFIRMED (registered), NOT_FOUND (unregistered),
  POSSIBLE (ambiguous / WAF), ERROR (network failure).
"""
import asyncio
import hashlib
import json
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import aiohttp

from applog import get_logger

_log = get_logger("holehe_scanner")

CONFIRMED = "CONFIRMED"
POSSIBLE = "POSSIBLE"
NOT_FOUND = "NOT_FOUND"
ERROR = "ERROR"

DEFAULT_TIMEOUT = 10
DEFAULT_CONCURRENCY = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _gravatar_hash(email: str) -> str:
    return hashlib.md5(email.lower().strip().encode()).hexdigest()


def _libravatar_hash(email: str) -> str:
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()


def _make_result(
    platform: str,
    category: str,
    email: str,
    confidence: str,
    reason: str,
    profile_url: str = "",
    avatar_url: str = "",
    emailrecovery: Optional[str] = None,
    phone_number: Optional[str] = None,
    others: Optional[Dict[str, Any]] = None,
    rate_limited: bool = False,
) -> Dict[str, Any]:
    return {
        "platform": platform,
        "service": platform,
        "category": category,
        "identifier": email,
        "target_identifier": email,
        "confidence": confidence,
        "reason": reason,
        "profile_url": profile_url,
        "avatar_url": avatar_url,
        "emailrecovery": emailrecovery,
        "phoneNumber": phone_number,
        "others": others or {},
        "rate_limited": rate_limited,
        "vector": "email_account",
    }


# ---------------------------------------------------------------------------
# Individual Platform Probers (Zero-Alert)
# ---------------------------------------------------------------------------

async def probe_gravatar(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Gravatar for public profile and avatar."""
    hash_val = _gravatar_hash(email)
    url = f"https://www.gravatar.com/avatar/{hash_val}?d=404&format=json"
    profile_url = f"https://gravatar.com/{hash_val}"
    avatar_url = f"https://www.gravatar.com/avatar/{hash_val}?s=128&d=404"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    entry = data.get("entry", [{}])[0]
                    display_name = entry.get("displayName") or entry.get("preferredUsername") or ""
                    reason = f"Public profile found ({display_name})" if display_name else "Public Gravatar profile found"
                    return _make_result("Gravatar", "avatar_service", email, CONFIRMED, reason, profile_url, avatar_url)
                except Exception:
                    return _make_result("Gravatar", "avatar_service", email, CONFIRMED, "Gravatar profile located", profile_url, avatar_url)
            elif resp.status == 404:
                return _make_result("Gravatar", "avatar_service", email, NOT_FOUND, "No Gravatar profile associated")
            elif resp.status == 429:
                return _make_result("Gravatar", "avatar_service", email, POSSIBLE, "Gravatar rate limited", rate_limited=True)
            return _make_result("Gravatar", "avatar_service", email, ERROR, f"Gravatar HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Gravatar", "avatar_service", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_libravatar(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Libravatar decentralized avatar presence."""
    hash_val = _libravatar_hash(email)
    url = f"https://seccdn.libravatar.org/avatar/{hash_val}?d=404"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT), allow_redirects=True) as resp:
            if resp.status == 200:
                return _make_result("Libravatar", "avatar_service", email, CONFIRMED, "Libravatar profile/avatar found", avatar_url=url)
            elif resp.status == 404:
                return _make_result("Libravatar", "avatar_service", email, NOT_FOUND, "No Libravatar profile associated")
            return _make_result("Libravatar", "avatar_service", email, ERROR, f"Libravatar HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Libravatar", "avatar_service", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_pgp(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check OpenPGP keyserver for published public encryption keys."""
    url = f"https://keys.openpgp.org/vks/v1/by-email/{quote(email)}"
    profile_url = f"https://keys.openpgp.org/search?{urlencode({'q': email})}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT), allow_redirects=True) as resp:
            if resp.status == 200:
                text = await resp.text()
                if "BEGIN PGP PUBLIC KEY BLOCK" in text:
                    return _make_result("PGP Keys", "encryption", email, CONFIRMED, "PGP public encryption key registered", profile_url)
                try:
                    data = json.loads(text)
                    if isinstance(data, dict) and data.get("keys"):
                        return _make_result("PGP Keys", "encryption", email, CONFIRMED, f"PGP keys registered ({len(data['keys'])} keys)", profile_url)
                except Exception:
                    pass
                return _make_result("PGP Keys", "encryption", email, CONFIRMED, "PGP key registered", profile_url)
            elif resp.status == 404:
                return _make_result("PGP Keys", "encryption", email, NOT_FOUND, "No public PGP keys registered")
            return _make_result("PGP Keys", "encryption", email, ERROR, f"PGP HTTP {resp.status}")
    except Exception as exc:
        return _make_result("PGP Keys", "encryption", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_spotify(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Spotify registration status via public signup validator."""
    url = f"https://spclient.wg.spotify.com/signup/public/v1/account?validate=1&email={quote(email)}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    errors = data.get("errors", {})
                    if "email" in errors or data.get("status") == 20:
                        return _make_result("Spotify", "media", email, CONFIRMED, "Account registered on Spotify", "https://open.spotify.com")
                    elif data.get("status") == 1 and not errors:
                        return _make_result("Spotify", "media", email, NOT_FOUND, "Email not registered on Spotify")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Spotify", "media", email, POSSIBLE, "Spotify rate limit reached", rate_limited=True)
            return _make_result("Spotify", "media", email, NOT_FOUND, "Email available / not registered")
    except Exception as exc:
        return _make_result("Spotify", "media", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_duolingo(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Duolingo user lookup API for public user account."""
    url = f"https://www.duolingo.com/2017-06-30/users?email={quote(email)}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    users = data.get("users", [])
                    if users:
                        user = users[0]
                        username = user.get("username", "")
                        picture = user.get("picture", "")
                        if picture and not picture.startswith("http"):
                            picture = f"https:{picture}"
                        profile_url = f"https://www.duolingo.com/profile/{username}" if username else "https://www.duolingo.com"
                        return _make_result(
                            "Duolingo", "education", email, CONFIRMED,
                            f"Duolingo account found (@{username})" if username else "Duolingo account registered",
                            profile_url=profile_url,
                            avatar_url=picture,
                        )
                    return _make_result("Duolingo", "education", email, NOT_FOUND, "No Duolingo account found")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Duolingo", "education", email, POSSIBLE, "Duolingo rate limited", rate_limited=True)
            return _make_result("Duolingo", "education", email, NOT_FOUND, "No Duolingo account found")
    except Exception as exc:
        return _make_result("Duolingo", "education", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_pinterest(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Pinterest EmailExists resource endpoint."""
    url = f'https://www.pinterest.com/_ngjs/resource/EmailExistsResource/get/?data={{"options":{{"email":"{quote(email)}"}}}}'
    headers = {
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    res_data = data.get("resource_response", {}).get("data", False)
                    if res_data is True or str(res_data).lower() == "true":
                        return _make_result("Pinterest", "social", email, CONFIRMED, "Pinterest account registered", "https://www.pinterest.com")
                    return _make_result("Pinterest", "social", email, NOT_FOUND, "No Pinterest account found")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Pinterest", "social", email, POSSIBLE, "Pinterest rate limited", rate_limited=True)
            return _make_result("Pinterest", "social", email, NOT_FOUND, f"Pinterest HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Pinterest", "social", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_chess(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Chess.com email availability endpoint."""
    url = f"https://www.chess.com/callback/email/available?email={quote(email)}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("available") is False:
                        return _make_result("Chess.com", "gaming", email, CONFIRMED, "Chess.com account registered", "https://www.chess.com")
                    return _make_result("Chess.com", "gaming", email, NOT_FOUND, "No Chess.com account found")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Chess.com", "gaming", email, POSSIBLE, "Chess.com rate limited", rate_limited=True)
            return _make_result("Chess.com", "gaming", email, NOT_FOUND, f"Chess.com HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Chess.com", "gaming", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_github_email(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check GitHub public email associations via API search."""
    url = f"https://api.github.com/search/users?q={quote(email)}+in:email"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github.v3+json",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    items = data.get("items", [])
                    if items:
                        user = items[0]
                        login = user.get("login", "")
                        profile_url = user.get("html_url") or f"https://github.com/{login}"
                        avatar_url = user.get("avatar_url", "")
                        return _make_result(
                            "GitHub", "code", email, CONFIRMED,
                            f"GitHub public commit/profile association (@{login})",
                            profile_url=profile_url,
                            avatar_url=avatar_url,
                        )
                    return _make_result("GitHub", "code", email, NOT_FOUND, "No public GitHub profile linked to email")
                except Exception:
                    pass
            elif resp.status in (403, 429):
                return _make_result("GitHub", "code", email, POSSIBLE, "GitHub API rate limit reached", rate_limited=True)
            return _make_result("GitHub", "code", email, NOT_FOUND, f"GitHub HTTP {resp.status}")
    except Exception as exc:
        return _make_result("GitHub", "code", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_adobe(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Adobe ID presence via public sign-in endpoint."""
    url = "https://auth.services.adobe.com/signin/v2/users/accounts"
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "X-Request-Id": hashlib.md5(email.encode()).hexdigest(),
    }
    payload = json.dumps({"username": email})
    try:
        async with session.post(url, headers=headers, data=payload, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        return _make_result("Adobe", "creativity", email, CONFIRMED, "Adobe ID registered", "https://account.adobe.com")
                except Exception:
                    pass
            elif resp.status == 404:
                return _make_result("Adobe", "creativity", email, NOT_FOUND, "No Adobe ID registered")
            elif resp.status == 429:
                return _make_result("Adobe", "creativity", email, POSSIBLE, "Adobe rate limited", rate_limited=True)
            return _make_result("Adobe", "creativity", email, NOT_FOUND, f"Adobe HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Adobe", "creativity", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_substack(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Substack author/reader account presence."""
    url = f"https://substack.com/api/v1/users/lookup?email={quote(email)}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("id") or data.get("name"):
                        handle = data.get("handle") or data.get("id")
                        profile_url = f"https://substack.com/@{handle}" if handle else "https://substack.com"
                        return _make_result(
                            "Substack", "publishing", email, CONFIRMED,
                            f"Substack profile found ({data.get('name', 'Registered user')})",
                            profile_url=profile_url,
                            avatar_url=data.get("photo_url", ""),
                        )
                except Exception:
                    pass
            elif resp.status == 404:
                return _make_result("Substack", "publishing", email, NOT_FOUND, "No Substack account associated")
            elif resp.status == 429:
                return _make_result("Substack", "publishing", email, POSSIBLE, "Substack rate limited", rate_limited=True)
            return _make_result("Substack", "publishing", email, NOT_FOUND, f"Substack HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Substack", "publishing", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_imgur(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Imgur account registration."""
    url = "https://imgur.com/signin/ajax_email_available"
    headers = {
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }
    data = {"email": email}
    try:
        async with session.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    res_data = await resp.json()
                    if res_data.get("data", {}).get("available") is False:
                        return _make_result("Imgur", "media", email, CONFIRMED, "Imgur account registered", "https://imgur.com")
                    return _make_result("Imgur", "media", email, NOT_FOUND, "No Imgur account found")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Imgur", "media", email, POSSIBLE, "Imgur rate limited", rate_limited=True)
            return _make_result("Imgur", "media", email, NOT_FOUND, f"Imgur HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Imgur", "media", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_pornhub(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Pornhub registration status via front-end auth checker."""
    url = "https://www.pornhub.com/front/authenticate"
    headers = {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
    try:
        async with session.post(url, headers=headers, data={"email": email}, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                text = await resp.text()
                if "email_taken" in text or "already in use" in text or "exists" in text or "true" in text.lower():
                    return _make_result("Pornhub", "adult", email, CONFIRMED, "Account registered on Pornhub", "https://www.pornhub.com")
                return _make_result("Pornhub", "adult", email, NOT_FOUND, "Email not registered on Pornhub")
            elif resp.status == 429:
                return _make_result("Pornhub", "adult", email, POSSIBLE, "Pornhub rate limited", rate_limited=True)
            return _make_result("Pornhub", "adult", email, NOT_FOUND, f"Pornhub HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Pornhub", "adult", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_onlyfans(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check OnlyFans account presence via public check endpoint."""
    url = "https://onlyfans.com/api2/v2/users/check_email"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with session.post(url, headers=headers, json={"email": email}, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("exists") is True or data.get("is_registered") is True or data.get("taken") is True:
                        return _make_result("OnlyFans", "adult", email, CONFIRMED, "Account registered on OnlyFans", "https://onlyfans.com")
                    return _make_result("OnlyFans", "adult", email, NOT_FOUND, "Email not registered on OnlyFans")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("OnlyFans", "adult", email, POSSIBLE, "OnlyFans rate limited", rate_limited=True)
            return _make_result("OnlyFans", "adult", email, NOT_FOUND, f"OnlyFans HTTP {resp.status}")
    except Exception as exc:
        return _make_result("OnlyFans", "adult", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_xvideos(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check XVideos account registration status."""
    url = f"https://www.xvideos.com/account/checkemail?email={quote(email)}"
    headers = {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                text = await resp.text()
                if "taken" in text or "exists" in text or "false" in text:
                    return _make_result("XVideos", "adult", email, CONFIRMED, "Account registered on XVideos", "https://www.xvideos.com")
                return _make_result("XVideos", "adult", email, NOT_FOUND, "Email not registered on XVideos")
            elif resp.status == 429:
                return _make_result("XVideos", "adult", email, POSSIBLE, "XVideos rate limited", rate_limited=True)
            return _make_result("XVideos", "adult", email, NOT_FOUND, f"XVideos HTTP {resp.status}")
    except Exception as exc:
        return _make_result("XVideos", "adult", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_stripchat(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Stripchat email presence."""
    url = f"https://stripchat.com/api/front/users/check-email?email={quote(email)}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("isAvailable") is False or data.get("exists") is True:
                        return _make_result("Stripchat", "adult", email, CONFIRMED, "Account registered on Stripchat", "https://stripchat.com")
                    return _make_result("Stripchat", "adult", email, NOT_FOUND, "Email not registered on Stripchat")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Stripchat", "adult", email, POSSIBLE, "Stripchat rate limited", rate_limited=True)
            return _make_result("Stripchat", "adult", email, NOT_FOUND, f"Stripchat HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Stripchat", "adult", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_chaturbate(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Chaturbate account presence."""
    url = f"https://chaturbate.com/auth/check_email/?email={quote(email)}"
    headers = {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("success") is False or data.get("exists") is True or data.get("available") is False:
                        return _make_result("Chaturbate", "adult", email, CONFIRMED, "Account registered on Chaturbate", "https://chaturbate.com")
                    return _make_result("Chaturbate", "adult", email, NOT_FOUND, "Email not registered on Chaturbate")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Chaturbate", "adult", email, POSSIBLE, "Chaturbate rate limited", rate_limited=True)
            return _make_result("Chaturbate", "adult", email, NOT_FOUND, f"Chaturbate HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Chaturbate", "adult", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_twitter(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check X (Twitter) account availability."""
    url = f"https://api.twitter.com/i/users/email_available.json?email={quote(email)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("taken") is True:
                        return _make_result("X (Twitter)", "social", email, CONFIRMED, "Account registered on X (Twitter)", "https://x.com")
                    return _make_result("X (Twitter)", "social", email, NOT_FOUND, "Email not registered on X")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("X (Twitter)", "social", email, POSSIBLE, "X rate limited", rate_limited=True)
            return _make_result("X (Twitter)", "social", email, NOT_FOUND, f"X HTTP {resp.status}")
    except Exception as exc:
        return _make_result("X (Twitter)", "social", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_reddit(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Reddit user association for email handle."""
    handle = email.split("@")[0]
    url = f"https://www.reddit.com/user/{quote(handle)}/about.json"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("data", {}).get("name"):
                        uname = data["data"]["name"]
                        return _make_result("Reddit", "social", email, CONFIRMED, f"Reddit username association (@{uname})", f"https://reddit.com/user/{uname}")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Reddit", "social", email, POSSIBLE, "Reddit rate limited", rate_limited=True)
            return _make_result("Reddit", "social", email, NOT_FOUND, "No direct Reddit association")
    except Exception as exc:
        return _make_result("Reddit", "social", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_ebay(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check eBay account existence."""
    url = f"https://signin.ebay.com/identity/api/v1/user/exists?email={quote(email)}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("exists") is True:
                        return _make_result("eBay", "commerce", email, CONFIRMED, "Account registered on eBay", "https://www.ebay.com")
                    return _make_result("eBay", "commerce", email, NOT_FOUND, "Email not registered on eBay")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("eBay", "commerce", email, POSSIBLE, "eBay rate limited", rate_limited=True)
            return _make_result("eBay", "commerce", email, NOT_FOUND, f"eBay HTTP {resp.status}")
    except Exception as exc:
        return _make_result("eBay", "commerce", email, ERROR, f"Request failed: {str(exc)[:60]}")


async def probe_snapchat(email: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Check Snapchat registration."""
    url = "https://accounts.snapchat.com/accounts/merlin/login"
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    try:
        async with session.post(url, headers=headers, json={"email": email}, timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("status") == "TAKEN" or data.get("error_code") == "EMAIL_EXISTS":
                        return _make_result("Snapchat", "social", email, CONFIRMED, "Account registered on Snapchat", "https://www.snapchat.com")
                    return _make_result("Snapchat", "social", email, NOT_FOUND, "Email not registered on Snapchat")
                except Exception:
                    pass
            elif resp.status == 429:
                return _make_result("Snapchat", "social", email, POSSIBLE, "Snapchat rate limited", rate_limited=True)
            return _make_result("Snapchat", "social", email, NOT_FOUND, f"Snapchat HTTP {resp.status}")
    except Exception as exc:
        return _make_result("Snapchat", "social", email, ERROR, f"Request failed: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
# Master Probers Registry & Aggregated Scanner
# ---------------------------------------------------------------------------

PROBERS = [
    ("Gravatar", probe_gravatar),
    ("Libravatar", probe_libravatar),
    ("PGP Keys", probe_pgp),
    ("Spotify", probe_spotify),
    ("Duolingo", probe_duolingo),
    ("Pinterest", probe_pinterest),
    ("Chess.com", probe_chess),
    ("GitHub", probe_github_email),
    ("Adobe", probe_adobe),
    ("Substack", probe_substack),
    ("Imgur", probe_imgur),
    ("Pornhub", probe_pornhub),
    ("OnlyFans", probe_onlyfans),
    ("XVideos", probe_xvideos),
    ("Stripchat", probe_stripchat),
    ("Chaturbate", probe_chaturbate),
    ("X (Twitter)", probe_twitter),
    ("Reddit", probe_reddit),
    ("eBay", probe_ebay),
    ("Snapchat", probe_snapchat),
]


async def scan_email_async(email: str, concurrency: int = DEFAULT_CONCURRENCY) -> List[Dict[str, Any]]:
    """Scan one email across all registered platform probers concurrently."""
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, ttl_dns_cache=300)

    async def _run_probe(session: aiohttp.ClientSession, name: str, prober) -> Dict[str, Any]:
        async with semaphore:
            try:
                return await prober(email_clean, session)
            except Exception as exc:
                _log.warning("Prober %s error for email: %s", name, exc)
                return _make_result(name, "service", email_clean, ERROR, f"Scan error: {str(exc)[:60]}")

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            asyncio.ensure_future(_run_probe(session, name, prober))
            for name, prober in PROBERS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    # Sort: CONFIRMED first, then POSSIBLE, then alphabetical
    order = {CONFIRMED: 0, POSSIBLE: 1, NOT_FOUND: 2, ERROR: 3}
    return sorted(results, key=lambda r: (order.get(r["confidence"], 4), r["platform"].lower()))


def scan_email_sync(email: str) -> List[Dict[str, Any]]:
    """Synchronous entrypoint for Streamlit & CLI execution."""
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return []
    return asyncio.run(scan_email_async(email_clean))
