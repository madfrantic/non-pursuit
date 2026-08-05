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
"""
import time
from playwright.sync_api import sync_playwright

DISMISS_BANNER_TEXTS = ["Accept", "Accept All", "I Agree", "Got it", "OK"]
MAX_WAIT_SECONDS = 600


def run_search_and_wait(full_name: str, location: str) -> str:
    """Open Chrome, search Spokeo, and block until the human closes the window.

    Returns "closed" if the user closed the browser, or "timed_out" if
    MAX_WAIT_SECONDS elapsed first (the browser is force-closed in that case).
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
                pass

        query = f"{full_name}, {location}" if location else full_name
        search_input = page.query_selector("#homepage_hero_form input[name='q']")
        if search_input:
            search_input.scroll_into_view_if_needed()
            search_input.click()
            search_input.fill(query)
            search_input.press("Enter")
            page.wait_for_timeout(2000)

        elapsed = 0
        while browser.is_connected() and elapsed < MAX_WAIT_SECONDS:
            time.sleep(1)
            elapsed += 1

        if browser.is_connected():
            browser.close()
            return "timed_out"
        return "closed"
