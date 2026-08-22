"""Build a normalized platform dataset from local upstream snapshots.

The snapshots are refreshed separately by ``site_registry.ensure_registry``.
This command performs only deterministic local normalization and writes the
requested ``data/sites.json`` artifact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
UTILS = ROOT / "utils"
if str(UTILS) not in sys.path:
    sys.path.insert(0, str(UTILS))

import site_registry


def extract(data_dir: Path) -> dict:
    def load(name: str):
        path = data_dir / name
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    registry = site_registry.build_registry(
        load("wmn-data.json"), load("sherlock-data.json"), load("maigret-data.json"))
    return {
        "schema_version": site_registry.REGISTRY_SCHEMA_VERSION,
        "attribution": site_registry.attribution(),
        "sites": [
            {
                "platform": site["name"],
                "check_url": site["uri_check"],
                "profile_url": site.get("uri_pretty"),
                "detection_type": site["check_type"],
                "error_codes": {"exists": site.get("e_code"), "missing": site.get("m_code")},
                "error_indicators": {
                    "exists": site.get("e_strings", []),
                    "missing": site.get("m_strings", []),
                    "redirect": site.get("error_url"),
                },
                "regex": site.get("regex_check"),
                "source": site.get("sources", [site.get("source", "")]),
            }
            for site in registry["sites"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "sites.json")
    args = parser.parse_args()
    payload = extract(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(payload['sites'])} sites to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
