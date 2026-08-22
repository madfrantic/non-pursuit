"""
Data-broker exposure probing, and the registry that drives it.

Social platforms and data brokers look similar from the outside -- both
answer "is this person here?" over HTTP -- and are different in every way
that matters to this tool. A platform holds an account you created; a broker
holds a dossier you never consented to, assembled from public records and
purchased lists. The account check keys on a handle. The broker check keys
on a name plus a location, because that is the only key a broker's public
search accepts.

So brokers get their own probe path rather than being bolted onto the site
registry as more entries. Three concrete reasons:

  * The query is a name and a city, not a handle, so handle-regex filtering,
    handle rarity and the whole identity-graph scoring model do not apply.
  * A hit is a *record*, not an account. There is nothing to log into and no
    profile page to extract a bio from; what matters is the record URL,
    because every broker's opt-out form demands it.
  * The remediation path is statutory rather than a settings page, and the
    contact to serve it on is per-broker, verified, and worth being wrong
    about loudly rather than quietly.

WHAT THIS WILL NOT DO

Several major brokers sit behind Cloudflare and answer automated requests
with a challenge. Those are declared `manual` in the registry and are never
probed: the module hands back the search URL for a person to open. No
challenge solving, no headless-browser fingerprint work, no retry storms.
A compliance record built on defeating a site's access controls is worth
less than no record, and the honest output -- "open this yourself" -- costs
the user thirty seconds.

CONTACT DATA IS NEVER INVENTED

Opt-out URLs and compliance addresses come from data/brokers.csv, which this
repo maintains with a `last_verified` stamp per row, or from runtime
discovery (see privacy_contacts.py) which records where it found what it
found. A broker with no verified contact is reported as having none. A
plausible-looking guess at privacy@<broker>.com would be indistinguishable
from a real one in the output and would silently send a statutory demand
into a black hole.
"""
import asyncio
import csv
import json
import time
from pathlib import Path
from urllib.parse import quote_plus

import aiohttp

from applog import get_logger
from footprint_scanner import validate_target_url
from recon_engine import BASE_HEADERS, MAX_BODY_BYTES, _read_capped, pick_user_agent

_log = get_logger("broker_probe")

# Verdicts. Deliberately not the account scanner's vocabulary: "CONFIRMED"
# for an account means the handle is taken, which is a different claim than
# "this broker is holding a record that matches you".
RECORD_FOUND = "RECORD_FOUND"
NO_RECORD = "NO_RECORD"
MANUAL_CHECK = "MANUAL_CHECK"
PROBE_ERROR = "PROBE_ERROR"

# Probe strategies a registry entry can declare.
MODE_QUERY = "query"      # GET the search URL and read the response
MODE_MANUAL = "manual"    # hand the URL to a human; never requested here

DEFAULT_TIMEOUT = 20
DEFAULT_CONCURRENCY = 4  # brokers are few and slow; hammering them is pointless

BROKERS_CSV = "data/brokers.csv"
BROKER_PROBES_JSON = "data/broker_probes.json"


def _bool(value) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1", "y"}


def load_broker_contacts(csv_path: str = BROKERS_CSV) -> dict:
    """Verified broker contact data, keyed by lowercase broker name.

    data/brokers.csv is the repo's existing source of truth for this and
    carries a per-row `last_verified` stamp. That stamp travels into every
    downstream record: a compliance package that cites an opt-out URL should
    also say when someone last confirmed the URL was real.
    """
    path = Path(csv_path)
    if not path.is_file():
        _log.warning("Broker contact file missing: %s", csv_path)
        return {}

    contacts = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("broker_name") or "").strip()
            if not name:
                continue
            contacts[name.lower()] = {
                "broker_name": name,
                "compliance_email": (row.get("compliance_email") or "").strip(),
                "optout_url": (row.get("optout_url") or "").strip(),
                "search_url": (row.get("search_url") or "").strip(),
                "automated_search": _bool(row.get("automated_search")),
                "notes": (row.get("notes") or "").strip(),
                "last_verified": (row.get("last_verified") or "").strip(),
                "contact_source": "data/brokers.csv",
            }
    return contacts


def load_probe_definitions(json_path: str = BROKER_PROBES_JSON) -> list:
    path = Path(json_path)
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("Unreadable broker probe definitions at %s: %s", json_path, exc)
        return []
    return payload.get("brokers", [])


def build_registry(csv_path: str = BROKERS_CSV,
                   json_path: str = BROKER_PROBES_JSON) -> list:
    """Merge probe definitions with verified contacts into one broker list.

    A probe definition without a matching contact row still yields an entry
    -- knowing a broker holds a record is useful even when nobody has
    verified where to send the demand yet, and the entry says so.
    """
    contacts = load_broker_contacts(csv_path)
    definitions = {d.get("name", "").lower(): d for d in load_probe_definitions(json_path)}

    brokers = []
    for key in sorted(set(contacts) | set(definitions)):
        contact = contacts.get(key, {})
        definition = definitions.get(key, {})
        name = definition.get("name") or contact.get("broker_name") or key

        mode = definition.get("mode")
        if not mode:
            # No probe definition: automatable only if the contact row says
            # so and actually supplies somewhere to look.
            mode = (MODE_QUERY if contact.get("automated_search")
                    and contact.get("search_url") else MODE_MANUAL)

        brokers.append({
            "name": name,
            "mode": mode,
            "search_template": definition.get("search_template") or "",
            "search_url": contact.get("search_url") or definition.get("search_url", ""),
            "found_strings": definition.get("found_strings") or [],
            "absent_strings": definition.get("absent_strings") or [],
            "record_url_pattern": definition.get("record_url_pattern") or "",
            "protection": definition.get("protection") or [],
            "jurisdiction_note": definition.get("jurisdiction_note", ""),
            "compliance_email": contact.get("compliance_email", ""),
            "optout_url": contact.get("optout_url", ""),
            "notes": contact.get("notes", ""),
            "last_verified": contact.get("last_verified", ""),
            "contact_source": contact.get("contact_source", ""),
            "contact_verified": bool(contact.get("optout_url")
                                     or contact.get("compliance_email")),
        })
    return brokers


def build_search_url(broker: dict, subject: dict) -> str:
    """Fill a broker's search template from the subject's identity fields.

    Placeholders are {first}, {last}, {name}, {city}, {state}. Everything is
    percent-encoded: a subject named "O'Brien" in "Fort Lee, NJ" must not be
    able to alter the request's structure.
    """
    template = broker.get("search_template") or ""
    if not template:
        return broker.get("search_url", "")

    name = (subject.get("name") or "").strip()
    parts = name.split()
    values = {
        "name": quote_plus(name),
        "first": quote_plus(parts[0] if parts else ""),
        "last": quote_plus(parts[-1] if len(parts) > 1 else ""),
        "city": quote_plus((subject.get("city") or "").strip()),
        "state": quote_plus((subject.get("state") or "").strip()),
    }
    url = template
    for key, value in values.items():
        url = url.replace("{" + key + "}", value)
    return url


def classify_broker_response(broker: dict, status: int | None, body: str) -> tuple:
    """(verdict, reason) for one broker search response.

    Ordered miss-first, like the account classifier and for the same reason:
    a broker's "no results" page is a specific, reliable string, whereas the
    hit case is a results template that also renders for near-misses. Absent
    a declared found-string, a 200 is reported as MANUAL_CHECK rather than a
    record -- a broker search that returns *something* is not evidence that
    the something is the subject.
    """
    if status is None:
        return PROBE_ERROR, "no response"
    if status in {403, 429, 503}:
        return MANUAL_CHECK, f"blocked by the broker's edge (HTTP {status}) — open the search by hand"

    for needle in broker.get("absent_strings") or []:
        if needle and needle in body:
            return NO_RECORD, f"broker reported no results ({needle[:40]!r})"

    for needle in broker.get("found_strings") or []:
        if needle and needle in body:
            return RECORD_FOUND, f"results page matched ({needle[:40]!r})"

    if status == 200:
        return MANUAL_CHECK, "search returned a page with no recognised result marker"
    return PROBE_ERROR, f"unexpected response (HTTP {status})"


def extract_record_urls(broker: dict, body: str) -> list:
    """Record URLs from a results page, if the broker declares a pattern.

    The record URL is the payload every opt-out form demands, so pulling it
    here is what makes the difference between a report that says "you are on
    Spokeo" and one the user can act on without searching again by hand.
    """
    import re
    pattern = broker.get("record_url_pattern")
    if not pattern:
        return []
    try:
        matches = re.findall(pattern, body[:MAX_BODY_BYTES])
    except re.error:
        _log.warning("Bad record_url_pattern on %s", broker.get("name"))
        return []
    seen, urls = set(), []
    for match in matches:
        url = match if isinstance(match, str) else match[0]
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls[:25]


def _manual_row(broker: dict, subject: dict, reason: str) -> dict:
    return {
        "broker": broker["name"],
        "verdict": MANUAL_CHECK,
        "reason": reason,
        "search_url": build_search_url(broker, subject) or broker.get("search_url", ""),
        "record_urls": [],
        "http_status": None,
        "response_time_ms": None,
        "optout_url": broker.get("optout_url", ""),
        "compliance_email": broker.get("compliance_email", ""),
        "contact_verified": broker.get("contact_verified", False),
        "last_verified": broker.get("last_verified", ""),
        "notes": broker.get("notes", ""),
    }


async def _probe_one(session, broker, subject, semaphore, timeout):
    if broker.get("mode") != MODE_QUERY:
        return _manual_row(
            broker, subject,
            "this broker blocks automated searches — open the URL and check by hand")

    url = build_search_url(broker, subject)
    if not url:
        return _manual_row(broker, subject, "no search URL on record for this broker")

    row = _manual_row(broker, subject, "")
    row["search_url"] = url

    async with semaphore:
        started = time.perf_counter()
        try:
            rejection = await asyncio.to_thread(validate_target_url, url)
            if rejection:
                verdict, reason = PROBE_ERROR, rejection
            else:
                headers = dict(BASE_HEADERS)
                headers["User-Agent"] = pick_user_agent(broker["name"])
                async with session.get(
                    url, headers=headers, allow_redirects=True, max_redirects=5,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    body = await _read_capped(response)
                    row["http_status"] = response.status
                    verdict, reason = classify_broker_response(broker, response.status, body)
                    if verdict == RECORD_FOUND:
                        row["record_urls"] = extract_record_urls(broker, body)
        except asyncio.TimeoutError:
            verdict, reason = PROBE_ERROR, "timed out"
        except aiohttp.ClientError as exc:
            _log.info("Broker probe failed for %s: %s", broker["name"], exc)
            verdict, reason = PROBE_ERROR, "request failed"
        except Exception as exc:  # noqa: BLE001
            _log.info("Unexpected broker probe failure for %s: %s", broker["name"], exc)
            verdict, reason = PROBE_ERROR, "request failed"
        finally:
            row["response_time_ms"] = round((time.perf_counter() - started) * 1000, 1)

    row["verdict"] = verdict
    row["reason"] = reason
    return row


async def probe_brokers(subject: dict, brokers: list | None = None, *,
                        concurrency: int = DEFAULT_CONCURRENCY,
                        timeout: int = DEFAULT_TIMEOUT) -> list:
    """Check every registered broker for a record matching `subject`.

    `subject` needs at minimum a name; city and state sharpen the query for
    brokers whose search accepts them. Returns one row per broker including
    the manual ones, because "this broker was never checked automatically"
    is a result the user has to see -- a report listing four brokers when
    the registry holds six reads as an all-clear on the missing two.
    """
    brokers = brokers if brokers is not None else build_registry()
    if not subject.get("name") or not brokers:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=2)
    async with aiohttp.ClientSession(connector=connector) as session:
        return list(await asyncio.gather(*(
            _probe_one(session, broker, subject, semaphore, timeout)
            for broker in brokers
        )))


def probe_brokers_sync(subject: dict, brokers: list | None = None, **kwargs) -> list:
    return asyncio.run(probe_brokers(subject, brokers, **kwargs))


async def verify_template(broker: dict, listed_subject: dict,
                          unlisted_subject: dict, *, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Check a probe definition against a known-listed and a known-unlisted
    subject before anyone trusts it.

    This is the claimed/unclaimed technique Sherlock and Maigret use per
    site, applied to brokers. A definition is only sound if it reports
    RECORD_FOUND for someone who is definitely listed *and* NO_RECORD for
    someone who is definitely not -- a found_string that also appears on the
    empty-results page passes the first test and fails silently forever
    after, marking every subject as exposed.

    Returns the two verdicts plus a `sound` flag. Nothing is written; the
    caller decides whether to flip the broker's mode to "query".
    """
    probe = dict(broker, mode=MODE_QUERY)
    semaphore = asyncio.Semaphore(1)
    connector = aiohttp.TCPConnector(limit=1, limit_per_host=1)
    async with aiohttp.ClientSession(connector=connector) as session:
        listed = await _probe_one(session, probe, listed_subject, semaphore, timeout)
        unlisted = await _probe_one(session, probe, unlisted_subject, semaphore, timeout)

    sound = (listed["verdict"] == RECORD_FOUND and unlisted["verdict"] == NO_RECORD)
    return {
        "broker": broker.get("name", ""),
        "sound": sound,
        "listed_result": listed,
        "unlisted_result": unlisted,
        "recommendation": (
            "safe to set mode='query'" if sound else
            "do NOT enable: the definition cannot separate a hit from a miss"
        ),
    }


def summarize(rows: list) -> dict:
    counts = {RECORD_FOUND: 0, NO_RECORD: 0, MANUAL_CHECK: 0, PROBE_ERROR: 0}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return {
        "brokers_checked": len(rows),
        "by_verdict": counts,
        "without_verified_contact": sum(1 for r in rows if not r.get("contact_verified")),
    }
