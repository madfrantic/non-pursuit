"""
Typed vocabulary for the broker-agent pipeline.

Ported from chino/GLM.py (the standalone PySide6 "DataBroker Agent"), whose
engines passed dicts around and spelled their status strings inline. The
strings are the useful part -- they are what lands in SQLite and what the
UI filters on -- so they live here as enums with one definition each, the
same way utils/review_queue.py keeps its reason vocabulary closed.

WHY THESE ARE DATACLASSES AND NOT dicts

The rest of utils/ passes dicts, and for row-shaped data that is fine. These
seven are different: they cross an engine boundary (scanner -> opt-out ->
verification -> evidence), and a typo in a key name on that path is a bug
that shows up three stages later as a missing removal. A dataclass fails at
the assignment instead.

Rows read back out of SQLite stay dicts, as everywhere else in this app.
`from_row` is the one-way door between the two.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

import review_queue


class ScanStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class RemovalStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REQUIRES_MANUAL = "requires_manual"


class BrokerDifficulty(Enum):
    """How much of a broker's opt-out a machine can actually do.

    This is the taxonomy that decides whether a broker is even a candidate
    for automation. CAPTCHA and MANUAL_ONLY never are -- see
    `queues_immediately`.
    """
    EASY = "easy"
    STANDARD = "standard"
    HARD = "hard"
    CAPTCHA = "captcha"
    MANUAL_ONLY = "manual_only"

    @property
    def queues_immediately(self) -> bool:
        """True when automation should not even open the page.

        A CAPTCHA-gated or manual-only broker goes straight to the human
        queue. Attempting it first would either trip a bot check or waste a
        browser launch, and in neither case can the run finish.
        """
        return self in (BrokerDifficulty.CAPTCHA, BrokerDifficulty.MANUAL_ONLY)

    @property
    def queue_reason(self) -> str:
        """The review_queue reason to file under when this difficulty blocks."""
        if self is BrokerDifficulty.CAPTCHA:
            return review_queue.CAPTCHA
        return review_queue.NO_AUTOMATOR


# ---------------------------------------------------------------------------
# Queue vocabulary reconciliation
# ---------------------------------------------------------------------------
# chino had its own QueueReason enum. utils/review_queue.py already had a
# closed reason vocabulary, chosen so the column stays aggregatable -- and it
# is the more precise of the two. Rather than carry both, chino's reasons map
# onto the existing ones:
#
#   captcha             -> CAPTCHA                  (same concept)
#   phone_verification  -> IDENTITY_VERIFICATION    (both are "prove it's you")
#   email_verification  -> IDENTITY_VERIFICATION
#   error               -> SITE_UNREACHABLE         (chino's only failure bucket)
#   other               -> (rejected)
#
# `other` deliberately has no mapping. A free-form catch-all is exactly what
# review_queue's closed vocabulary exists to prevent -- it becomes a dumping
# ground and the column stops meaning anything. Callers that would have used
# it must pick a real reason.
CHINO_QUEUE_REASON_MAP = {
    "captcha": review_queue.CAPTCHA,
    "phone_verification": review_queue.IDENTITY_VERIFICATION,
    "email_verification": review_queue.IDENTITY_VERIFICATION,
    "error": review_queue.SITE_UNREACHABLE,
}


def map_queue_reason(chino_reason: str) -> str:
    """Translate a chino QueueReason string to a review_queue reason.

    Raises ValueError for 'other' and anything unrecognised, rather than
    silently bucketing it -- see CHINO_QUEUE_REASON_MAP.
    """
    try:
        return CHINO_QUEUE_REASON_MAP[chino_reason]
    except KeyError:
        raise ValueError(
            f"No review_queue reason for {chino_reason!r}. "
            f"Valid: {sorted(CHINO_QUEUE_REASON_MAP)}"
        ) from None


@dataclass
class PIIField:
    """One piece of PII belonging to a profile."""
    field_type: str
    value: str
    label: str = ""

    def __post_init__(self):
        if not self.label:
            self.label = self.field_type.replace("_", " ").title()


@dataclass
class Profile:
    """A named identity the agent works on behalf of.

    Distinct from `target_profile` in utils/database.py (the single baseline
    identity the Streamlit session edits) and from profile_state's session
    dict. This one is the vault's multi-profile record: a household might
    run three.
    """
    id: Optional[int] = None
    name: str = ""
    fields: List[PIIField] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_active: bool = True

    def get(self, field_type: str, default: str = "") -> str:
        for f in self.fields:
            if f.field_type == field_type:
                return f.value
        return default

    def as_target_data(self) -> dict:
        """Flatten to the dict shape utils/optout_engine.py automators expect."""
        return {f.field_type: f.value for f in self.fields}


@dataclass
class Broker:
    id: Optional[int] = None
    name: str = ""
    url: str = ""
    opt_out_url: str = ""
    search_url: str = ""
    difficulty: BrokerDifficulty = BrokerDifficulty.STANDARD
    removal_method: str = "automated"
    supports_gdpr: bool = False
    supports_ccpa: bool = True
    notes: str = ""
    handler_module: str = ""
    compliance_email: str = ""
    is_active: bool = True


@dataclass
class ScanResult:
    id: Optional[int] = None
    profile_id: int = 0
    broker_id: int = 0
    scan_type: str = "initial"
    status: ScanStatus = ScanStatus.PENDING
    found: bool = False
    search_url_used: str = ""
    result_url: str = ""
    error_message: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class Removal:
    id: Optional[int] = None
    scan_id: int = 0
    profile_id: int = 0
    broker_id: int = 0
    status: RemovalStatus = RemovalStatus.PENDING
    submission_method: str = ""
    request_id: str = ""
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_reason: str = ""
    retry_count: int = 0
    notes: str = ""


@dataclass
class Evidence:
    """A captured artefact proving what a broker's page showed at a moment.

    `file_hash` is the point of the record. A screenshot with no hash is an
    image someone could have edited; a screenshot plus the SHA256 recorded
    at capture time is something a regulator can be handed.
    """
    id: Optional[int] = None
    removal_id: int = 0
    evidence_type: str = ""
    file_path: str = ""
    file_hash: str = ""
    captured_at: Optional[datetime] = None
    notes: str = ""


EVIDENCE_BEFORE = "before_screenshot"
EVIDENCE_AFTER = "after_screenshot"
EVIDENCE_CONFIRMATION = "confirmation_page"
EVIDENCE_TYPES = (EVIDENCE_BEFORE, EVIDENCE_AFTER, EVIDENCE_CONFIRMATION)


def coerce_difficulty(value: Any) -> BrokerDifficulty:
    """Best-effort read of a difficulty from a DB string or enum.

    Unknown values fall back to STANDARD rather than raising: a broker row
    with a typo'd difficulty should still be scannable, just not trusted to
    skip the queue.
    """
    if isinstance(value, BrokerDifficulty):
        return value
    try:
        return BrokerDifficulty(str(value).strip().lower())
    except ValueError:
        return BrokerDifficulty.STANDARD
