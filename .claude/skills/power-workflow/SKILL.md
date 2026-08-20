---
name: power-workflow
description: Unified execution framework merging Boris Cherny's 4-step governance (model triage, iteration limits, dual verification, exception reporting) with Austin Marchese's 6 Power Phrases (interview, spec, parallel sub-agents, pre-execution testing, skill codification, automation gating). Use when running autonomous tasks, large refactors, batch data workflows, complex builds, parallel audits, codifying new skills, or setting up automations.
---

# Power Workflow: Unified Execution Framework

Merged from three previously separate skills (`agent-governor`, `boss-mode`, `power-workflow`) that overlapped heavily on execution guardrails. Apply all phases below to non-trivial multi-step work — planning phases first, then guardrails during execution, then verification before declaring done.

---

## Phase 0: Model Allocation (Minimum Viable Model)
Determine task complexity before routing, and pick the right brain for the job:
- **Low Complexity** (formatting, linting, basic file edits, easy/small chores, data schema checks): fast/cheap model (e.g. Haiku).
- **Medium Complexity** (script authoring, API connector wrapping, scraper maintenance): balanced model (e.g. Sonnet).
- **High Complexity** (algorithmic regime modeling, architecture design, multi-file system debugging, hard/tricky math or code): advanced reasoning model (e.g. Opus).

---

## Phase 1: Context Extraction ("Interview Me")
Before writing any code or architecture documents:
1. Ask 3 to 5 highly targeted questions covering:
   - **Target User & Scope**: exactly who this is for and what is explicitly *out of scope*.
   - **Input/Output Constraints**: data structures, APIs, dependencies, and file paths.
   - **Edge Cases**: failure modes, rate limits, and missing data handling.
2. Synthesize answers into a concise context block before proceeding.

---

## Phase 2: Spec Formulation ("Write Me an Implementation Spec")
Draft a strict, unambiguous implementation specification containing:
- **Core Objective**: 1–2 sentence summary.
- **Architectural Steps**: step-by-step breakdown (single clear execution path to eliminate assumptions).
- **Key Decision Points**: explicit choices made for libraries, data handling, and state management.
- **Halt for Approval**: prompt the user to approve the spec before generating executable code.

---

## Phase 3: Parallel Execution ("Launch Sub-Agents")
When handling multi-faceted tasks (code reviews, multi-asset data parsing, competitor audits):
- Decompose the problem into independent, isolated sub-tasks.
- Assign dedicated sub-agent contexts to each branch (e.g. Security, Performance, Edge Cases) so findings do not anchor or bias one another.
- Consolidate sub-agent results into a single structured summary.

---

## Phase 4: Pre-Execution Testing ("Verify Before You Build")
1. **Verification Tooling**: define *how* output will be proven functional (e.g. pytest, syntax linter, schema validator, live preview) before writing implementation code.
2. **Human Validation Zones (HVZ)**: identify critical high-cost-of-error areas (financial sizing, auth, deletions, database migrations) and mandate explicit manual sign-off.

---

## Phase 5: Iteration Guardrail (Don't Get Stuck)
- Hard ceiling of **`max_iterations = 10`** on all self-correcting or recursive tool loops, including automated sub-agent routines.
- If a solution is not converging by iteration 7, **stop** — halt execution, preserve state, and raise an **Exception Flag** to the user with specific failure points, rather than running in circles.

---

## Phase 6: Dual-Verification Gate (Check Your Own Homework)
Before declaring any task complete, run two checks:

### A. Rule-Based Verification (objective pass/fail)
- **Code/Data**: syntax validation, static typing checks, schema compliance, unit tests. Did you break any code or make typos?
- **File System**: ensure no dangling temporary files or corrupted states exist.
- **Requirement**: must score 100% pass — zero unresolved runtime errors.

### B. Taste-Based Verification (anti-slop standard)
- **Concision**: strip boilerplate preamble, redundant commentary, and generic conversational padding. Is the writing clean and brief? Delete any boring fluff.
- **Direct Deliverable**: ensure code, copy, or data structures match professional production standards.
- **Architectural Cleanliness**: verify functions are modular (<50 lines where feasible), explicit, and maintainable.

---

## Phase 7: Codification & Evolution ("Build Me a Skill + Gotchas")
When codifying completed conversations or workflows into new `SKILL.md` files:
1. Base the skill strictly on proven, executed conversational outputs (concrete over abstract).
2. **Mandatory Gotchas Section**: every generated skill must end with an explicit `## Gotchas` log documenting failure modes, edge cases, and stylistic constraints discovered during the session.

---

## Phase 8: Automation Gate ("Automate vs. Augment")
Apply the dual-filter check before proposing any unattended loops or scheduled routines:
1. **The Taste Test**: is output objective/quantifiable? → candidate for automation. Is it subjective/aesthetic/creative? → augment only (require human review).
2. **The 80/20 Rule**: is 80% baseline output acceptable? → automate. Does it require 100% precision? → retain a human checkpoint.

---

## Reporting Protocol (Only Bug Me If Something Breaks)
- **On Success**: give only the minimal executive summary — files changed, tests passed, and the next direct command/artifact (a 2-line summary is fine for small tasks).
- **On Failure / Exception**: immediately output:
  1. Root cause of failure.
  2. Iteration count reached.
  3. Precise blocking line or dependency.
  4. Suggested remediation path.

---

## Gotchas & Anti-Patterns
- **Never skip Phase 2 (Spec)**: starting code without an approved spec leads to assumption compounding.
- **No speculative skills**: never create an abstract skill without having executed the workflow manually at least once.
- **Guard against runaway loops**: always attach explicit iteration limits (`max_iterations = 10`) to automated sub-agent routines.
