"""
The autonomous background engine.

Ported from chino/GLM.py's TaskScheduler. chino used APScheduler's
QtScheduler, which pumps jobs through a Qt event loop; the Qt layer is gone,
so this uses BackgroundScheduler, which runs its own daemon thread pool and
does not need one.

WHAT IT RUNS, AND WHAT IT DELIBERATELY DOES NOT

Two job types: monitoring scans and verification re-scans. Both are
read-only against the outside world -- they issue searches and record what
came back.

It does NOT submit opt-outs. chino's scheduler could drive its OptOutEngine,
which marked removals SUBMITTED unattended. This repo's CLAUDE.md puts an
automation gate exactly here: broker-freshness checks and data exports are
"objective/quantifiable -- safe to automate", while anything that asserts a
legal demand in the user's name is not. A background thread on a 7-day timer
is the least supervised context in the entire app, so it gets the read-only
half. Work that needs a person is written to review_queue instead.

THREAD SAFETY

Jobs run on scheduler-owned threads, not the Streamlit script thread. Two
consequences shape this module:

  * Nothing here touches st.session_state or any Streamlit API. A background
    thread has no ScriptRunContext, and Streamlit calls from one are either
    dropped with a warning or raise.
  * Every database call goes through broker_ledger / review_queue, which
    open and close a connection per call. A sqlite3 connection cannot be
    shared across threads, which is the bug chino's held-connection
    DatabaseManager would have hit here.

Jobs are registered with max_instances=1 and coalesce=True: a scan that
overruns its interval must not have a second copy started on top of it, and
a machine that was asleep through three fire times should run once on wake,
not three times back to back.
"""
from __future__ import annotations

import atexit
import threading
from datetime import datetime, timezone
from typing import Optional

import config
from applog import get_logger

_log = get_logger("agent_scheduler")

MONITORING_JOB = "monitoring"
VERIFICATION_JOB = "verification"

_LOCK = threading.Lock()


def _job_id(kind: str, profile_id: int) -> str:
    return f"{kind}_profile_{profile_id}"


class AgentScheduler:
    """Owns one APScheduler BackgroundScheduler and the jobs on it."""

    def __init__(self, db_path: str, review_db_path: str,
                 scan_interval_days: Optional[int] = None,
                 verify_interval_days: Optional[int] = None):
        self.db_path = db_path
        self.review_db_path = review_db_path
        self.scan_interval_days = scan_interval_days or config.AGENT_SCAN_INTERVAL_DAYS
        self.verify_interval_days = verify_interval_days or config.AGENT_VERIFY_INTERVAL_DAYS
        self._scheduler = None
        self._started = False
        self._last_error: str = ""

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Start the daemon thread pool. Idempotent; returns True if running."""
        with _LOCK:
            if self._started:
                return True
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
            except ImportError as exc:
                self._last_error = f"APScheduler not installed: {exc}"
                _log.warning(self._last_error)
                return False

            try:
                self._scheduler = BackgroundScheduler(
                    daemon=True,
                    job_defaults={
                        "max_instances": 1,
                        "coalesce": True,
                        "misfire_grace_time": 3600,
                    },
                )
                self._scheduler.start()
                self._started = True
                atexit.register(self.stop)
                _log.info("Agent scheduler started (scan every %sd, verify every %sd)",
                          self.scan_interval_days, self.verify_interval_days)
                return True
            except Exception as exc:  # noqa: BLE001 - never take the app down with us
                self._last_error = str(exc)
                _log.error("Could not start agent scheduler: %s", exc)
                self._scheduler = None
                return False

    def stop(self) -> None:
        with _LOCK:
            if self._scheduler and self._started:
                try:
                    self._scheduler.shutdown(wait=False)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("Scheduler shutdown raised: %s", exc)
                finally:
                    self._started = False
                    _log.info("Agent scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._started and self._scheduler is not None

    @property
    def last_error(self) -> str:
        return self._last_error

    # -- jobs --------------------------------------------------------------

    def add_monitoring_job(self, profile_id: int,
                           interval_days: Optional[int] = None) -> Optional[str]:
        """Re-scan the brokers that previously held this profile's data."""
        return self._add(MONITORING_JOB, profile_id,
                         interval_days or self.scan_interval_days,
                         self._run_monitoring_scan)

    def add_verification_job(self, profile_id: int,
                             interval_days: Optional[int] = None) -> Optional[str]:
        """Re-check whether submitted removals actually took."""
        return self._add(VERIFICATION_JOB, profile_id,
                         interval_days or self.verify_interval_days,
                         self._run_verification)

    def _add(self, kind: str, profile_id: int, interval_days: int, func) -> Optional[str]:
        if not self.is_running:
            _log.warning("Cannot add %s job: scheduler is not running", kind)
            return None
        from apscheduler.triggers.interval import IntervalTrigger

        job_id = _job_id(kind, profile_id)
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(days=interval_days),
            id=job_id,
            args=[profile_id],
            replace_existing=True,
        )
        import broker_ledger
        broker_ledger.record_scheduled_task(
            self.db_path, kind, profile_id=profile_id, interval_days=interval_days,
            next_run=self._next_run(job_id))
        _log.info("Scheduled %s for profile %s every %s day(s)", kind, profile_id, interval_days)
        return job_id

    def remove_job(self, job_id: str) -> None:
        if not self.is_running:
            return
        try:
            self._scheduler.remove_job(job_id)
            _log.info("Removed job %s", job_id)
        except Exception:  # noqa: BLE001 - removing an absent job is not an error
            pass

    def _next_run(self, job_id: str) -> str:
        try:
            job = self._scheduler.get_job(job_id)
            return str(job.next_run_time) if job and job.next_run_time else ""
        except Exception:  # noqa: BLE001
            return ""

    def get_jobs(self) -> list[dict]:
        if not self.is_running:
            return []
        out = []
        for job in self._scheduler.get_jobs():
            out.append({
                "id": job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else "n/a",
                "trigger": str(job.trigger),
            })
        return out

    # -- job bodies --------------------------------------------------------
    #
    # These run on a scheduler thread. Every exception is caught: an
    # uncaught one inside APScheduler kills that job's future runs, so a
    # single network blip would silently end monitoring for good.

    def _run_monitoring_scan(self, profile_id: int) -> None:
        _log.info("Scheduled monitoring scan starting for profile %s", profile_id)
        try:
            from agent_engines import RemovalPlanner, ScannerEngine
            scanner = ScannerEngine(self.db_path, self.review_db_path)
            results = scanner.run_monitoring_scan(profile_id)
            # A re-listing is only useful if it becomes outstanding work.
            RemovalPlanner(self.db_path, self.review_db_path).prepare_removals(profile_id)
            _log.info("Monitoring scan finished for profile %s: %s broker(s)",
                      profile_id, len(results))
        except Exception as exc:  # noqa: BLE001
            _log.error("Monitoring scan failed for profile %s: %s", profile_id, exc)

    def _run_verification(self, profile_id: int) -> None:
        _log.info("Scheduled verification starting for profile %s", profile_id)
        try:
            from agent_engines import VerificationEngine
            results = VerificationEngine(self.db_path, self.review_db_path)\
                .verify_all_submitted(profile_id)
            _log.info("Verification finished for profile %s: %s removal(s)",
                      profile_id, len(results))
        except Exception as exc:  # noqa: BLE001
            _log.error("Verification failed for profile %s: %s", profile_id, exc)


def build_scheduler(db_path: str, review_db_path: str) -> AgentScheduler:
    """Construct and start a scheduler, and enrol every active profile.

    Called once per Streamlit server process from app.py under
    @st.cache_resource -- see that call site for why exactly once.
    """
    scheduler = AgentScheduler(db_path, review_db_path)
    if not config.AGENT_SCHEDULER_ENABLED:
        _log.info("Agent scheduler disabled by config")
        return scheduler
    if not scheduler.start():
        return scheduler

    try:
        import broker_ledger
        broker_ledger.init_ledger(db_path)
        for profile in broker_ledger.get_profiles(db_path):
            scheduler.add_monitoring_job(profile["id"])
            scheduler.add_verification_job(profile["id"])
    except Exception as exc:  # noqa: BLE001
        _log.error("Could not enrol profiles with the scheduler: %s", exc)
    return scheduler
