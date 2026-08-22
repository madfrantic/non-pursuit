"""Command-line entrypoint for local compliance scans."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
UTILS = ROOT / "utils"
if str(UTILS) not in sys.path:
    sys.path.insert(0, str(UTILS))

import infra_checker
import site_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan public PII exposure signals.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--username", help="Username to scan across the site registry.")
    target.add_argument("--domain", help="Domain to inspect with DNS, RDAP, and TLS checks.")
    parser.add_argument("--email", default="", help="Optional email identity seed.")
    parser.add_argument("--name", default="", help="Optional name identity seed.")
    parser.add_argument("--output", type=Path, help="Write JSON to this file instead of stdout.")
    parser.add_argument("--max-sites", type=int, default=100)
    return parser


async def run(args: argparse.Namespace) -> dict:
    if args.domain:
        return {"target": {"domain": args.domain}, "infrastructure": await infra_checker.check_hosts([args.domain])}

    registry, status = site_registry.ensure_registry(str(ROOT / "data"), False)
    sites = site_registry.select_sites(registry, account=args.username)[:args.max_sites]
    import recon_engine
    rows = await recon_engine.scan(args.username, sites)
    return {
        "target": {"username": args.username, "name": args.name, "email": args.email},
        "scan": {"registry_status": status, "sites_selected": len(sites)},
        "findings": rows,
    }


def main() -> int:
    args = build_parser().parse_args()
    payload = asyncio.run(run(args))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())