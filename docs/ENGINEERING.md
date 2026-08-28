# Fencing an Autonomous Agent

### Notes on shipping 38,000 lines of agent-written Python into a domain where being wrong is a legal problem

---

Non-Pursuit is a local-first privacy enforcement engine: it locates a person's
exposed personal data, drafts the statutory deletion demand that applies in the
relevant jurisdiction, and tracks the response window the law gives the
recipient. It is ~38,000 lines of Python — Streamlit, SQLite, FastAPI,
Playwright, Jinja2 — and the large majority of it was written by an AI coding
agent running autonomously in a terminal.

That second fact is not the interesting one. Plenty of people shipped
agent-written code this year. The interesting question is the one that follows
it: **when the agent writes a function that computes a legal deadline, what
stops a wrong answer from reaching a user?**

Test coverage is the reflexive answer and it is not sufficient. This is an
account of what I built instead, and where it held.

---

## 1. Tests do not catch drift

The campaign tracker computes when a data broker's response is due under CCPA
§ 1798.130(a)(2). The convention — day 45 compliant, day 46 overdue — is a
boundary I checked by hand and signed off manually. The project's `CLAUDE.md`
designates that math a **Human Validation Zone**: the agent may touch it, but
no change lands without a human re-reading the boundary against the statute.

Later, a second deadline implementation appeared in a different module,
`remediation.deadline_for()`. Its arithmetic was correct. Its boundary matched.
Every test passed, and every test would have kept passing.

The defect wasn't wrongness — it was that a second, independently derived
deadline now existed for the same campaign. Nothing was wrong *yet*. It would
go wrong the first time either implementation changed and the other didn't, and
the failure would surface as a user being told a demand was still within its
window when it wasn't. No unit test detects that, because at the moment you
write the test both implementations agree.

I could have deleted it. I quarantined it instead: it stays, it is display-only,
it is deliberately wired to nothing that feeds the ledger, and the reason lives
at the definition so the next person — human or agent — cannot re-derive the
mistake:

> *"a second, differently-derived deadline reaching the ledger is exactly the
> drift that convention exists to prevent."*

**The generalizable point:** an agent produces plausible, locally correct code
at a rate that outpaces review. The failures that survive that rate are not
bugs, which tests catch. They are *duplications, drifts, and quiet redefinitions
of things that were already decided* — architectural erosion, which tests are
blind to by construction. Reviewing agent output for correctness is the easy
half. Reviewing it for redundancy is the half that matters.

---

## 2. The agent is not allowed to send anything

The strongest guarantee in this system is a negative one. There is no send path.

`utils/remediation.py` compiles a demand into a payload and stamps it
`requires_human_signoff` unless its template appears in `SIGNED_OFF_TEMPLATES`
— a frozenset a human edits by hand after reading the rendered output. A
template cannot promote itself. `api/main.py` enforces the identical flag on
the HTTP surface, with the reasoning recorded inline: approval is *"a property
of `remediation.SIGNED_OFF_TEMPLATES`, not a property of the file."*

And then the actual gate: grep the repository for an outbound path — `smtplib`,
a `POST` of a compiled demand, anything — and there isn't one. The agent can
draft a legal demand. It cannot deliver one. The terminal step is a person
clicking send in their own mail client, having read the letter.

This is a deliberately unsophisticated control, and that is the argument for
it. A permission system can be misconfigured. A capability that was never built
cannot be enabled by accident, by a prompt injection in scraped page content,
or by a future agent session that doesn't know the rule.

It also disposes of the unauthorized-practice-of-law question before anyone has
to ask it. The software drafts; the user reviews and sends; the software never
corresponds with anyone on the user's behalf.

---

## 3. Where the leak would have been

A design decision I want to keep, because it is the one an agent would not have
made unprompted.

The hosted demo serves many visitors from a single Streamlit process. Every
data store routes through one accessor, `runtime_mode.db_path()`, which returns
the durable database in local mode and a **per-visitor temporary file** in demo
mode, created once per session and held in `session_state`.

The original spec placed the broker campaign ledger in its own `campaigns.db`.
That is a perfectly reasonable schema decision and a data breach: a hardcoded
second path bypasses the accessor, so in demo mode it would have been one
shared file — and one visitor's campaign records would have been served to the
next visitor.

The ledger became a table inside the existing `tracker.db`. One accessor, one
routing decision, no second path to keep in sync.

The class of bug is worth naming: **the agent was reasoning about the schema,
and the vulnerability lived in the deployment topology.** It had no way to see
that a filename in a spec was a multi-tenancy decision. Holding the boundary
between what the agent reasons about and what only the architect can see is,
in practice, most of the job.

---

## 4. What the tests are actually for

The suite is 907 test functions, 1,088 collected cases, ~11,000 lines against
~38,000 lines of source, running on every push and pull request with no
excluded markers.

I want to be precise about what that number does and does not mean, because
"my agent wrote 1,088 tests" is not a claim of quality — a sufficiently
motivated agent will generate a thousand assertions about getters.

The suite's job here is narrow: it is a **regression fence around decisions
already made by a human.** The day-45 boundary is pinned by tests so that a
future agent session cannot quietly move it. The demo/local database routing
boundary is pinned so that a refactor cannot collapse it. Jinja2 rendering is
pinned because a template that silently drops a required statutory clause
produces a letter that looks correct and is legally inert.

Those three areas — deadline math, database routing, template rendering — are
where a silent failure is either a legal problem or a data-exposure problem.
That is where the coverage is deliberately heaviest. The tests do not certify
that the agent's code is right. They certify that the parts a human already
certified stay that way.

---

## 5. Choices I would defend, and one I would not

**SQLite over Postgres.** Not a performance decision — a threat-model decision.
The product's premise is that your PII never leaves your hardware. A database
server is a network service, and a network service is the thing being argued
against. Single user, single writer, no concurrency story required. PII columns
are Fernet-encrypted at rest, with an optional master-password overlay
(PBKDF2-HMAC-SHA256, 480k iterations) that generates 16 random bytes of salt
per install — the predecessor implementation hardcoded one salt into the
source, which is the same salt on every machine and therefore not a salt.

**Streamlit over a desktop GUI.** An earlier iteration of this work was a
PySide6 desktop application. The Qt layer was dropped during the merge into
this repo; one visible scar is that the background scheduler had to move off
APScheduler's `QtScheduler`, which needs a Qt event loop, onto
`BackgroundScheduler`, which owns its own threads. Streamlit bought a hosted
demo build from the same codebase — worth more than the desktop packaging it
cost.

**What I would not defend:** this system has no production operations story. It
has CI and a Dockerfile and no load testing, no distributed tracing, no
incident history, no on-call. It has never run anywhere but a laptop and a
free-tier host. Local-first was the correct call for the product and it means
I have not operated this thing at scale, and I would rather say that plainly
than have someone discover it in an interview.

---

## What I would tell someone starting this

Working with a coding agent at volume changed which engineering skills were
load-bearing for me, and not in the direction I expected.

Writing code stopped being the constraint almost immediately. Reading became
the constraint, and then reading stopped scaling too — you cannot review 38,000
lines with the same attention you'd give 3,800. What replaced it was
**deciding, in advance, which regions of the system a wrong answer must never
reach**, and then building structural barriers around those regions: an
allowlist a generated artifact cannot add itself to, a send capability that
does not exist, a single accessor that every store is forced through, a
Human Validation Zone recorded in the file the agent reads before it starts.

The barriers have to be structural rather than procedural. "Remember to check
the deadline math" is not a control — the next session has no memory of it.
`SIGNED_OFF_TEMPLATES` is a control, because approval is a thing a human
physically types into a set.

The agent was extremely good at producing correct code. It had no way to know
which mistakes were expensive. That distinction was mine to hold, and holding
it turned out to be the whole job.

---

*Source: [github.com/madfrantic/non-pursuit](https://github.com/madfrantic/non-pursuit).
Deadline conventions, sign-off gates, and Human Validation Zones are documented
in the repository's `CLAUDE.md`.*
