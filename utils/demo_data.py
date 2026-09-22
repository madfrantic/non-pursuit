"""
Synthetic dataset for the hosted demo build.

A privacy tool demoes badly from an empty state: the whole product is
deadlines counting down and exposure accumulating, and neither is visible
in a fresh install. This seeds a campaign already in progress so a
presenter can open the app and immediately show the thing that matters.

Every value here is fabricated. The profile is a fictional person, the
brokers are real companies but the requests against them never happened,
and the discovered accounts are invented handles. Nothing in this module
should ever be presented as a real record.

Dates are computed relative to today rather than hardcoded, so the demo
shows a live 45-day clock no matter when it's run -- a fixed date would
drift into "everything is overdue" within weeks and quietly stop
demonstrating the feature.
"""
from datetime import datetime, timedelta

import discovered_accounts
import exposure_store
from tracker import add_request, update_status

DEMO_PROFILE = {
    "first_name": "Jordan",
    "last_name": "Vale",
    "middle_name": "",
    "birth_year": 1991,
    "email_address": "jordan.vale@example.com",
    "phone_number": "555-0142",
    "current_city": "Sacramento",
    "current_state": "CA",
    "current_zip_code": "95814",
    "historical_zip_codes": "94110, 90026",
}

DEMO_NAME = f"{DEMO_PROFILE['first_name']} {DEMO_PROFILE['last_name']}"
DEMO_LOCATION = f"{DEMO_PROFILE['current_city']}, {DEMO_PROFILE['current_state']}"
DEMO_EMAIL = DEMO_PROFILE["email_address"]
DEMO_RECORD_URL = "https://www.spokeo.com/Jordan-Vale/California/demo-record"

# (broker, channel, days since sent, status). The spread is chosen to put
# every state the tracker can render on screen at once: one just sent, one
# at day 30 of its 45-day window (the "act now" case), one already past
# deadline with no response, and one closed out successfully.
DEMO_REQUESTS = [
    ("Spokeo", "Email", 3, "Sent"),
    ("MyLife", "Email", 30, "Awaiting Response"),
    ("WhitePages", "Opt-out form", 52, "Non-Compliant"),
    ("BeenVerified", "Opt-out form", 21, "Complete"),
]

DEMO_ACCOUNTS = [
    {
        "platform": "Reddit", "category": "social", "target_identifier": "jvale_91",
        "profile_url": "https://www.reddit.com/user/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "Twitch", "category": "gaming", "target_identifier": "jvale_91",
        "profile_url": "https://www.twitch.tv/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "GitHub (User)", "category": "coding", "target_identifier": "jvale_91",
        "profile_url": "https://github.com/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "eBay", "category": "commerce", "target_identifier": "jvale_91",
        "profile_url": "https://www.ebay.com/usr/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "OnlyFans", "category": "adult", "target_identifier": "jvale_91",
        "profile_url": "https://onlyfans.com/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "Pornhub", "category": "adult", "target_identifier": "jvale_91",
        "profile_url": "https://www.pornhub.com/users/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "XVideos", "category": "adult", "target_identifier": "jvale_91",
        "profile_url": "https://www.xvideos.com/profiles/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "Stripchat", "category": "adult", "target_identifier": "jvale_91",
        "profile_url": "https://stripchat.com/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "Twitter / X", "category": "social", "target_identifier": "jvale_91",
        "profile_url": "https://twitter.com/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "Instagram", "category": "social", "target_identifier": "jvale_91",
        "profile_url": "https://instagram.com/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "Snapchat", "category": "social", "target_identifier": "jvale_91",
        "profile_url": "https://www.snapchat.com/add/jvale_91", "confidence": "CONFIRMED",
    },
    {
        "platform": "Steam", "category": "gaming", "target_identifier": "jvale_91",
        "profile_url": "https://steamcommunity.com/id/jvale_91", "confidence": "POSSIBLE",
    },
    {
        "platform": "Pinterest", "category": "social", "target_identifier": "jvale_91",
        "profile_url": "https://www.pinterest.com/jvale_91", "confidence": "POSSIBLE",
    },
]

DEMO_EXPOSURE = {
    "email_breach": "Found exposure",
    "phone_listing": "Checked — clear",
    "social_media": "Found exposure",
    "broker:Spokeo": "Found exposure",
    "broker:MyLife": "Found exposure",
}


def mock_scan_results(handle: str) -> list:
    """Stand-in for a live footprint scan. Returns the same row shape
    footprint_scanner produces so the UI needs no special-casing, with the
    demo handle substituted in so results look like they belong to
    whatever was typed."""
    results = []
    for account in DEMO_ACCOUNTS:
        identifier = handle or account["target_identifier"]
        results.append({
            "platform": account["platform"],
            "category": account["category"],
            "target_identifier": identifier,
            "profile_url": account["profile_url"].replace(
                account["target_identifier"], identifier
            ),
            "confidence": account["confidence"],
            "reason": (
                "exists-string and status matched"
                if account["confidence"] == "CONFIRMED"
                else "HTTP 200 without a definitive match"
            ),
            "protection": "",
        })
    # A believable scan also finds nothing on most sites; without these the
    # summary metrics would show 0 checked-and-clear and look broken.
    for platform in ("Facebook", "TikTok", "Etsy", "Venmo", "Strava"):
        results.append({
            "platform": platform, "category": "social",
            "target_identifier": handle, "profile_url": "",
            "confidence": "NOT_FOUND", "reason": "missing-string matched",
            "protection": "",
        })
    return results


def seed_demo_campaign(db_path: str) -> dict:
    """Populate a session database with the full demo scenario.

    Writes through the real store modules rather than inserting rows
    directly, so the demo exercises the same code path production does --
    if add_request's deadline math breaks, the demo breaks too, which is
    the point.
    """
    for broker, channel, days_ago, status in DEMO_REQUESTS:
        add_request(db_path, broker, channel, 45, notes="")

    # add_request always stamps today, so backdate afterwards to create the
    # spread of deadlines the demo needs.
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, broker_name FROM requests ORDER BY id").fetchall()
    by_broker = {row["broker_name"]: row["id"] for row in rows}
    for broker, _channel, days_ago, _status in DEMO_REQUESTS:
        sent = (datetime.now().date() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        conn.execute(
            "UPDATE requests SET date_sent = ?, status_updated_at = ? WHERE id = ?",
            (sent, sent, by_broker[broker]),
        )
    conn.commit()
    conn.close()

    for broker, _channel, _days, status in DEMO_REQUESTS:
        if status != "Sent":
            update_status(db_path, by_broker[broker], status)

    discovered_accounts.save_discoveries(db_path, DEMO_ACCOUNTS)

    for category, value in DEMO_EXPOSURE.items():
        exposure_store.record_check(db_path, category, value)

    return {
        "requests": len(DEMO_REQUESTS),
        "accounts": len(DEMO_ACCOUNTS),
        "exposure_checks": len(DEMO_EXPOSURE),
    }
