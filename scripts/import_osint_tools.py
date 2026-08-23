#!/usr/bin/env python3
"""
Build data/osint_catalog.json from every configured upstream.

    python scripts/import_osint_tools.py                 # full run
    python scripts/import_osint_tools.py --limit 10      # quick smoke run
    python scripts/import_osint_tools.py --only "OSINT Framework"
    python scripts/import_osint_tools.py --dry-run       # fetch, merge, discard

Re-running is safe and expected: tool ids are derived from the normalised URL,
so a second run reproduces the same catalog rather than duplicating it. The
whole document is rewritten atomically at the end, so an interrupted run
leaves the previous catalog in place.

A source that fails is reported and skipped -- the point of ten sources is
that no single one is load-bearing. Exit status is 1 only if *every* source
failed, or if the merged catalog came out empty.

Environment:
    GITHUB_TOKEN   optional; raises the GitHub quota from 60/hr to 5000/hr.
                   The pipeline needs ~10 calls, so it runs fine without one.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "utils")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import osint_catalog  # noqa: E402
from importers import IMPORTERS  # noqa: E402
from importers._http import HttpClient  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def run(delay: float = 1.0, limit=None, only=None, dry_run: bool = False,
        path: str = None) -> int:
    catalog = osint_catalog.Catalog()
    client = HttpClient(delay=delay)

    if not client.github_token:
        _log("note: GITHUB_TOKEN unset — using the 60 req/hr anonymous quota "
             "(enough for this run).")

    selected = [(n, m) for n, m in IMPORTERS
                if not only or n.lower() in {o.lower() for o in only}]
    if not selected:
        _log(f"error: no importer matches {only!r}. "
             f"Known: {', '.join(n for n, _ in IMPORTERS)}")
        return 1

    succeeded, failed = [], []
    started = time.monotonic()

    for name, module in selected:
        _log(f"\n=== {name} ===")
        source_started = time.monotonic()
        try:
            if name == "OSINT Tools Library":
                def progress(done, total, _name=name):
                    if done % 25 == 0 or done == total:
                        _log(f"  {done}/{total} tool pages")
                rows = module.fetch(client, limit=limit, on_progress=progress)
            else:
                rows = module.fetch(client)
        except Exception as exc:  # noqa: BLE001 - one dead upstream, nine live ones
            _log(f"  FAILED: {type(exc).__name__}: {exc}")
            failed.append(name)
            continue

        before = len(catalog)
        for row in rows:
            catalog.merge_or_create_tool(row)
        added = len(catalog) - before
        elapsed = time.monotonic() - source_started
        _log(f"  {len(rows)} records -> {added} new, {len(rows) - added} merged "
             f"({elapsed:.1f}s)")
        succeeded.append(name)

    if not succeeded:
        _log("\nerror: every source failed; leaving the existing catalog alone.")
        return 1
    if len(catalog) == 0:
        _log("\nerror: catalog came out empty; refusing to overwrite.")
        return 1

    document = catalog.to_document()
    summary = osint_catalog.summarize(document)

    _log(f"\n{'=' * 52}")
    _log(f"tools            {summary['tools']}")
    _log(f"in >1 source     {summary['multi_source']}")
    _log(f"categories       {summary['categories']}")
    _log(f"relationships    {summary['relationships']}")
    _log(f"by source        {summary['by_source']}")
    _log(f"by access        {summary['by_access']}")
    _log(f"requests made    {client.request_count}")
    _log(f"elapsed          {time.monotonic() - started:.1f}s")
    if failed:
        _log(f"failed sources   {', '.join(failed)}")

    if dry_run:
        _log("\ndry run — nothing written.")
        return 0

    written = osint_catalog.save_catalog(catalog, path)
    _log(f"\nwrote {written}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Import OSINT tool catalogs into data/osint_catalog.json")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds between requests to one host (default 1.0)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap tool pages fetched from the OSINT Tools Library")
    parser.add_argument("--only", action="append", default=None,
                        metavar="SOURCE", help="run only this importer (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and merge but do not write")
    parser.add_argument("--out", default=None,
                        help="output path (default data/osint_catalog.json)")
    args = parser.parse_args(argv)
    return run(delay=args.delay, limit=args.limit, only=args.only,
               dry_run=args.dry_run, path=args.out)


if __name__ == "__main__":
    raise SystemExit(main())
