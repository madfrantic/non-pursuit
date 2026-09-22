# Safety boundaries

Non-Pursuit handles personal data and produces draft legal correspondence.
The following areas require manual review before a change is accepted:

- statutory request text and jurisdiction routing;
- response-deadline calculations;
- database schema or encryption/key-derivation changes;
- logging changes that could capture personal data;
- browser automation that could submit an opt-out request;
- any path that changes an unknown verification result into a negative result.

## Automation limits

Automation may discover public signals, create drafts, calculate review dates,
capture evidence, and prepare exports. It must not send correspondence or submit
an opt-out without an explicit human action.

## Data handling

- Personal data is stored locally and encrypted at rest.
- Runtime databases, keys, logs, screenshots, and evidence are excluded from Git.
- Demo mode uses synthetic data and per-session temporary storage.
- Network-dependent tests use mocks rather than live identities or broker sites.

## Required verification

Run the complete test suite after changes to application code. Legal templates,
deadline rules, encryption behavior, and schema changes also require a human
review independent of the automated tests.
