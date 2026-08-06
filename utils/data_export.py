"""
Export everything this app has tracked about you as one JSON file.

data/tracker.db is the sole record of a real deletion campaign and
self-search history, with no backup path of its own -- if that file is
ever lost, so is the whole campaign. This gives the user a plain,
portable copy they control, not tied to this app or this machine.
"""
import json
from datetime import datetime


def build_json_export(requests: list[dict], exposure_checks: dict) -> bytes:
    """requests: from tracker.get_all_requests(). exposure_checks: from
    exposure_store.get_all_checks(). Both are already plain dict/list data,
    so this just adds an export timestamp and serializes."""
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "campaign_requests": requests,
        "exposure_checks": exposure_checks,
    }
    return json.dumps(payload, indent=2).encode("utf-8")
