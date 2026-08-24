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

## Presentation layer and the owner console (2026-08-24)

The app, the pitch deck and the exported PDFs had drifted onto three different
palettes and two different product names. They are now one document.

- `components/theme.py` — the stylesheet, tokens lifted unchanged from
  `demo_pitch.html`'s `:root` (navy/slate/brass/bone, stamp red kept scarce).
  Injected once per script run from `app.py` and `components/page_shell.setup()`;
  a no-op under `NON_PURSUIT_DEBUG_UNSTYLE`, which exists to strip styling.
  It carries **no** `prefers-color-scheme` or `[data-theme]` branch on purpose —
  paired with the `[theme]` block now in `.streamlit/config.toml`, that is what
  stopped the page changing palette when the OS did. `config.py`,
  `.streamlit/config.toml` and `theme.py` all name the same colours and
  `test_the_pinned_theme_matches_the_stylesheet_and_config` fails if they drift.
- **Legibility rules the type layer** (2026-08-24, after the interface was
  reported as blocky and hard to read for older eyes). Monospace is reserved for
  code and the deadline clock; it used to run the whole label layer, and
  monospace + UPPERCASE + `.14–.2em` tracking at 11px stacked three legibility
  costs on the smallest text on screen. Body is 16px, nothing renders below 14px,
  tracking is capped at `.06em`, and uppercase survives only on the sidebar's own
  section headers. `--np-bone-dim` was lifted to `#B4BDCA` (9.8:1 on navy) for
  age-related contrast sensitivity. Tests in `tests/test_branding_theme.py`
  enforce all five limits — the serif/brass character lives in the headings and
  palette, not in shrinking the labels.
- `config.APP_HEADLINE` — "non-pursuit. the sovereign engine", lowercase, the
  single headline. It replaced the deck's "Take yourself off the market." and the
  footer's "They chase. You enforce."; `st.logo` now draws the shield
  (`APP_TOPMARK_PATH`) rather than the wordmark, which repeated in a picture what
  the headline says in words.
- `runtime_mode.apply_startup_default()` — a fresh session opens on the **online**
  shape, because the desktop build is a separate install. It is not the same thing
  as a user choosing: `override_is_explicit()` keeps the badge from claiming
  somebody forced the switch, and re-applying never overrides a later choice —
  including a deliberate return to Auto-detect. Auto-detection itself is unchanged.
  Called from **both** entrypoints; a `pages/` script opened by URL never runs
  `app.py`, and one entrypoint seeding the default meant one session with two names
  for the same section.
- `components/nav.py` — `MPA_SECTIONS` now holds section *keys*, resolved by
  `section_label()`. The engine section is "Sovereign Engine" online and
  "Broker agent console" on the desktop runtime, which is the only build that
  ships that console.
- `utils/admin_auth.py` + `components/admin_dashboard.py` + `pages/11_Admin.py` —
  the owner console: every stored record in one view. Gated on
  `NON_PURSUIT_ADMIN_KEY` (env or `st.secrets`), compared with
  `hmac.compare_digest` over SHA-256 digests; the key never enters session state.
  With no key configured the feature is absent, not merely locked.

**Three things to preserve.** The console reads and never writes — a screen that
aggregates every record is the worst place for a destructive control, and each of
those actions already has a home on the page that owns that data. Its sidebar link
is dropped for non-owners but that is not the check: `pages/11_Admin.py` enforces
the gate itself, because anyone can type the URL. And the admin page stays listed
in `MPA_SECTIONS` — that table is the page registry, so hiding the link by deleting
the entry would make the page unreachable rather than private.

**Page import order is load-bearing.** `page_shell.setup()` is what puts `utils/`
on `sys.path`, so a page must import it *first* and import flat `utils/` modules
only after that call. `tests/conftest.py` adds `utils/` for the whole suite, which
hides the failure — `test_every_page_imports_cleanly_without_the_app_having_run_first`
re-runs each page in a clean subprocess for exactly that reason.

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
