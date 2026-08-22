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
