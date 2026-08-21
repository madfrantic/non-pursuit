"""
Delisting recon sweep -- the 24-hour background verifier.

Checks every active campaign's broker endpoint and records whether the
profile still resolves, so a removal gets confirmed (and dated) without
anyone having to remember to look. Meant for cron:

    0 9 * * *  cd /path/to/non-pursuit && .venv/bin/python agent_recon.py

Reads and writes the same local SQLite ledger the app uses. Nothing is
uploaded; the only outbound traffic is the HEAD request to each broker
URL already on record.

Run with --dry-run to see what would be checked without touching the
ledger or making a single request.
"""
import argparse
import logging
import os
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
UTILS_DIR = os.path.join(ROOT_DIR, "utils")
if UTILS_DIR not in sys.path:
    sys.path.append(UTILS_DIR)

import campaign_manager  # noqa: E402  -- needs UTILS_DIR on the path first
import config  # noqa: E402
import recon_worker  # noqa: E402

_log = logging.getLogger("agent_recon")


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-Pursuit delisting recon sweep")
    parser.add_argument("--db", default=config.CAMPAIGNS_DB_PATH, help="SQLite ledger path")
    parser.add_argument("--limit", type=int, default=None, help="Max campaigns to check")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be checked; make no requests and no writes")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    campaign_manager.init_db(args.db)
    active = campaign_manager.get_active_campaigns(args.db)
    if args.limit is not None:
        active = active[:args.limit]

    if not active:
        print("No active campaigns to verify.")
        return 0

    if args.dry_run:
        print(f"Would check {len(active)} campaign(s):\n")
        for record in active:
            remaining = record["days_remaining"]
            if remaining is None:
                clock = "no clock"
            elif remaining < 0:
                clock = f"{abs(remaining)}d OVERDUE"
            else:
                clock = f"{remaining}d remaining"
            print(f"  {record['broker_name']:<20} {record['effective_status']:<15} "
                  f"{clock:<15} {record.get('profile_url') or '(no URL on record)'}")
        return 0

    results = recon_worker.run_sweep(args.db, max_checks=args.limit)
    if not results:
        print("Sweep did not run — live scanning is disabled in this runtime.")
        return 0

    for entry in results:
        print(f"{entry['broker_name']:<20} {entry['result']:<15} {entry['detail']}")

    removed = sum(1 for e in results if e["result"] == recon_worker.RESULT_REMOVED)
    print(f"\n{len(results)} checked, {removed} newly confirmed removed.")

    counts = campaign_manager.summarize(args.db)
    if counts[campaign_manager.STATUS_NON_COMPLIANT]:
        print(f"⚠️  {counts[campaign_manager.STATUS_NON_COMPLIANT]} campaign(s) past the "
              f"{config.CCPA_RESPONSE_WINDOW_DAYS}-day statutory window — escalation warranted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
