"""
FastAPI surface over the discovery -> scoring -> remediation pipeline.

The modules under utils/ each do one thing well and none of them know about
each other's transport. This is the seam that runs them in order:

    site_registry  ->  recon_engine  ->  identity_graph  ->  remediation
                       infra_checker      privacy_contacts
                       broker_probe

WHAT THIS API DOES NOT DO

It does not send anything. There is no dispatch route, no SMTP client, and
no write to the campaign ledger. Every demand comes back as a rendered
payload with `requires_human_signoff` and `ready_to_send` on it, and the
person whose name is on the letter is the one who decides. That is the
Human Validation Zone from CLAUDE.md expressed as an API shape rather than
as a comment: the pipeline can draft an erasure demand end to end, and it
still cannot put one in the post.

CONCURRENCY

A scan is minutes of network I/O, so /api/scan defaults to returning a
JobRef immediately and running the work as a background task; `wait=true`
runs it inline for small scans and for tests. The job store is an in-process
dict -- it holds PII, so it is deliberately not a database, is capped, ages
out, and does not survive a restart. Under multiple uvicorn workers a job
started on one worker is invisible to the others; run one worker, or move
the store to Redis before you scale it.

PHASING

Phase 1 gathers the account scan and the broker probes concurrently -- they
share nothing and neither needs the other's output. Infrastructure checks
are NOT in that gather, and not because they could not be: they run in phase
2, against only the hosts where an exposure was actually confirmed. Checking
all 600 candidate sites would mean 600 RDAP queries and 600 TLS handshakes
to learn about hosts that turned out to hold nothing.
"""
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT_DIR = Path(__file__).resolve().parent.parent
UTILS_DIR = ROOT_DIR / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.append(str(UTILS_DIR))

import broker_probe                                    # noqa: E402
import identity_graph                                  # noqa: E402
import infra_checker                                   # noqa: E402
import metadata_extractor                              # noqa: E402
import privacy_contacts                                # noqa: E402
import recon_engine                                    # noqa: E402
import remediation                                     # noqa: E402
import site_registry                                   # noqa: E402
from applog import get_logger                          # noqa: E402

from api.models import (                               # noqa: E402
    JobRef, JobStatus, RemediateRequest, ScanRequest, TemplateType,
)

_log = get_logger("api")

API_VERSION = "1.0.0"

# Explicit origins, not "*". Every response on this API carries somebody's
# exposure map, and a wildcard would let any page the user has open read it
# from a browser that can reach localhost.
DEFAULT_ORIGINS = (
    "http://localhost:8501", "http://127.0.0.1:8501",   # Streamlit app
    "http://localhost:3000", "http://127.0.0.1:3000",
)

# Jobs age out rather than accumulating: a finished job holds a full
# exposure report in memory.
JOB_TTL_SECONDS = 3600
MAX_JOBS = 64

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def cors_origins() -> list:
    configured = os.getenv("NONPURSUIT_CORS_ORIGINS", "").strip()
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return list(DEFAULT_ORIGINS)


def _template_environment() -> Environment:
    """Read-only view of the statutory templates, for /api/templates.

    Letters are rendered by remediation, which owns its own Environment --
    this one exists so the API can list and preview what is on disk without
    becoming a second renderer. Two environments rendering legally binding
    text is how the two drift apart.
    """
    return Environment(
        loader=FileSystemLoader(str(remediation.TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the site registry once, from cache, without reaching the network.

    A registry fetch on startup would make the API's readiness depend on
    three GitHub raw URLs. It loads what is cached and reports the coverage
    it actually has; /api/sites?refresh=true is the deliberate way to update.
    """
    app.state.started_at = datetime.now(timezone.utc).isoformat()
    app.state.jobs = {}
    app.state.templates = _template_environment()
    app.state.registry, app.state.registry_status = {}, "unavailable"
    app.state.registry_error = ""

    try:
        registry, status = await asyncio.to_thread(
            site_registry.ensure_registry, str(ROOT_DIR / "data"), False)
        app.state.registry, app.state.registry_status = registry, status
    except Exception as exc:  # noqa: BLE001 - the API still serves without it
        app.state.registry_error = str(exc)
        _log.warning("Site registry unavailable at startup: %s", exc)

    try:
        app.state.brokers = await asyncio.to_thread(
            broker_probe.build_registry,
            str(ROOT_DIR / "data" / "brokers.csv"),
            str(ROOT_DIR / "data" / "broker_probes.json"))
    except Exception as exc:  # noqa: BLE001
        app.state.brokers = []
        _log.warning("Broker registry unavailable at startup: %s", exc)

    _log.info("API up: %s sites, %s brokers",
              len(app.state.registry.get("sites", [])), len(app.state.brokers))
    yield
    app.state.jobs.clear()


app = FastAPI(
    title="Non-Pursuit Compliance API",
    version=API_VERSION,
    description="Discovery, identity scoring, and statutory demand drafting. "
                "Drafts only -- this API never sends.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,   # no cookies or auth headers; nothing to carry
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ----------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reap_jobs(jobs: dict) -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - JOB_TTL_SECONDS
    for job_id, job in list(jobs.items()):
        if job.get("_created", 0) < cutoff and job["status"] in (STATUS_DONE, STATUS_FAILED):
            jobs.pop(job_id, None)
    while len(jobs) > MAX_JOBS:
        oldest = min(jobs, key=lambda k: jobs[k].get("_created", 0))
        jobs.pop(oldest, None)


def _registry_or_503():
    registry = getattr(app.state, "registry", {}) or {}
    if not registry.get("sites"):
        raise HTTPException(
            status_code=503,
            detail="Site registry unavailable. POST /api/sites?refresh=true to build it, "
                   f"or check data/sites-unified.json. ({app.state.registry_error})")
    return registry


def _select_sites(registry: dict, request: ScanRequest) -> list:
    sources = tuple(request.options.sources) or site_registry.SOURCE_PRECEDENCE
    sites = site_registry.select_sites(
        registry,
        include_nsfw=request.options.include_nsfw,
        sources=sources,
        categories=request.options.categories or None,
        account=request.subject.handle,
    )
    # Trim highest-trust-source-first so a capped scan drops the least
    # reliable entries rather than an arbitrary tail.
    order = {name: index for index, name in enumerate(site_registry.SOURCE_PRECEDENCE)}
    sites.sort(key=lambda s: order.get(s.get("source"), len(order)))
    return sites[: request.options.max_sites]


def _registry_mtime() -> str:
    path = ROOT_DIR / "data" / "sites-unified.json"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return ""


def _hosts_of(exposures: list) -> list:
    hosts = []
    for row in exposures:
        host = privacy_contacts.host_of(row.get("url") or row.get("profile_url", ""))
        if host:
            hosts.append(host)
    return hosts


# ------------------------------------------------------------- the pipeline

async def run_scan(request: ScanRequest, *, progress=None) -> dict:
    """The whole pipeline for one subject. See the module docstring's PHASING."""
    def _tick(stage, **extra):
        if progress is not None:
            progress.update({"stage": stage, **extra})

    registry = _registry_or_503()
    sites = _select_sites(registry, request)
    subject, options = request.subject, request.options
    profile = subject.profile()
    _tick("scanning", sites=len(sites))

    broker_subject = {
        "name": profile.get("name", ""),
        "city": subject.city,
        "state": subject.state,
        "location": profile.get("location", ""),
    }

    # Phase 1: accounts and brokers, concurrently. Neither reads the other.
    scan_task = recon_engine.scan(
        subject.handle, sites,
        concurrency=options.concurrency, timeout=options.timeout)
    broker_task = (
        broker_probe.probe_brokers(broker_subject, getattr(app.state, "brokers", None))
        if options.probe_brokers and broker_subject["name"]
        else _nothing()
    )
    scan_rows, broker_rows = await asyncio.gather(scan_task, broker_task)
    _tick("scoring", probed=len(scan_rows), brokers=len(broker_rows))

    # Scoring is CPU-bound and pure; no reason for it to be async.
    graph = identity_graph.build_from_scan(
        scan_rows,
        metadata_extractor.build_link_resolver(sites),
        seed_handles=[subject.handle, *subject.extra_handles],
        seed_emails=[str(e) for e in ([subject.email] if subject.email else [])
                     + list(subject.extra_emails)],
        seed_names=[subject.name] if subject.name else None,
        seed_locations=[profile["location"]] if profile.get("location") else None,
    )
    scored = graph.score()

    exposures = [row for row in scored["accounts"]
                 if row["confidence"] >= options.min_confidence]
    _tick("resolving", exposures=len(exposures))

    # Phase 2: contacts and infrastructure, concurrently, for confirmed
    # exposures only.
    contacts_task = (
        privacy_contacts.resolve_many(exposures, discover_missing=True)
        if options.discover_contacts and exposures else _nothing(dict)
    )
    infra_task = (
        infra_checker.check_hosts(_hosts_of(exposures))
        if exposures else _nothing()
    )
    contacts, infra = await asyncio.gather(contacts_task, infra_task)
    _tick("drafting", contacts=len(contacts), hosts=len(infra))

    report = remediation.build_compliance_report(
        scored, broker_rows, profile, contacts,
        min_confidence=options.min_confidence)
    report["infrastructure"] = {
        "hosts": infra,
        "summary": infra_checker.summarize(infra),
        "operator_groups": infra_checker.group_by_operator(infra),
    }
    report["scan"] = {
        "sites_selected": len(sites),
        "sites_probed": len(scan_rows),
        "registry_status": app.state.registry_status,
        **recon_engine.summarize(scan_rows),
    }
    _tick("done")
    return report


async def _nothing(kind=list):
    return kind()


async def _run_job(job_id: str, request: ScanRequest) -> None:
    job = app.state.jobs[job_id]
    job["status"] = STATUS_RUNNING
    try:
        job["result"] = await run_scan(request, progress=job["progress"])
        job["status"] = STATUS_DONE
    except asyncio.CancelledError:
        job["status"] = STATUS_FAILED
        job["error"] = "cancelled"
        raise
    except HTTPException as exc:
        job["status"], job["error"] = STATUS_FAILED, str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        _log.exception("Scan job %s failed", job_id)
        # The message, not the traceback: a scan failure can carry the
        # handle and the URL that broke it, and this string is user-facing.
        job["status"], job["error"] = STATUS_FAILED, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------- routes

@app.get("/health")
async def health() -> dict:
    """Liveness plus the degradations that change what results mean."""
    registry = getattr(app.state, "registry", {}) or {}
    warnings = []
    if not registry.get("sites"):
        warnings.append("site registry not loaded; /api/scan will return 503")
    if infra_checker._dns_resolver is None:
        warnings.append("dnspython missing; DNS checks limited to A/AAAA")
    if infra_checker._whois is None:
        warnings.append("python-whois missing; registration data is RDAP-only")
    if registry.get("incomplete_sources"):
        warnings.append(
            "registry built without: " + ", ".join(registry["incomplete_sources"]))

    return {
        "status": "degraded" if warnings else "ok",
        "version": API_VERSION,
        "started_at": app.state.started_at,
        "sites": len(registry.get("sites", [])),
        "brokers": len(getattr(app.state, "brokers", [])),
        "registry_status": app.state.registry_status,
        "jobs": len(getattr(app.state, "jobs", {})),
        "warnings": warnings,
    }


@app.get("/api/sites")
async def sites(refresh: bool = Query(False, description="Re-fetch upstream site lists."),
                source: str = Query("", description="Filter coverage to one source.")) -> dict:
    """Platform coverage: how many sites, from where, in what categories."""
    if refresh:
        try:
            registry, status = await asyncio.to_thread(
                site_registry.ensure_registry, str(ROOT_DIR / "data"), True)
            app.state.registry, app.state.registry_status = registry, status
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502,
                                detail=f"registry refresh failed: {exc}") from exc

    registry = getattr(app.state, "registry", {}) or {}
    entries = registry.get("sites", [])
    if source:
        entries = [s for s in entries if s.get("source") == source]

    by_source, by_category = {}, {}
    for entry in entries:
        by_source[entry.get("source", "")] = by_source.get(entry.get("source", ""), 0) + 1
        category = entry.get("cat", "uncategorised")
        by_category[category] = by_category.get(category, 0) + 1

    return {
        "total": len(entries),
        "by_source": dict(sorted(by_source.items())),
        "by_category": dict(sorted(by_category.items(),
                                   key=lambda kv: -kv[1])[:25]),
        "nsfw_excluded_by_default": sum(
            1 for s in entries if s.get("cat") == site_registry.NSFW_CATEGORY),
        "registry_status": app.state.registry_status,
        "schema_version": registry.get("schema_version", ""),
        # The registry payload carries no build timestamp, so the cache
        # file's mtime is the honest answer to "how stale is this coverage".
        "cached_at": _registry_mtime(),
        "incomplete_sources": registry.get("incomplete_sources", []),
        "attribution": site_registry.attribution(),
    }


@app.post("/api/scan")
async def scan(request: ScanRequest,
               wait: bool = Query(False, description="Run inline and return the report.")):
    """Discover, score, and draft. Returns a JobRef unless `wait` is set."""
    _registry_or_503()

    if wait:
        return await run_scan(request)

    jobs = app.state.jobs
    _reap_jobs(jobs)
    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "job_id": job_id, "status": STATUS_QUEUED, "submitted_at": _now(),
        "progress": {"stage": STATUS_QUEUED}, "error": "", "result": None,
        "_created": datetime.now(timezone.utc).timestamp(),
    }
    # Held so the task is not garbage-collected mid-flight.
    jobs[job_id]["_task"] = asyncio.create_task(_run_job(job_id, request))

    return JobRef(job_id=job_id, status=STATUS_QUEUED, submitted_at=jobs[job_id]["submitted_at"],
                  detail="GET /api/jobs/{job_id} for status, /api/jobs/{job_id}/result "
                         "when done.")


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str) -> JobStatus:
    job = app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown or expired job")
    return JobStatus(job_id=job["job_id"], status=job["status"],
                     submitted_at=job["submitted_at"], progress=job["progress"],
                     error=job["error"])


@app.get("/api/jobs/{job_id}/result")
async def job_result(job_id: str) -> dict:
    job = app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown or expired job")
    if job["status"] == STATUS_FAILED:
        raise HTTPException(status_code=500, detail=job["error"])
    if job["status"] != STATUS_DONE:
        # 409, not 404: the job exists and the answer is "not yet".
        raise HTTPException(status_code=409,
                            detail=f"job is {job['status']}; no result yet")
    return job["result"]


@app.post("/api/remediate")
async def remediate(request: RemediateRequest) -> dict:
    """Draft demand letters for exposures a person has already reviewed."""
    profile = request.subject.profile()
    exposures = [row.model_dump() for row in request.exposures
                 if row.confidence >= request.min_confidence]

    broker_rows = []
    if request.probe_brokers and profile.get("name"):
        broker_rows = await broker_probe.probe_brokers(
            {"name": profile["name"], "city": request.subject.city,
             "state": request.subject.state, "location": profile.get("location", "")},
            getattr(app.state, "brokers", None))

    contacts = {}
    if exposures:
        contacts = await privacy_contacts.resolve_many(
            exposures, discover_missing=request.discover_contacts)

    scored = {"accounts": exposures, "seeded": bool(profile.get("name") or profile.get("email")),
              "summary": identity_graph.summarize(exposures) if exposures else {}}
    report = remediation.build_compliance_report(
        scored, broker_rows, profile, contacts,
        min_confidence=request.min_confidence)
    report["dispatch"] = {
        "sent": 0,
        "note": "This API drafts and never sends. Review each payload, then "
                "dispatch through the Campaign Tracker.",
    }
    return report


@app.get("/api/templates")
async def templates() -> dict:
    """The statutory templates on disk, and which carry human sign-off.

    remediation.ALL_TEMPLATES is the one type<->filename mapping this
    reads -- the same dict api.models.TemplateType is built from -- so a
    filename here resolves to exactly one template type, with no separate
    lookup table of our own that could quietly drift out of step with it.
    """
    available = sorted(app.state.templates.list_templates())
    type_of_file = {filename: template_type
                    for template_type, filename in remediation.ALL_TEMPLATES.items()}

    entries = []
    for name in available:
        template_type = type_of_file.get(name, "")
        signed_off = template_type in remediation.SIGNED_OFF_TEMPLATES
        entries.append({
            "name": name,
            "template_type": TemplateType(template_type).value if template_type else "",
            "signed_off": signed_off,
            "requires_human_signoff": not signed_off,
        })

    return {
        "templates": entries,
        "directory": str(remediation.TEMPLATES_DIR),
        "note": "Sign-off is a human's decision recorded in "
                "remediation.SIGNED_OFF_TEMPLATES, not a property of the file.",
    }
