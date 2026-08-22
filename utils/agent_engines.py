"""
Scanner and verification engines for the broker agent.

Ported from chino/GLM.py's ScannerEngine, OptOutEngine and
VerificationEngine, with one substantive change: chino's engines called
`handler.search()` / `submit_opt_out()` / `verify_removal()` on a
BrokerHandler ABC whose only implementation was ExampleBrokerHandler -- a
`# TODO` stub returning `found=False` and the literal message "Stub
implementation - not actually searched". Ported verbatim, the whole
pipeline would run, write rows, and mean nothing.

So the search path is wired to utils/broker_probe.py, which is this repo's
real broker scanner (verified contact registry, per-broker classification
rules, tri-state verdicts), and verification re-runs that same probe. The
orchestration around it -- scan loop, monitoring scan, retry accounting,
progress callbacks, rate limiting -- is chino's, and is what was worth
porting.

WHAT THESE ENGINES WILL NOT DO

They do not submit. utils/optout_engine.execute_optout() refuses
`dry_run=False`, CLAUDE.md designates submission a Human Validation Zone,
and the background scheduler that drives these engines runs unattended by
definition. So a removal that automation cannot finish is written as
REQUIRES_MANUAL and filed to review_queue for a person, which is the same
thing the rest of this app already does. chino's OptOutEngine marked
removals SUBMITTED on its own; that behaviour is deliberately not carried
over. `prepare_removals()` is the honest half of it: it opens the removal
records and queues the human work, and stops there.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

import broker_ledger
import broker_probe
import optout_engine
import review_queue
from applog import get_logger
from broker_agent_models import (
    BrokerDifficulty,
    RemovalStatus,
    ScanStatus,
    coerce_difficulty,
)

_log = get_logger("agent_engines")

# Seconds between brokers. chino used 2s for scans and 3s for opt-outs;
# broker_probe already bounds concurrency internally, so this is the extra
# courtesy delay between sequential ledger-driven passes.
SCAN_DELAY_SECONDS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _subject_from_profile(profile) -> dict:
    """Map a vault Profile onto the subject dict broker_probe expects."""
    data = profile.as_target_data()
    first = data.get("first_name", "")
    last = data.get("last_name", "")
    return {
        "name": data.get("full_name") or " ".join(p for p in (first, last) if p),
        "first_name": first,
        "last_name": last,
        "city": data.get("city", ""),
        "state": data.get("state", ""),
        "email": data.get("email", ""),
        "age": data.get("age", ""),
    }


class ScannerEngine:
    """Scans a profile across the brokers in the ledger."""

    def __init__(self, db_path: str, review_db_path: str):
        self.db_path = db_path
        self.review_db_path = review_db_path
        self._running = False
        self._progress_callback: Optional[Callable] = None

    def set_progress_callback(self, callback: Callable) -> None:
        self._progress_callback = callback

    def _report(self, current: int, total: int, message: str) -> None:
        if self._progress_callback:
            try:
                self._progress_callback(current, total, message)
            except Exception as exc:  # noqa: BLE001 - a UI callback must not kill a scan
                _log.warning("Progress callback raised: %s", exc)

    def cancel(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def scan_profile(self, profile_id: int, broker_ids: Optional[list] = None,
                     scan_type: str = "initial") -> list[dict]:
        """Scan one profile across brokers. Returns the scan rows written."""
        profile = broker_ledger.load_profile(self.db_path, profile_id)
        if profile is None:
            _log.error("Scan requested for unknown profile %s", profile_id)
            return []
        if not profile.fields:
            _log.error("Profile %s has no PII fields; nothing to search on", profile_id)
            return []

        subject = _subject_from_profile(profile)
        if not subject.get("name"):
            _log.error("Profile %s has no name; broker search needs one", profile_id)
            return []

        if broker_ids:
            brokers = [b for b in (broker_ledger.get_broker(self.db_path, i)
                                   for i in broker_ids) if b]
        else:
            brokers = broker_ledger.get_brokers(self.db_path)

        if not brokers:
            _log.info("No active brokers in the ledger; nothing to scan")
            return []

        results = []
        total = len(brokers)
        self._running = True

        try:
            for index, broker in enumerate(brokers, start=1):
                if not self._running:
                    _log.info("Scan cancelled after %s of %s brokers", index - 1, total)
                    break

                self._report(index, total, f"Scanning {broker['name']}...")
                scan_id = broker_ledger.create_scan(
                    self.db_path, profile_id, broker["id"], scan_type)
                broker_ledger.update_scan(
                    self.db_path, scan_id,
                    status=ScanStatus.IN_PROGRESS.value,
                    started_at=_now(),
                    search_url_used=broker.get("search_url") or "",
                )

                difficulty = coerce_difficulty(broker.get("difficulty"))
                if difficulty.queues_immediately:
                    # Never open a browser at a CAPTCHA-gated or manual-only
                    # broker: the run cannot finish and the attempt itself is
                    # what trips the bot check.
                    broker_ledger.update_scan(
                        self.db_path, scan_id,
                        status=ScanStatus.FAILED.value,
                        error_message=f"requires a human ({difficulty.value})",
                        completed_at=_now(),
                    )
                    broker_ledger.queue_for_human(
                        self.review_db_path,
                        target=broker["name"], task_type="broker_search",
                        reason=difficulty.queue_reason,
                        note=f"Search {broker['name']} by hand.",
                        current_url=broker.get("search_url") or "",
                    )
                    results.append({"broker": broker["name"], "status": "queued",
                                    "found": None, "scan_id": scan_id})
                    continue

                try:
                    rows = broker_probe.probe_brokers_sync(
                        subject, brokers=[self._as_probe_broker(broker)])
                    row = rows[0] if rows else {}
                    verdict = row.get("verdict", broker_probe.PROBE_ERROR)
                    found = verdict == broker_probe.RECORD_FOUND
                    record_urls = row.get("record_urls") or []

                    if verdict == broker_probe.MANUAL_CHECK:
                        status = ScanStatus.FAILED.value
                        broker_ledger.queue_for_human(
                            self.review_db_path,
                            target=broker["name"], task_type="broker_search",
                            reason=review_queue.TOS_PROHIBITS,
                            note=row.get("reason", "")[:200],
                            current_url=row.get("search_url", ""),
                        )
                    elif verdict == broker_probe.PROBE_ERROR:
                        status = ScanStatus.FAILED.value
                    else:
                        status = ScanStatus.COMPLETED.value

                    broker_ledger.update_scan(
                        self.db_path, scan_id,
                        status=status, found=found,
                        result_url=record_urls[0] if record_urls else "",
                        error_message=("" if status == ScanStatus.COMPLETED.value
                                       else row.get("reason", "")[:300]),
                        completed_at=_now(),
                    )
                    results.append({"broker": broker["name"], "status": status,
                                    "found": found, "scan_id": scan_id,
                                    "verdict": verdict})

                except Exception as exc:  # noqa: BLE001 - one broker must not end the sweep
                    _log.error("Scan failed for %s: %s", broker["name"], exc)
                    broker_ledger.update_scan(
                        self.db_path, scan_id,
                        status=ScanStatus.FAILED.value,
                        error_message=str(exc)[:300],
                        completed_at=_now(),
                    )
                    results.append({"broker": broker["name"], "status": "failed",
                                    "found": False, "scan_id": scan_id})

                if index < total:
                    time.sleep(SCAN_DELAY_SECONDS)
        finally:
            self._running = False

        broker_ledger.log_activity(
            self.db_path, "scan_profile", "profile", profile_id,
            f"{scan_type}: {len(results)} brokers")
        return results

    @staticmethod
    def _as_probe_broker(broker: dict) -> dict:
        """Ledger row -> the dict shape broker_probe.build_registry emits."""
        return {
            "broker": broker["name"],
            "name": broker["name"],
            "search_url": broker.get("search_url") or "",
            "optout_url": broker.get("opt_out_url") or "",
            "compliance_email": broker.get("compliance_email") or "",
            "mode": broker_probe.MODE_QUERY,
        }

    def run_monitoring_scan(self, profile_id: int) -> list[dict]:
        """Re-scan only the brokers that previously had a record.

        The point of a monitoring pass: a broker that never held your data
        is not where re-exposure shows up, and re-sweeping all of them costs
        a full scan to learn nothing.
        """
        previous = broker_ledger.get_scans(self.db_path, profile_id=profile_id, found_only=True)
        broker_ids = sorted({s["broker_id"] for s in previous})
        if not broker_ids:
            _log.info("No prior findings for profile %s; monitoring scan skipped", profile_id)
            return []
        return self.scan_profile(profile_id, broker_ids=broker_ids, scan_type="monitoring")


class RemovalPlanner:
    """Opens removal records for found exposures and queues the human work.

    This is chino's OptOutEngine with the autonomous submission removed --
    see the module docstring. It does the bookkeeping half (which is real
    work: knowing what is outstanding is most of a campaign) and stops at
    the point a person has to act.
    """

    def __init__(self, db_path: str, review_db_path: str):
        self.db_path = db_path
        self.review_db_path = review_db_path

    def prepare_removals(self, profile_id: Optional[int] = None) -> list[dict]:
        """Create a removal for every found scan that doesn't have one yet."""
        scans = broker_ledger.get_scans(self.db_path, profile_id=profile_id, found_only=True)
        existing = {
            r["scan_id"] for r in broker_ledger.get_removals(self.db_path, profile_id=profile_id)
        }
        created = []
        for scan in scans:
            if scan["id"] in existing:
                continue
            broker = broker_ledger.get_broker(self.db_path, scan["broker_id"]) or {}
            removal_id = broker_ledger.create_removal(
                self.db_path, scan["id"], scan["profile_id"], scan["broker_id"],
                opt_out_url=broker.get("opt_out_url") or "",
            )
            difficulty = coerce_difficulty(broker.get("difficulty"))
            reason = (difficulty.queue_reason if difficulty.queues_immediately
                      else review_queue.SUBMIT_REQUIRES_SIGNOFF)
            broker_ledger.update_removal(
                self.db_path, removal_id,
                status=RemovalStatus.REQUIRES_MANUAL.value,
                submission_method="manual",
                notes="Awaiting human review; automation does not submit.",
            )
            broker_ledger.queue_for_human(
                self.review_db_path,
                target=broker.get("name", str(scan["broker_id"])),
                task_type="broker_optout",
                reason=reason,
                note=f"Record found at {broker.get('name','broker')}. "
                     f"Review and submit the opt-out.",
                current_url=broker.get("opt_out_url") or "",
            )
            created.append({"removal_id": removal_id, "broker": broker.get("name", ""),
                            "reason": reason})
        if created:
            _log.info("Opened %s removal record(s) awaiting human action", len(created))
        return created

    def retry_failed(self, profile_id: Optional[int] = None) -> int:
        """Reset retryable failures to pending. Returns how many."""
        retryable = broker_ledger.get_removals_for_retry(self.db_path)
        if profile_id is not None:
            retryable = [r for r in retryable if r["profile_id"] == profile_id]
        for removal in retryable:
            broker_ledger.update_removal(
                self.db_path, removal["id"],
                status=RemovalStatus.PENDING.value,
                retry_count=removal["retry_count"] + 1,
            )
        return len(retryable)


class VerificationEngine:
    """Re-scans after a removal to check whether the record actually went.

    chino's version was a stub end to end: verify_removal() called
    self.db.get_removal(), a method its DatabaseManager did not define, and
    returned False with a "[STUB]" log line. Both halves are real here --
    broker_ledger.get_removal() exists, and the re-search runs through
    broker_probe.
    """

    def __init__(self, db_path: str, review_db_path: str):
        self.db_path = db_path
        self.review_db_path = review_db_path

    def verify_removal(self, removal_id: int) -> Optional[bool]:
        """Check whether a removal actually succeeded.

        Returns True/False/None:
          - True: profile is no longer at the broker (removal confirmed)
          - False: profile is still there (removal failed)
          - None: the check could not be completed (probe error, or broker
                  is CAPTCHA-gated and requires manual inspection)

        IMPORTANT: Callers MUST handle the None return value. None means the
        verification could not be made, so the removal status should not change.
        Returning False would manufacture false evidence of compliance.
        See CLAUDE.md: a verification that could not be completed is recorded
        as None, not False, to avoid manufacturing evidence of non-compliance.
        """
        removal = broker_ledger.get_removal(self.db_path, removal_id)
        if not removal:
            _log.error("Verification requested for unknown removal %s", removal_id)
            return None

        profile = broker_ledger.load_profile(self.db_path, removal["profile_id"])
        if profile is None:
            return None

        broker = broker_ledger.get_broker(self.db_path, removal["broker_id"]) or {}
        difficulty = coerce_difficulty(broker.get("difficulty"))
        if difficulty.queues_immediately:
            broker_ledger.queue_for_human(
                self.review_db_path,
                target=broker.get("name", ""), task_type="verify_removal",
                reason=difficulty.queue_reason,
                note="Confirm by hand whether the record is gone.",
                current_url=broker.get("search_url") or "",
            )
            return None

        try:
            rows = broker_probe.probe_brokers_sync(
                _subject_from_profile(profile),
                brokers=[ScannerEngine._as_probe_broker(broker)],
            )
        except Exception as exc:  # noqa: BLE001
            _log.error("Verification probe failed for removal %s: %s", removal_id, exc)
            return None

        verdict = (rows[0] if rows else {}).get("verdict", broker_probe.PROBE_ERROR)
        if verdict == broker_probe.NO_RECORD:
            broker_ledger.update_removal(
                self.db_path, removal_id,
                status=RemovalStatus.CONFIRMED.value, confirmed_at=_now())
            broker_ledger.add_evidence(
                self.db_path, removal_id, "confirmation_page",
                notes=f"Re-scan on {_now()} returned NO_RECORD.")
            _log.info("Removal %s confirmed: no record at %s",
                      removal_id, broker.get("name", ""))
            return True

        if verdict == broker_probe.RECORD_FOUND:
            _log.info("Removal %s still listed at %s", removal_id, broker.get("name", ""))
            return False

        return None

    def verify_all_submitted(self, profile_id: Optional[int] = None) -> list[dict]:
        """Verify every removal that has been submitted but not confirmed.
        
        Returns a list of dicts with keys: removal_id, broker_name, verified.
        Note: verified can be True (confirmed), False (failed), or None (unchecked).
        None does NOT mean failure -- it means the verification could not be made.
        """
        removals = broker_ledger.get_removals(
            self.db_path, profile_id=profile_id, status=RemovalStatus.SUBMITTED.value)
        results = []
        for removal in removals:
            result_status = self.verify_removal(removal["id"])
            results.append({
                "removal_id": removal["id"],
                "broker_name": removal.get("broker_name", ""),
                "verified": result_status,  # True/False/None (None = unchecked)
            })
        return results
