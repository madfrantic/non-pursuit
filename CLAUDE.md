# Non-Pursuit — Agent Instructions

Local-first CCPA compliance engine: generates binding data-broker demand letters and tracks
45-day statutory enforcement deadlines. Streamlit app (`app.py`, `components/`) backed by
`utils/` (broker freshness checks, exposure store, mailto/letter builders, calendar export,
SQLite tracker) with a pytest suite in `tests/`.

`vox-director/` at the repo root is an unrelated nested git repo (video-generation pipeline) —
ignored by this repo's `.gitignore`; don't treat it as part of this app.

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

**Automation gate (Phase 8):** broker-freshness checks and data exports are objective/quantifiable
— safe to automate. Letter copy and exposure-summary language are subjective/legal — augment
only, human reviews before send.

Run `pytest` (see `tests/`) as the rule-based verification step (Phase 6A) before declaring any
change to `utils/` or `components/` complete.
