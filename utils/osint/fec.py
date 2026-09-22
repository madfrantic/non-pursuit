"""FEC campaign finance: query contributor disclosure records."""
import asyncio
import re
import aiohttp
from typing import Any, Dict, List, Optional, Tuple

from applog import get_logger
from osint import STATUS_SUCCESS, STATUS_UNAVAILABLE, STATUS_EMPTY

_log = get_logger("osint_fec")

FEC_API = "https://api.open.fec.gov/v1"
FEC_DEMO_KEY = "DEMO_KEY"  # Free tier key documented at FEC site

# Confidence verdicts for a contributor record against the subject profile.
CONFIRMED = "CONFIRMED"  # full name plus at least one corroborating attribute
POSSIBLE = "POSSIBLE"    # full name only, nothing available to corroborate

# Honorifics and generational suffixes carry no identifying weight, and the
# FEC files them inconsistently ("DOE, JANE A" vs "Jane Doe Jr.").
_NAME_NOISE = frozenset({
    "mr", "mrs", "ms", "miss", "dr", "prof",
    "jr", "sr", "ii", "iii", "iv", "v",
})


async def _search_contributions(name: str, state: str = None) -> List[Dict[str, Any]]:
    """
    Query FEC Schedule A contributions (itemized donations) matching
    contributor name. Returns contributor records with address and employer.

    The API's contributor_name parameter is a full-text search that ORs the
    terms it is given, so this is a candidate pool to be screened -- never a
    set of confirmed hits. See _match_contributor().
    """
    if not name:
        return []

    params = {
        "api_key": FEC_DEMO_KEY,
        "contributor_name": name,
        "per_page": 100,  # Reasonable limit for this query
    }
    if state:
        params["contributor_state"] = state.upper()

    url = f"{FEC_API}/schedules/schedule_a/"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=45)) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("results", [])
                else:
                    _log.warning("FEC API returned %d", r.status)
                    return []
    except asyncio.TimeoutError:
        _log.info("FEC API timeout")
    except Exception as exc:
        _log.info("FEC API error: %s", exc)
    return []


def _name_parts(value: Any) -> List[str]:
    """Identifying name tokens, order-normalized and stripped of noise.

    Handles the FEC's inverted "DOE, JANE A" form as well as plain
    "Jane A. Doe", so both reduce to ["jane", "a", "doe"].
    """
    if not value:
        return []
    text = str(value).strip()
    if "," in text:
        last, _, rest = text.partition(",")
        text = f"{rest.strip()} {last.strip()}"
    text = re.sub(r"[^\w\s'-]", " ", text)
    tokens = [t for t in text.casefold().split() if t]
    return [t for t in tokens if t.strip(".'-") not in _NAME_NOISE]


def _token_match(a: str, b: str) -> bool:
    """Equal tokens, or an initial standing in for a full given name."""
    a, b = a.strip(".'-"), b.strip(".'-")
    if not a or not b:
        return False
    if len(a) == 1 or len(b) == 1:
        return a[0] == b[0]
    return a == b


def _names_match(record_name: Any, subject_name: Any) -> bool:
    """True only when given name *and* surname both line up.

    This is the gate the old code was missing entirely: the API happily
    returns every "Jane" in the country for a "Jane Doe" query, and a
    surname check is what separates the subject from all of them.
    """
    rec, sub = _name_parts(record_name), _name_parts(subject_name)
    if len(rec) < 2 or len(sub) < 2:
        # A mononym on either side can never be disambiguated safely.
        return False
    return _token_match(rec[-1], sub[-1]) and _token_match(rec[0], sub[0])


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _zip5(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:5] if len(digits) >= 5 else ""


def _corroborate(record: Dict[str, Any], subject: Dict[str, str]) -> Tuple[int, bool]:
    """Score a name-matched record against the subject's known attributes.

    Returns (supporting_field_count, disqualified). A state or ZIP that
    contradicts the subject is treated as disqualifying: attributing a
    stranger's political donations to the subject is a far worse failure
    than missing one of the subject's own.
    """
    support = 0
    disqualified = False

    comparisons = (
        # (record value, subject value, hard conflict?)
        (_clean(record.get("contributor_state")), _clean(subject.get("state")), True),
        (_zip5(record.get("contributor_zip")), _zip5(subject.get("zip_code")), True),
        (_clean(record.get("contributor_city")), _clean(subject.get("city")), False),
        (_clean(record.get("contributor_employer") or record.get("employer")),
         _clean(subject.get("employer")), False),
        (_clean(record.get("contributor_occupation") or record.get("occupation")),
         _clean(subject.get("occupation")), False),
    )

    for record_value, subject_value, hard in comparisons:
        if not record_value or not subject_value:
            continue  # nothing to compare -- neither support nor conflict
        if record_value == subject_value:
            support += 1
        elif hard:
            disqualified = True

    return support, disqualified


def _identity_key(record: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """The locality/employer fingerprint used to tell same-named people apart."""
    return (
        _clean(record.get("contributor_state")),
        _clean(record.get("contributor_city")),
        _zip5(record.get("contributor_zip")),
        _clean(record.get("contributor_employer") or record.get("employer")),
    )


def _match_contributor(raw: List[Dict[str, Any]], subject: Dict[str, str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split the API's candidate pool into (confirmed, name-only) matches."""
    confirmed: List[Dict[str, Any]] = []
    possible: List[Dict[str, Any]] = []

    for record in raw:
        if not isinstance(record, dict):
            continue
        if not _names_match(record.get("contributor_name"), subject.get("name")):
            continue
        support, disqualified = _corroborate(record, subject)
        if disqualified:
            continue
        (confirmed if support else possible).append(record)

    return confirmed, possible


def _normalize_fec_records(raw: List[Dict[str, Any]], confidence: str = POSSIBLE) -> List[Dict[str, str]]:
    """Extract key fields from FEC API response.

    The Schedule A endpoint's real fields are contribution_receipt_amount,
    contributor_employer and contributor_occupation -- not the
    unprefixed/unsuffixed names this used to read, which don't exist in the
    response and so always evaluated to their fallback (every contribution
    rendered as a $0 donation with no employer). Output keys are the
    generic ones the shared renderers (components/master.py,
    utils/pdf_generator.py) already look for on every vector -- "amount",
    "date", "recipient" -- so a fixed field only had to be read once.
    """
    normalized = []
    for record in raw:
        normalized.append({
            "contributor_name": record.get("contributor_name", ""),
            "contributor_address": (record.get("contributor_city", "") + ", " +
                                   record.get("contributor_state", "") + " " +
                                   record.get("contributor_zip", "")).strip(),
            "employer": record.get("contributor_employer") or record.get("employer", ""),
            "occupation": record.get("contributor_occupation") or record.get("occupation", ""),
            "date": record.get("contribution_receipt_date", ""),
            "amount": record.get("contribution_receipt_amount", record.get("contribution_amount", 0)),
            "recipient": record.get("committee", {}).get("name", ""),
            "match_confidence": confidence,
        })
    return normalized


def _no_confident_match(screened: int, reason: str) -> Dict[str, Any]:
    return {
        "status": STATUS_EMPTY,
        "module": "fec",
        "records": [],
        "count": 0,
        "screened": screened,
        "reason": reason,
    }


async def scan_fec(
    name: str,
    state: str = None,
    city: str = None,
    zip_code: str = None,
    employer: str = None,
    occupation: str = None,
) -> Dict[str, Any]:
    """Scan FEC contribution records for one specific individual.

    The API search is a fuzzy full-text query, so everything it returns is
    screened against the subject's full name plus whatever locality and
    employment attributes the profile supplies. An ambiguous pool resolves
    to "no confident match" rather than to a stranger's donation history.
    """
    if not name:
        return {
            "status": STATUS_EMPTY,
            "module": "fec",
            "records": [],
            "error": "No name provided",
        }

    subject = {
        "name": name,
        "state": state,
        "city": city,
        "zip_code": zip_code,
        "employer": employer,
        "occupation": occupation,
    }

    records = await _search_contributions(name, state)
    if not records:
        return {
            "status": STATUS_EMPTY,
            "module": "fec",
            "records": [],
        }

    screened = len(records)
    confirmed, possible = _match_contributor(records, subject)

    if confirmed:
        normalized = _normalize_fec_records(confirmed, CONFIRMED)
        return {
            "status": STATUS_SUCCESS,
            "module": "fec",
            "records": normalized,
            "count": len(normalized),
            "screened": screened,
            "match_confidence": CONFIRMED,
        }

    if not possible:
        return _no_confident_match(
            screened,
            "No contributor matched the subject's full name and known details.",
        )

    # Name-only matches: safe to surface only if they describe one person.
    identities = {_identity_key(record) for record in possible}
    distinct = {key for key in identities if any(key)}
    if len(distinct) > 1:
        return _no_confident_match(
            screened,
            f"{len(distinct)} different people share this name in the FEC data; "
            "add a city, ZIP or employer to the profile to disambiguate.",
        )

    normalized = _normalize_fec_records(possible, POSSIBLE)
    return {
        "status": STATUS_SUCCESS,
        "module": "fec",
        "records": normalized,
        "count": len(normalized),
        "screened": screened,
        "match_confidence": POSSIBLE,
        "reason": "Name matched, but no city, ZIP or employer was available to confirm identity.",
    }
