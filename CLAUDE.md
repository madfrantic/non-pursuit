# Non-Pursuit — Agent Instructions

Local-first CCPA compliance engine: generates binding data-broker demand letters and tracks
45-day statutory enforcement deadlines. Streamlit app (`app.py`, `components/`) backed by
`utils/` (broker freshness checks, exposure store, mailto/letter builders, calendar export,
SQLite tracker) with a pytest suite in `tests/`.

`vox-director/` at the repo root is an unrelated nested git repo (video-generation pipeline) —
ignored by this repo's `.gitignore`; don't treat it as part of this app.

## Broker agent (merged from `chino/`, 2026-08-22)

The standalone PySide6 "DataBroker Agent" (`chino/GLM.py`) was merged in and removed. Its Qt UI
was discarded; the headless engine became:

- `utils/broker_agent_models.py` — enums + dataclasses. chino's `QueueReason` is mapped onto
  `review_queue`'s existing closed vocabulary; `other` is deliberately unmappable.
- `utils/broker_ledger.py` — profiles, `agent_brokers`, scans, removals, evidence,
  `scheduled_tasks`, `activity_log`, all inside `data/tracker.db` via `PRAGMA table_info`
  migrations. Per-call connections, never a held one (the scheduler thread reaches it).
- `utils/agent_engines.py` — `ScannerEngine`, `RemovalPlanner`, `VerificationEngine`, wired to
  the real `broker_probe` rather than chino's stub handlers.
- `utils/agent_scheduler.py` — APScheduler `BackgroundScheduler`. **Scans and verifies only.**
- `utils/database.py` — `VaultManager` (PBKDF2-HMAC-SHA256, random per-install salt) as an
  overlay over the legacy `.env` key, with decrypt fallback so pre-vault rows stay readable.
- `utils/optout_engine.py` — evidence capture + SHA256 chain, CAPTCHA detection, difficulty taxonomy.
- `pages/` — Streamlit MPA (Vault, Scanner, Queue, Evidence, Removals, Verification, Reports,
  Settings). Every page must call `components/page_shell.setup()`, which enforces the vault gate.

**Two invariants to preserve.** Automation never submits an opt-out — `RemovalPlanner` opens
records as `REQUIRES_MANUAL` and queues the human step; chino's autonomous submit path was
deliberately not ported. And a verification that could not be made is recorded as `None`
(unknown), never `False`, so a blocked re-scan can't become manufactured evidence of
non-compliance.

## OSINT tool catalog (added 2026-08-23)

A reference index of OSINT tools and datasets merged from ten upstream sources, surfaced as
the `OSINT Toolkit` page. Read-only reference material — it scans nothing and touches no
identity data.

- `importers/` — one module per upstream, each exposing `fetch(client) -> list[dict]`. They
  deduplicate nothing and write nothing; merging and persistence happen once, in the runner.
  `_http.HttpClient` owns the per-host rate limit and GitHub's 403/Retry-After handling, so a
  new source cannot forget to be polite.
- `utils/osint_catalog.py` — `Catalog.merge_or_create_tool()`, URL normalisation, the
  difflib name fallback, and the JSON reader/writer. Tool ids are UUID5 over the normalised
  URL, which is what makes a re-import idempotent rather than duplicative.
- `scripts/import_osint_tools.py` — the runner. Per-source error isolation; refuses to write
  an empty catalog over a good one; `--limit` / `--only` / `--dry-run` for development.
- `data/osint_catalog.json` — the output, and **committed, unlike the rest of `data/`**. It
  holds no personal data and carries no ShareAlike obligation (unlike `data/wmn-data.json`),
  and shipping it means the page works on a clean checkout without a five-minute import.

**Three things to preserve.** The catalog stays out of `tracker.db` — public reference data
must not inherit the retention rules and migration path written for the user's case file.
Shodan, Censys, Maltego and Epieos stay hardcoded in `importers/static_entries.py`; their
terms forbid scraping, so the honest options were a fixed entry or omission. And `provenance`
must keep recording which source supplied each field — a merged row spanning seven upstreams
is otherwise an unattributable claim.

## Workflow governance: apply `power-workflow`

For any non-trivial work in this repo (new features, refactors, batch/automation scripts,
multi-file debugging) — follow the **`power-workflow`** skill
(`.claude/skills/power-workflow/SKILL.md`) end to end: model triage → interview → spec →
(parallel sub-agents where the task splits cleanly) → pre-execution testing → iteration
guardrail → dual-verification → reporting protocol. Don't skip Phase 2 (spec approval) on
anything touching letter-generation logic, deadline math, or the database schema.

**Human Validation Zones for this project** (mandatory manual sign-off, per Phase 4):
- Any change to demand-letter content or templates (`components/letters.py`) — legally binding text.
- Any change to statutory deadline calculation (`utils/broker_freshness.py`, `utils/tracker.py`, `utils/calendar_export.py`).
- Any migration or schema change touching `data/tracker.db` (gitignored — contains real broker requests and exposure data).
- Anything that could write PII to logs (`utils/applog.py`) — `logs/` is gitignored for this exact reason; treat log content as sensitive even locally.
- Any change to the vault key derivation or the legacy-decrypt fallback (`utils/database.py`) — getting this wrong makes existing encrypted rows permanently unreadable.
- Any change that would let unattended code submit an opt-out (`utils/agent_engines.py`, `utils/agent_scheduler.py`).

**Automation gate (Phase 8):** broker-freshness checks and data exports are objective/quantifiable
— safe to automate. Letter copy and exposure-summary language are subjective/legal — augment
only, human reviews before send.

Run `pytest` (see `tests/`) as the rule-based verification step (Phase 6A) before declaring any
change to `utils/` or `components/` complete.
