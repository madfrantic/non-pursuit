"""
Spokeo self-search automation.

Spokeo is the only one of the four brokers in brokers.csv that doesn't run
bot-detection against an automated browser (MyLife hard-blocks it via
Cloudflare, WhitePages serves a CAPTCHA, BeenVerified silently stalls
forever) — verified by hand before writing this. So this module is
deliberately Spokeo-specific rather than a general "automate any broker"
framework; extending it to another broker means verifying that broker
doesn't fight back first, not just adding a config entry.

Playwright's sync API ties its internal dispatcher to the OS thread that
called sync_playwright().start(). Streamlit reruns a script on a fresh
thread per interaction, so a browser opened in one rerun can't be closed
from a later one — attempting that raises
"cannot switch to a different thread". To avoid that entirely, the whole
open-search-wait-for-the-human-to-close-it sequence happens inside a single
blocking call: the human closing the Chrome window IS the "done reviewing"
signal, and this function returns once that happens (or after a timeout).

While that wait loop runs, it also polls page.url so that if the human
clicks into their own listing, the resulting record-detail URL gets
captured automatically instead of requiring a manual copy/paste of the
address bar into the Letters page afterward.
"""
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from applog import get_logger

DISMISS_BANNER_TEXTS = ["Accept", "Accept All", "I Agree", "Got it", "OK"]
MAX_WAIT_SECONDS = 600

_log = get_logger("spokeo_automation")


def _is_record_url(url: str, search_results_url: str) -> bool:
    """True once the human has navigated off the search-results page to
    what looks like an actual record on spokeo.com (not some other site a
    banner or ad redirected them to)."""
    if not url or url == search_results_url:
        return False
    return urlparse(url).netloc.endswith("spokeo.com")


def run_search_and_wait(full_name: str, location: str) -> dict:
    """Open Chrome, search Spokeo, and block until the human closes the window.

    Returns {"outcome": "closed" | "timed_out", "record_url": str | None}.
    outcome is "closed" if the user closed the browser, or "timed_out" if
    MAX_WAIT_SECONDS elapsed first (the browser is force-closed in that
    case). record_url is the last spokeo.com URL seen that wasn't the
    search-results page itself, i.e. a specific listing the human clicked
    into -- or None if they never navigated past the results list.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page()
        page.goto("https://www.spokeo.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        for text in DISMISS_BANNER_TEXTS:
            btn = page.get_by_text(text, exact=False)
            try:
                if btn.count() > 0:
                    btn.first.click(timeout=1000)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                _log.exception("Failed dismissing a Spokeo banner (text=%r)", text)

        query = f"{full_name}, {location}" if location else full_name
        search_input = page.query_selector("#homepage_hero_form input[name='q']")
        if search_input:
            search_input.scroll_into_view_if_needed()
            search_input.click()
            search_input.fill(query)
            search_input.press("Enter")
            page.wait_for_timeout(2000)

        search_results_url = page.url
        record_url = None

        elapsed = 0
        while browser.is_connected() and elapsed < MAX_WAIT_SECONDS:
            try:
                current_url = page.url
                if _is_record_url(current_url, search_results_url):
                    record_url = current_url
            except Exception:
                # The tab/browser can close between the is_connected() check
                # above and reading page.url -- that's just the human
                # finishing up, not a real error, so nothing is logged here.
                pass
            time.sleep(1)
            elapsed += 1

        if browser.is_connected():
            browser.close()
            return {"outcome": "timed_out", "record_url": record_url}
        return {"outcome": "closed", "record_url": record_url}
