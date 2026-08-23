"""
The one HTTP client every importer shares.

Politeness is a property of the client rather than of each importer, so that
a new source cannot forget it: `HttpClient.get` sleeps to hold a per-host
request rate, and `github_json` additionally understands GitHub's rate-limit
headers. Nothing here retries forever -- a source that will not answer is a
source the runner should skip, not one that should hold the pipeline open.
"""
from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests

USER_AGENT = "non-pursuit-osint-catalog/1.0 (+local research tool)"

DEFAULT_DELAY = 1.0     # seconds between requests to the same host
DEFAULT_TIMEOUT = 30
GITHUB_API = "https://api.github.com"


class FetchError(RuntimeError):
    """An upstream could not be read. Caught per-source by the runner."""


class HttpClient:
    """A requests.Session with a per-host rate limit."""

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: int = DEFAULT_TIMEOUT,
                 github_token: Optional[str] = None):
        self.delay = delay
        self.timeout = timeout
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN") or ""
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_hit: Dict[str, float] = {}
        self.request_count = 0

    # -- politeness --------------------------------------------------------

    def _wait_for(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        last = self._last_hit.get(host)
        if last is not None:
            remaining = self.delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_hit[host] = time.monotonic()

    # -- plain fetches -----------------------------------------------------

    def get(self, url: str, **kwargs) -> requests.Response:
        self._wait_for(url)
        self.request_count += 1
        try:
            return self.session.get(url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise FetchError(f"GET {url} failed: {exc}") from exc

    def get_text(self, url: str) -> str:
        response = self.get(url)
        if response.status_code != 200:
            raise FetchError(f"GET {url} returned HTTP {response.status_code}")
        return response.text

    def get_json(self, url: str) -> Any:
        response = self.get(url)
        if response.status_code != 200:
            raise FetchError(f"GET {url} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"GET {url} returned unparseable JSON: {exc}") from exc

    # -- GitHub ------------------------------------------------------------

    def _github_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def github_json(self, path: str, *, max_retries: int = 2) -> Any:
        """GET an api.github.com path, honouring the documented rate limits.

        Unauthenticated callers get 60 requests an hour, which is enough for
        this pipeline but leaves no room for a retry storm. A 403/429 carrying
        Retry-After or a x-ratelimit-reset in the near future is waited out
        once; a longer wait is reported rather than slept through, because
        blocking an import for the better part of an hour is worse than
        telling the operator to set GITHUB_TOKEN.
        """
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        for attempt in range(max_retries + 1):
            response = self.get(url, headers=self._github_headers())
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise FetchError(f"{url}: unparseable JSON: {exc}") from exc
            if response.status_code == 404:
                raise FetchError(f"{url}: not found (HTTP 404)")
            if response.status_code in (403, 429) and attempt < max_retries:
                wait = _rate_limit_wait(response)
                if wait is None or wait > 120:
                    raise FetchError(
                        f"{url}: rate limited (HTTP {response.status_code}); "
                        "set GITHUB_TOKEN to raise the quota")
                time.sleep(wait)
                continue
            raise FetchError(f"{url}: HTTP {response.status_code}")
        raise FetchError(f"{url}: exhausted retries")

    def github_readme(self, owner: str, repo: str) -> str:
        """Decoded README text, or "" when the repo has none."""
        try:
            payload = self.github_json(f"/repos/{owner}/{repo}/readme")
        except FetchError:
            return ""
        if not isinstance(payload, dict):
            return ""
        if payload.get("encoding") == "base64" and payload.get("content"):
            try:
                return base64.b64decode(payload["content"]).decode("utf-8", "replace")
            except (ValueError, TypeError):
                return ""
        return payload.get("content") or ""


def _rate_limit_wait(response: requests.Response) -> Optional[float]:
    """Seconds to wait per the response headers, or None when unstated."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        try:
            return max(0.0, float(reset) - time.time())
        except ValueError:
            pass
    return None
