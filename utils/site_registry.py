"""
One site list built from three upstream datasets: WhatsMyName, Sherlock and
Maigret.

Each of the three encodes "does this handle exist here?" differently, and
each is wrong in a different direction. WhatsMyName carries hand-tuned
exists-strings but only ~700 sites. Sherlock has ~480 with a clean
three-way error model (status_code / message / response_url) and, crucially,
`regexCheck` -- a per-site rule for what a valid handle even looks like
there. Maigret has ~3200, most of them forums, and is the only one that
records `protection` and inherits shared detection logic from an `engine`
definition rather than repeating it per site.

The normalised schema is deliberately WhatsMyName's, extended rather than
replaced, because utils/footprint_scanner.py already speaks it and its
classifier encodes real hard-won corrections (the empty-m_string trap, the
m_code == e_code collision) that a fresh schema would have to relearn. The
extensions are additive:

    e_strings / m_strings   list form of e_string / m_string. Sherlock and
                            Maigret both ship *lists* of error messages;
                            collapsing them to one string throws away real
                            detection coverage.
    check_type              status_code | message | response_url, from
                            Sherlock's errorType / Maigret's checkType.
    error_url               the URL a miss redirects to (response_url type).
    regex_check             handle-validity regex. A site that cannot hold
                            the handle should be skipped, not probed and
                            reported as a clean miss -- that difference is
                            what stops a 3000-site sweep reporting 2900
                            "not found" results it never legitimately tested.
    source                  which dataset the entry came from, so a
                            disagreement between them stays attributable.

Merge order is WhatsMyName > Sherlock > Maigret, by normalised site name.
WMN wins because its entries are the most actively curated per-site; Maigret
loses ties because its long tail is largely auto-generated forum entries.
A losing duplicate is not discarded -- its detection strings are folded into
the winner, so three partial descriptions of Reddit become one better one.

LICENSING, and why nothing here is committed: WhatsMyName is CC BY-SA 4.0.
Sherlock and Maigret are MIT. A merged file mixing them inherits ShareAlike,
which is exactly what this repo avoided by not vendoring wmn-data.json in the
first place. So the merged registry is built at runtime into data/ (which is
gitignored) and never checked in. attribution() returns the credit line all
three licences require.
"""
import hashlib
import json
import re
from pathlib import Path

import requests

from applog import get_logger
from wmn_dataset import DATASET_URL as WMN_URL, EXTRA_SITES, NSFW_CATEGORY

_log = get_logger("site_registry")

SHERLOCK_URL = (
    "https://raw.githubusercontent.com/sherlock-project/sherlock/master/"
    "sherlock_project/resources/data.json"
)
MAIGRET_URL = (
    "https://raw.githubusercontent.com/soxoj/maigret/main/maigret/resources/data.json"
)

SOURCE_WMN = "whatsmyname"
SOURCE_SHERLOCK = "sherlock"
SOURCE_MAIGRET = "maigret"

# Highest-trust first. Also the tie-break order in merge_sites().
SOURCE_PRECEDENCE = (SOURCE_WMN, SOURCE_SHERLOCK, SOURCE_MAIGRET)

CHECK_STATUS = "status_code"
CHECK_MESSAGE = "message"
CHECK_RESPONSE_URL = "response_url"

REGISTRY_SCHEMA_VERSION = "1.0"

# Maigret spells this key two ways in its own dataset -- 584 entries use
# "presenseStrs" and 13 use "presenceStrs". Reading only the documented
# spelling silently drops the 13.
_MAIGRET_PRESENCE_KEYS = ("presenseStrs", "presenceStrs")


def _norm_name(name: str) -> str:
    """Collapse a site name to a merge key. 'GitHub (User)', 'github' and
    'GitHub' are one platform; the parenthetical in WMN names is a
    disambiguator for *what* it probes, not a different site."""
    name = re.sub(r"\s*\([^)]*\)\s*", " ", str(name or ""))
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _as_list(value) -> list:
    """Normalise a str | list | None field to a list of non-empty strings.

    The empty string is dropped here rather than downstream, because
    `"" in content` is always True -- the same trap that made 34 WhatsMyName
    entries permanent false negatives before footprint_scanner guarded it.
    Filtering at ingest means no consumer has to remember the guard.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if isinstance(item, str) and item]


def _blank_site(name: str, source: str) -> dict:
    return {
        "name": name,
        "cat": "",
        "uri_check": "",
        "uri_pretty": None,
        "e_code": None,
        "e_string": "",
        "e_strings": [],
        "m_code": None,
        "m_string": "",
        "m_strings": [],
        "error_url": None,
        "check_type": CHECK_STATUS,
        "regex_check": None,
        "headers": {},
        "post_body": None,
        "strip_bad_char": "",
        "protection": [],
        "source": source,
        "sources": [source],
        "tags": [],
    }


def normalize_wmn(site: dict) -> dict | None:
    """WhatsMyName entry -> unified schema. Near-identity: this *is* the
    base schema, so the only work is widening the singular strings into
    lists and declaring the check type the fields imply."""
    if not site.get("name") or not site.get("uri_check"):
        return None

    entry = _blank_site(site["name"], SOURCE_WMN)
    entry.update({
        "cat": site.get("cat", ""),
        "uri_check": site["uri_check"],
        "uri_pretty": site.get("uri_pretty"),
        "e_code": site.get("e_code"),
        "e_string": site.get("e_string") or "",
        "e_strings": _as_list(site.get("e_string")),
        "m_code": site.get("m_code"),
        "m_string": site.get("m_string") or "",
        "m_strings": _as_list(site.get("m_string")),
        "headers": dict(site.get("headers") or {}),
        "post_body": site.get("post_body"),
        "strip_bad_char": "".join(site.get("strip_bad_char") or ""),
        "protection": list(site.get("protection") or []),
        "check_type": CHECK_MESSAGE if site.get("e_string") else CHECK_STATUS,
    })
    return entry


def normalize_sherlock(name: str, site: dict) -> dict | None:
    """Sherlock entry -> unified schema.

    Sherlock's model is inverted from WhatsMyName's: it describes what a
    *miss* looks like and treats everything else as a hit. So errorMsg
    becomes m_strings, and the exists-code is 200 by default because
    Sherlock only ever asserts a positive on a 2xx.

    `urlProbe` is honoured over `url` when present -- 52 sites are checked
    against an API endpoint but should link the human-facing profile page,
    the same split WhatsMyName spells uri_check / uri_pretty.
    """
    url = site.get("urlProbe") or site.get("url")
    if not url or "{}" not in url:
        return None

    entry = _blank_site(name, SOURCE_SHERLOCK)
    error_type = site.get("errorType", CHECK_STATUS)

    entry.update({
        # Sherlock has no category field; NSFW is the one classification it
        # does carry, and it has to survive because select_sites() filters on it.
        "cat": NSFW_CATEGORY if site.get("isNSFW") else "",
        "uri_check": url.replace("{}", "{account}"),
        "uri_pretty": (site["url"].replace("{}", "{account}")
                       if site.get("urlProbe") and site.get("url") else None),
        "e_code": 200,
        "m_code": site.get("errorCode"),
        "m_strings": _as_list(site.get("errorMsg")),
        "error_url": (site["errorUrl"].replace("{}", "{account}")
                      if site.get("errorUrl") else None),
        "check_type": error_type if error_type in (
            CHECK_STATUS, CHECK_MESSAGE, CHECK_RESPONSE_URL) else CHECK_STATUS,
        "regex_check": site.get("regexCheck"),
        "headers": dict(site.get("headers") or {}),
        "post_body": site.get("request_payload"),
        # errorCode is the documented miss status; when a site declares none,
        # 404 is the honest default for "profile absent".
        "post_method": site.get("request_method"),
    })
    if entry["m_code"] is None and entry["check_type"] == CHECK_STATUS:
        entry["m_code"] = 404
    entry["m_string"] = entry["m_strings"][0] if entry["m_strings"] else ""
    if isinstance(entry.get("post_body"), dict):
        entry["post_body"] = json.dumps(entry["post_body"]).replace("{}", "{account}")
    return entry


def _resolve_engine(site: dict, engines: dict) -> dict:
    """Fold a Maigret engine definition into a site that references it.

    1379 of Maigret's 3236 sites carry almost no detection logic of their
    own -- they name an `engine` (Discourse, XenForo, phpBB...) that holds
    the shared url template and match strings, with {urlMain} left for the
    site to fill in. Reading those sites without resolving the engine yields
    3236 entries where a third have no uri_check at all.
    """
    engine_name = site.get("engine")
    if not engine_name or engine_name not in engines:
        return dict(site)

    merged = dict(engines[engine_name].get("site") or {})
    merged.update(site)  # the site's own keys always win over the engine's

    # {urlSubpath} is the second placeholder engine templates carry: phpBB
    # and XenForo installs sit at wildly different paths ("", "/forum",
    # "/community"), and 51 sites supply their own. Leaving it unsubstituted
    # sends a literal "{urlSubpath}" in the request path -- a guaranteed 404
    # that reads as a clean, trustworthy miss.
    url_main = (site.get("urlMain") or "").rstrip("/")
    url_subpath = site.get("urlSubpath") or ""
    for key in ("url", "urlProbe", "errorUrl"):
        if isinstance(merged.get(key), str):
            merged[key] = (merged[key]
                           .replace("{urlMain}", url_main)
                           .replace("{urlSubpath}", url_subpath))
    return merged


def normalize_maigret(name: str, site: dict, engines: dict) -> dict | None:
    """Maigret entry (engine already folded in) -> unified schema.

    `disabled` entries are dropped rather than carried with a flag. Maigret
    marks a site disabled when its own maintainers found the check no longer
    works; probing it anyway produces a confident wrong answer, which is the
    one output a compliance tool must not emit.
    """
    if site.get("disabled"):
        return None

    site = _resolve_engine(site, engines)
    url = site.get("urlProbe") or site.get("url")
    if not url or "{username}" not in url:
        return None

    # 25 sites key on an internal numeric id (steam_id, gaia_id, vk_id...)
    # rather than a handle. Feeding them a username produces a guaranteed
    # miss that reads as a real negative result.
    if site.get("type"):
        return None

    # Any placeholder other than the handle means the template was never
    # fully resolved. Probing it produces a confident wrong answer, so the
    # entry is dropped rather than carried -- upstream adds new placeholders
    # from time to time and this catches them without a code change.
    if re.search(r"\{(?!username\})[a-zA-Z]+\}", url):
        _log.debug("Dropping %s: unresolved placeholder in %s", name, url)
        return None

    presence = []
    for key in _MAIGRET_PRESENCE_KEYS:
        presence.extend(_as_list(site.get(key)))

    entry = _blank_site(name, SOURCE_MAIGRET)
    tags = [t for t in (site.get("tags") or []) if isinstance(t, str)]
    check_type = site.get("checkType") or (CHECK_MESSAGE if presence else CHECK_STATUS)

    entry.update({
        "cat": tags[0] if tags else "",
        "tags": tags,
        "uri_check": url.replace("{username}", "{account}"),
        "uri_pretty": (site["url"].replace("{username}", "{account}")
                       if site.get("urlProbe") and site.get("url") else None),
        "e_code": 200,
        "e_strings": presence,
        "m_code": 404 if check_type == CHECK_STATUS else None,
        "m_strings": _as_list(site.get("absenceStrs")),
        "error_url": (site["errorUrl"].replace("{username}", "{account}")
                      if site.get("errorUrl") else None),
        "check_type": check_type if check_type in (
            CHECK_STATUS, CHECK_MESSAGE, CHECK_RESPONSE_URL) else CHECK_STATUS,
        "regex_check": site.get("regexCheck"),
        "headers": dict(site.get("headers") or {}),
        "protection": list(site.get("protection") or []),
    })
    entry["e_string"] = entry["e_strings"][0] if entry["e_strings"] else ""
    entry["m_string"] = entry["m_strings"][0] if entry["m_strings"] else ""

    # ignore403 means Maigret observed this site answering 403 to everything,
    # hit or miss. That is the same fact footprint_scanner encodes as a WAF
    # protection, and routing it there gets it a manual-review row instead of
    # a false POSSIBLE.
    if site.get("ignore403") and "waf" not in [p.lower() for p in entry["protection"]]:
        entry["protection"].append("WAF")
    return entry


def _fold_duplicate(winner: dict, loser: dict) -> dict:
    """Merge a lower-precedence duplicate into the winner.

    Only additive: detection strings union, protection flags union, and
    blank scalar fields get filled from the loser. The winner's uri_check
    and codes are never overwritten -- the whole point of precedence is
    that its probe is the one we trust.
    """
    for key in ("e_strings", "m_strings", "protection", "tags"):
        seen = set(winner[key])
        winner[key] = winner[key] + [v for v in loser.get(key, [])
                                     if not (v in seen or seen.add(v))]

    for key in ("uri_pretty", "regex_check", "error_url"):
        if not winner.get(key) and loser.get(key):
            winner[key] = loser[key]
    if not winner.get("cat") and loser.get("cat"):
        winner["cat"] = loser["cat"]

    winner["e_string"] = winner["e_string"] or (winner["e_strings"][0] if winner["e_strings"] else "")
    winner["m_string"] = winner["m_string"] or (winner["m_strings"][0] if winner["m_strings"] else "")

    for src in loser.get("sources", []):
        if src not in winner["sources"]:
            winner["sources"].append(src)
    return winner


def _is_probeable(site: dict) -> bool:
    """True when the handle actually reaches the site somewhere.

    21 merged entries have a static uri_check -- the handle travels in a
    POST body (AniList's GraphQL query) or, for a few, nowhere at all. The
    ones carrying it in the body are fine. The rest would send an identical
    request for every handle on earth and classify the identical response,
    which is a result that looks real and means nothing.
    """
    if "{account}" in (site.get("uri_check") or ""):
        return True
    return "{account}" in (site.get("post_body") or "")


def merge_sites(*groups: list) -> list:
    """Merge normalised site lists, highest precedence first.

    Returns entries sorted by name so a rebuild is byte-stable for the same
    inputs -- which is what makes the payload hash a usable change signal.
    """
    merged: dict[str, dict] = {}
    for group in groups:
        for site in group:
            if not site:
                continue
            key = _norm_name(site["name"])
            if not key:
                continue
            if key in merged:
                _fold_duplicate(merged[key], site)
            else:
                merged[key] = site
    return sorted(merged.values(), key=lambda s: s["name"].lower())


def build_registry(wmn: dict | None = None, sherlock: dict | None = None,
                   maigret: dict | None = None) -> dict:
    """Assemble the unified registry from any subset of the three sources.

    Every source is optional: a fetch failure for one should cost that
    source's sites, not the whole registry.
    """
    wmn_sites = [normalize_wmn(s) for s in (wmn or {}).get("sites", [])]
    wmn_sites += [normalize_wmn(s) for s in EXTRA_SITES]

    sherlock_sites = [
        normalize_sherlock(name, site)
        for name, site in (sherlock or {}).items()
        if not name.startswith("$") and isinstance(site, dict)
    ]

    maigret_raw = (maigret or {}).get("sites", {})
    engines_raw = (maigret or {}).get("engines", {})
    engines = {e["name"]: e for e in engines_raw.values()} if isinstance(engines_raw, dict) else {}
    # Maigret ships engines keyed by name already in some releases and by
    # index in others; normalise both to name -> definition.
    if isinstance(engines_raw, dict) and engines_raw and "name" not in next(iter(engines_raw.values()), {}):
        engines = engines_raw
    maigret_sites = [
        normalize_maigret(name, site, engines)
        for name, site in maigret_raw.items()
        if isinstance(site, dict)
    ]

    sites = merge_sites(
        [s for s in wmn_sites if s and _is_probeable(s)],
        [s for s in sherlock_sites if s and _is_probeable(s)],
        [s for s in maigret_sites if s and _is_probeable(s)],
    )
    counts = {src: 0 for src in SOURCE_PRECEDENCE}
    for site in sites:
        counts[site["source"]] = counts.get(site["source"], 0) + 1

    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "sites": sites,
        "counts": {"total": len(sites), "by_source": counts},
        "attribution": attribution(),
    }


def attribution() -> str:
    """Credit line for all three upstreams. WhatsMyName's CC BY-SA 4.0
    requires it; the MIT licences require the notice to travel with the
    data, and this is where it travels."""
    return (
        "Site definitions merged from: WhatsMyName by WebBreacher (Micah Hoffman), "
        "CC BY-SA 4.0; Sherlock by the Sherlock Project, MIT; "
        "Maigret by soxoj, MIT."
    )


def payload_hash(payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: str | Path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("Unreadable cache at %s: %s", path, exc)
        return None


def _write_json(path: str | Path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _fetch(url: str, timeout: int) -> dict:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def ensure_registry(data_dir: str = "data", refresh: bool = False,
                    timeout: int = 60) -> tuple[dict, str]:
    """Return (registry, status) with the merged registry cached on disk.

    status is "cached", "built" (no usable cache existed), "updated" (a
    refresh changed something), "unchanged", or "partial" (at least one
    upstream was unreachable and its sites are missing from this build).

    "partial" is a distinct status rather than a silent success because a
    registry missing Maigret is 3000 sites smaller, and a scan run against
    it would report far fewer exposures for reasons that have nothing to do
    with the person being scanned.
    """
    data_dir = Path(data_dir)
    registry_path = data_dir / "sites-unified.json"
    cached = _read_json(registry_path)

    if cached and not refresh:
        return cached, "cached"

    sources, failures = {}, []
    for key, url, path in (
        ("wmn", WMN_URL, data_dir / "wmn-data.json"),
        ("sherlock", SHERLOCK_URL, data_dir / "sherlock-data.json"),
        ("maigret", MAIGRET_URL, data_dir / "maigret-data.json"),
    ):
        try:
            payload = _fetch(url, timeout)
            _write_json(path, payload)
        except Exception as exc:
            _log.warning("Fetching %s failed (%s); falling back to cache", key, exc)
            payload = _read_json(path)
            if payload is None:
                failures.append(key)
        sources[key] = payload

    registry = build_registry(sources.get("wmn"), sources.get("sherlock"),
                              sources.get("maigret"))
    registry["incomplete_sources"] = failures

    if failures and cached:
        # A partial rebuild must not overwrite a complete cache.
        _log.warning("Keeping existing registry; sources unavailable: %s", failures)
        return cached, "partial"

    if cached and payload_hash(cached.get("sites")) == payload_hash(registry["sites"]):
        return cached, "unchanged"

    _write_json(registry_path, registry)
    return registry, "partial" if failures else ("updated" if cached else "built")


def handle_is_valid(site: dict, account: str) -> bool:
    """True when `account` can legally exist on this site.

    A site whose regex rejects the handle is not a miss -- it was never a
    candidate. Skipping it keeps the denominator honest: 40 real misses out
    of 40 real probes is a result, 40 misses out of 3000 sites that mostly
    could not have held the handle is noise dressed as a result.

    An unparseable regex is treated as "no constraint" rather than raising;
    a handful of upstream entries carry regexes that are valid in JavaScript
    but not in Python's `re`.
    """
    pattern = site.get("regex_check")
    if not pattern:
        return True
    try:
        return re.search(pattern, account) is not None
    except re.error:
        _log.debug("Unparseable regexCheck on %s: %r", site.get("name"), pattern)
        return True


def select_sites(registry: dict, include_nsfw: bool = True,
                 sources: tuple = SOURCE_PRECEDENCE,
                 categories: list | None = None,
                 account: str | None = None) -> list:
    """The site list a scan should run against.

    NSFW is INCLUDED by default. This is the maximum-coverage default and
    it is deliberate: an adult-platform account is the single most damaging
    kind of forgotten exposure, so a footprint sweep that silently skips
    that category reports a clean bill of health it did not actually earn.
    Omitting it is the surprise, not including it.

    The trade-off this accepts is that a sweep now probes adult platforms
    with the subject's real handle. That is the right default for a tool
    whose subject is its own operator, and callers scanning on someone
    else's behalf are expected to pass include_nsfw=False.

    The HTTP API does not inherit this default: api/main._select_sites
    always passes include_nsfw explicitly from ScanRequest.options, whose
    own default stays False, so a remote caller must still opt in.
    """
    sites = [s for s in registry.get("sites", []) if s.get("source") in sources]
    if not include_nsfw:
        sites = [s for s in sites if s.get("cat") != NSFW_CATEGORY]
    if categories:
        wanted = set(categories)
        sites = [s for s in sites if s.get("cat") in wanted or wanted & set(s.get("tags", []))]
    if account:
        sites = [s for s in sites if handle_is_valid(s, account)]
    return sites
