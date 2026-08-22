"""
Data Broker Opt-Out Automation Engine

This module serves as the foundation for automated data removal requests.
Unlike the interactive Spokeo searcher, this engine is designed to run headlessly
and programmatically navigate to a broker's opt-out page and fill out the
required information (Name, Age, State, Email).

This uses a factory/strategy pattern so you can add new broker classes
as you expand your coverage.

DRY RUN

Every automator here fills the form and stops. Nothing is submitted. That is
the same Human Validation Zone the rest of this app enforces (CLAUDE.md): an
opt-out is a legal assertion made in the subject's name, so a person signs it
off, not a background task. `execute_optout` refuses `dry_run=False` until a
broker class implements a reviewed submit path.

playwright is imported inside `execute_optout`, not at module scope: the API
imports this module to expose the endpoint, and a deployment that never runs
a browser (the container has no Chrome) must still be able to boot.
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import config
from applog import get_logger
from broker_agent_models import (
    EVIDENCE_AFTER,
    EVIDENCE_BEFORE,
    EVIDENCE_CONFIRMATION,
    EVIDENCE_TYPES,
    BrokerDifficulty,
    coerce_difficulty,
)

_log = get_logger("optout_engine")


class BaseBrokerAutomator:
    """Base class for all data broker automation scripts."""
    
    BROKER_NAME = "Base"
    OPTOUT_URL = ""
    
    def __init__(self, target_data: Dict[str, Any], proxy_email: str):
        """
        target_data: Dict containing 'first_name', 'last_name', 'state', 'age', etc.
        proxy_email: The masked email address to receive the confirmation link.
        """
        self.target = target_data
        self.proxy_email = proxy_email

    def run(self, page) -> Dict[str, Any]:
        """
        To be implemented by specific broker classes.
        `page` is a playwright.sync_api.Page.
        Must return a dict indicating success/failure and any relevant logs.
        """
        raise NotImplementedError("Subclasses must implement run()")


class ExampleBrokerAutomator(BaseBrokerAutomator):
    """
    Example implementation for a standard 'fill and submit' opt-out form.
    (e.g., similar to TruePeopleSearch or FastPeopleSearch opt-out flows)
    """
    BROKER_NAME = "ExampleBroker"
    OPTOUT_URL = "https://example-broker.com/opt-out"
    
    def run(self, page) -> Dict[str, Any]:
        try:
            _log.info("Navigating to %s", self.OPTOUT_URL)
            page.goto(self.OPTOUT_URL, wait_until="networkidle")
            
            # Step 1: Agree to terms if there's a popup
            try:
                page.get_by_text("I Agree").click(timeout=3000)
            except Exception:
                pass # No popup found, proceed
                
            # Step 2: Fill out the opt-out form
            _log.info("Filling out target data...")
            page.fill("input[name='firstName']", self.target.get("first_name", ""))
            page.fill("input[name='lastName']", self.target.get("last_name", ""))
            page.fill("input[name='email']", self.proxy_email)
            
            # Step 3: Check the certification box
            page.check("input[type='checkbox'][name='certify']")
            
            # Step 4: Submit
            # page.click("button[type='submit']")
            # page.wait_for_load_state("networkidle")
            
            # Step 5: Verify success message
            # if page.get_by_text("confirmation email sent").is_visible():
            #     return {"status": "success", "message": "Confirmation email sent."}
            
            return {"status": "simulated_success", "message": "Form filled and submitted successfully in dry-run."}
            
        except Exception as e:
            _log.error("Failed to process %s: %s", self.BROKER_NAME, e)
            return {"status": "error", "error": str(e)}



# ---------------------------------------------------------------------------
# Evidence chain (ported from chino/GLM.py VerificationEngine)
# ---------------------------------------------------------------------------
# chino had capture_screenshot() and calculate_file_hash() as `# TODO` stubs
# that logged "[STUB] Would capture screenshot" and returned "". The hashing
# half was real; the capture half was never written. Both are implemented
# here.
#
# WHY THE HASH IS THE POINT
#
# A screenshot on its own proves nothing -- it is a PNG, and PNGs can be
# edited. What makes it evidence is a digest computed at capture time and
# stored in a row nobody edits afterwards. If the file is later altered, its
# SHA256 stops matching what the ledger recorded, and the alteration is
# detectable. That is the whole difference between a screenshot and a
# record, and it is why capture and hash happen in one function here rather
# than being two calls a caller could forget to pair.
#
# Evidence files live under config.EVIDENCE_DIR, which is gitignored -- a
# broker's search results page is, by definition, a page with the user's PII
# on it.


def evidence_dir() -> Path:
    path = Path(config.EVIDENCE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def calculate_file_hash(filepath) -> str:
    """SHA256 of a file, or "" if it isn't there.

    Read in chunks: an evidence screenshot of a long results page can be
    several megabytes, and there is no reason to hold one in memory whole.
    """
    path = Path(filepath)
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_filename(broker_name: str, evidence_type: str) -> str:
    """A filesystem-safe, collision-resistant name for one capture."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(broker_name).lower()).strip("_") or "broker"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{slug}_{evidence_type}_{stamp}.png"


def capture_evidence(page, broker_name: str, evidence_type: str = EVIDENCE_BEFORE,
                     notes: str = "") -> dict:
    """Screenshot the current page and hash it in one step.

    Returns a dict shaped for broker_ledger.add_evidence(). On failure it
    returns the same shape with an `error` key and an empty hash rather than
    raising -- a removal that succeeded should not be recorded as failed
    just because the screenshot did not save.
    """
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(
            f"Unknown evidence type {evidence_type!r}; expected one of {list(EVIDENCE_TYPES)}")

    target = evidence_dir() / _evidence_filename(broker_name, evidence_type)
    try:
        page.screenshot(path=str(target), full_page=True)
    except Exception as exc:  # noqa: BLE001 - capture must not fail the removal
        _log.error("Evidence capture failed for %s: %s", broker_name, exc)
        return {"evidence_type": evidence_type, "file_path": "", "file_hash": "",
                "notes": notes, "error": str(exc)}

    file_hash = calculate_file_hash(target)
    _log.info("Captured %s evidence for %s (sha256 %s)",
              evidence_type, broker_name, file_hash[:12])
    return {"evidence_type": evidence_type, "file_path": str(target),
            "file_hash": file_hash, "notes": notes}


def verify_evidence(file_path, expected_hash: str) -> bool:
    """True when the file on disk still hashes to what was recorded."""
    if not expected_hash:
        return False
    return calculate_file_hash(file_path) == expected_hash


# ---------------------------------------------------------------------------
# CAPTCHA detection (ported from chino/GLM.py BrokerHandler.detect_captcha)
# ---------------------------------------------------------------------------
# Detection only. chino's docstring was explicit that solving a CAPTCHA
# would violate site terms, and that stands: a detected CAPTCHA becomes a
# review_queue item for a person, never something this code tries to defeat.

CAPTCHA_MARKERS = (
    "recaptcha", "hcaptcha", "cf-turnstile", "g-recaptcha",
    "h-captcha", "cf-turnstile-response", "captcha",
)


def detect_captcha(page) -> bool:
    """True when the page appears to be showing a bot check."""
    try:
        content = (page.content() or "").lower()
        if any(marker in content for marker in CAPTCHA_MARKERS):
            return True
        for frame in getattr(page, "frames", []) or []:
            if "captcha" in (getattr(frame, "url", "") or "").lower():
                return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("CAPTCHA detection failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Difficulty taxonomy
# ---------------------------------------------------------------------------
# Which brokers automation should not even open a browser for. Sourced from
# chino's default broker seed list plus this repo's data/brokers.csv notes,
# both of which independently recorded Cloudflare/CAPTCHA gating on the same
# sites.

BROKER_DIFFICULTY = {
    "spokeo": BrokerDifficulty.CAPTCHA,
    "truepeoplesearch": BrokerDifficulty.CAPTCHA,
    "nuwber": BrokerDifficulty.CAPTCHA,
    "whitepages": BrokerDifficulty.STANDARD,
    "fastpeoplesearch": BrokerDifficulty.STANDARD,
    "thatsthem": BrokerDifficulty.STANDARD,
    "that's them": BrokerDifficulty.STANDARD,
    "mylife": BrokerDifficulty.STANDARD,
    "radaris": BrokerDifficulty.HARD,
    "beenverified": BrokerDifficulty.HARD,
    "intelius": BrokerDifficulty.HARD,
    "example": BrokerDifficulty.EASY,
}


def difficulty_for(broker_name: str) -> BrokerDifficulty:
    """The recorded difficulty for a broker, STANDARD when unknown."""
    return BROKER_DIFFICULTY.get(str(broker_name).strip().lower(),
                                 BrokerDifficulty.STANDARD)


# The one place a broker id maps to an automator. Callers validate against
# this rather than guessing, so an unsupported broker fails loudly instead of
# being quietly run through the example mock.
BROKER_AUTOMATORS = {
    "example": ExampleBrokerAutomator,
}

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def supported_brokers() -> tuple[str, ...]:
    """Broker ids that have an automator behind them."""
    return tuple(sorted(BROKER_AUTOMATORS))


def execute_optout(broker_id: str, target_data: dict, proxy_email: str,
                   dry_run: bool = True) -> dict:
    """
    Main entry point for running an automated opt-out.

    `dry_run` is not a convenience flag: see the module docstring. No automator
    submits yet, so a caller asking for a real submission is asking for
    something this module cannot honestly do.
    """
    automator_class = BROKER_AUTOMATORS.get(broker_id)
    if automator_class is None:
        return {"status": "error",
                "error": f"Unknown broker ID: {broker_id}",
                "supported": list(supported_brokers())}
    if not dry_run:
        return {"status": "error",
                "error": "Live submission is not implemented; no automator has "
                         "a human-signed-off submit path."}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _log.error("playwright is not installed: %s", exc)
        return {"status": "error", "error": "playwright is not installed"}

    automator = automator_class(target_data, proxy_email)

    # Run headlessly by default for background jobs
    try:
        with sync_playwright() as p:
            # channel="chrome" drives the locally installed Google Chrome, the
            # same as spokeo_automation -- this project never runs
            # `playwright install`, so a bundled-chromium launch would not find
            # a browser to start.
            browser = p.chromium.launch(channel="chrome", headless=True)
            try:
                # An explicit user agent keeps the request from announcing
                # itself as headless automation to basic anti-bot screens.
                context = browser.new_context(user_agent=USER_AGENT)
                try:
                    return automator.run(context.new_page())
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - browser launch, not the form fill
        _log.error("Could not start a browser for %s: %s", broker_id, exc)
        return {"status": "error", "error": f"browser unavailable: {exc}"}

# --- For local testing ---
if __name__ == "__main__":
    test_target = {
        "first_name": "Jane",
        "last_name": "Doe",
        "state": "CA",
        "age": "35"
    }
    # This is a dry run simulation
    print("Testing Example Broker Automator...")
    res = execute_optout("example", test_target, "test.proxy123@example.com")
    print(res)
