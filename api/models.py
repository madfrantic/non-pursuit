"""Request and response shapes for the compliance API.

Pydantic models rather than bare dicts for one reason that matters more than
tidiness: every field here carries PII, and the models are where the bounds
on it live. A handle is length-capped, a scan's site count is capped, and
concurrency has a ceiling -- so a malformed or hostile request is rejected at
the edge instead of turning into three thousand outbound HTTP requests.
"""
import sys
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

_UTILS_DIR = Path(__file__).resolve().parent.parent / "utils"
if str(_UTILS_DIR) not in sys.path:
    sys.path.append(str(_UTILS_DIR))

import remediation  # noqa: E402 - see _UTILS_DIR bootstrap above

MAX_HANDLE = 64
MAX_SITES = 4000

# One Enum, built from remediation.ALL_TEMPLATES -- the dict remediation and
# letter_compiler already treat as canonical -- rather than a second,
# hand-written list of template names that could drift out of sync with the
# real one. See remediation.ALL_TEMPLATES for why that dict exists.
TemplateType = Enum(
    "TemplateType",
    {template_type.upper(): template_type for template_type in remediation.ALL_TEMPLATES},
    type=str,
)


class Subject(BaseModel):
    """The person a scan is about.

    Every field is optional except the handle, because a scan can run on a
    handle alone -- but the report says out loud that an unseeded scan's
    confidence scores are much weaker, and these fields are what turns that
    around. They are the identity seed the graph corroborates against.
    """

    handle: str = Field(..., min_length=1, max_length=MAX_HANDLE,
                        description="Username to search for across platforms.")
    name: str = Field("", max_length=200)
    email: EmailStr | None = None
    location: str = Field("", max_length=200,
                          description="Free text, e.g. 'Austin, TX'.")
    city: str = Field("", max_length=100)
    state: str = Field("", max_length=64,
                       description="Two-letter code for US states; routes CCPA/NY templates.")
    country: str = Field("", max_length=64,
                         description="ISO 3166-1 alpha-2; an EU/EEA/UK code routes GDPR.")
    extra_handles: list[str] = Field(default_factory=list, max_length=20)
    extra_emails: list[EmailStr] = Field(default_factory=list, max_length=20)

    @field_validator("handle")
    @classmethod
    def handle_is_plausible(cls, value: str) -> str:
        value = value.strip()
        # A handle containing whitespace or a slash is almost always a pasted
        # URL or a full name. Both produce a scan of 3000 guaranteed misses.
        if not value or any(c in value for c in " \t/\\?#"):
            raise ValueError("handle must be a bare username, not a URL or a full name")
        return value

    def profile(self) -> dict:
        """The dict shape jurisdiction_router and letter_compiler expect."""
        return {
            "name": self.name,
            "email": str(self.email) if self.email else "",
            "location": self.location or ", ".join(filter(None, [self.city, self.state])),
            "state": self.state,
            "country": self.country,
        }


class ScanOptions(BaseModel):
    include_nsfw: bool = Field(
        False, description="Adult sites stay excluded unless explicitly requested.")
    categories: list[str] = Field(default_factory=list, max_length=40)
    sources: list[str] = Field(
        default_factory=list,
        description="Restrict to whatsmyname / sherlock / maigret. Empty means all.")
    max_sites: int = Field(600, ge=1, le=MAX_SITES,
                           description="Cap on sites probed, highest-trust source first.")
    concurrency: int = Field(50, ge=1, le=200)
    timeout: int = Field(15, ge=1, le=60)
    probe_brokers: bool = True
    discover_contacts: bool = True
    min_confidence: float = Field(
        75.0, ge=0, le=100,
        description="Confidence at or above which a deletion demand is drafted.")


class ScanRequest(BaseModel):
    subject: Subject
    options: ScanOptions = Field(default_factory=ScanOptions)


class JobRef(BaseModel):
    job_id: str
    status: str
    submitted_at: str
    detail: str = ""


class JobStatus(JobRef):
    progress: dict = Field(default_factory=dict)
    error: str = ""


class ExposureRecord(BaseModel):
    """One scored account, handed back to /api/remediate for a demand.

    The client sends back rows it got from /api/scan, minus the ones the
    person looked at and disowned. Confidence is accepted as given rather
    than recomputed: the scan that produced it had the whole graph in hand,
    and re-deriving a number here from a single row would produce a
    different, worse one under the same name.
    """

    platform: str = Field(..., min_length=1, max_length=200)
    handle: str = Field("", max_length=MAX_HANDLE)
    url: str = Field("", max_length=2000)
    confidence: float = Field(0.0, ge=0, le=100)
    band: str = Field("", max_length=16)
    verdict: str = Field("", max_length=32)
    evidence: list[dict] = Field(default_factory=list, max_length=50)
    metadata: dict = Field(default_factory=dict)


class RemediateRequest(BaseModel):
    """Draft demands for exposures a person has already reviewed.

    Separate from ScanRequest because the two are different acts: a scan
    looks, and this writes letters in someone's name. Splitting them is what
    lets the person stand between the two.
    """

    subject: Subject
    exposures: list[ExposureRecord] = Field(default_factory=list, max_length=500)
    min_confidence: float = Field(
        75.0, ge=0, le=100,
        description="Exposures below this are left out of the demand set.")
    discover_contacts: bool = Field(
        True, description="Look up privacy contacts for platforms not already curated.")
    probe_brokers: bool = Field(
        False, description="Also query data brokers for records on this subject.")
    include_manual_brokers: bool = Field(
        True, description="Keep brokers whose search cannot be automated, marked as such.")

    @model_validator(mode="after")
    def has_something_to_act_on(self) -> "RemediateRequest":
        # An empty exposure list with broker probing off renders nothing and
        # returns a report reading as an all-clear -- a clean bill of health
        # for a question nobody asked. Reject it at the edge instead.
        if not self.exposures and not self.probe_brokers:
            raise ValueError(
                "nothing to remediate: send at least one exposure, "
                "or set probe_brokers to search data brokers")
        return self


class OptOutRequest(BaseModel):
    """One broker opt-out form to fill.

    Bounded like every other model here: this is the identity the automator
    types into a third party's form, so the edge is where its size is fixed.
    `email` is where the broker's confirmation link lands -- use a masked
    address, not the subject's real inbox.
    """

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    city: str = Field("", max_length=100)
    state: str = Field("", max_length=64)
    age: str = Field("", max_length=3, description="Some brokers disambiguate on age.")
    email: EmailStr
    broker: str = Field(..., min_length=1, max_length=64,
                        description="Broker id; must be one the engine has an automator for.")
