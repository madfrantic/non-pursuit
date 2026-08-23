"""
Upstream importers for the OSINT tool catalog.

Each module exposes `fetch(client) -> list[dict]`, returning rows shaped for
`utils.osint_catalog.merge_or_create_tool`. Importers do no deduplication and
touch no disk: they read one upstream and hand back records. Merging, id
assignment and persistence all happen once, in the runner, so that adding an
eleventh source never means re-implementing the merge rules.

An importer that fails raises. The runner catches per-source, logs, and moves
on -- one dead upstream must not cost the other nine.
"""
from . import github_repos, osint_framework, osint_newsletter, static_entries

# Ordered cheapest and most authoritative first, so that a run cut short still
# produces a useful catalog and the richest structured source (the OSINT
# Framework's arf.json) sets the baseline other sources enrich.
IMPORTERS = (
    ("Static Services", static_entries),
    ("OSINT Framework", osint_framework),
    ("GitHub", github_repos),
    ("OSINT Tools Library", osint_newsletter),
)

__all__ = ["IMPORTERS", "github_repos", "osint_framework", "osint_newsletter",
           "static_entries"]
