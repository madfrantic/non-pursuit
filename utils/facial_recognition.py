"""
Local biometric assistant: suggests whether a discovered avatar matches
the user's own uploaded photo. Suggests -- never decides.

This module never writes a verdict anywhere. It hands back a distance
score and a suggested label; whether that becomes discovered_accounts'
verification_status is a decision made in components/master.py, gated
behind an explicit button click. That split is deliberate and not just
style: audit_packager's evidence package tells its reader that every
Confirmed row was attested to by the user, and a model's cosine
distance is not that -- face-match models carry real false-positive
rates, especially against low-resolution social avatars, and this app's
letters and ZIP exports carry legal weight. A suggestion that quietly
became a fact would misrepresent what was actually verified.

Nothing here is persisted. The master photo lives in st.session_state
only, a downloaded avatar is held in memory just long enough to compare,
and neither image nor the raw comparison ever reaches SQLite -- a face
photo is more sensitive than anything else this app touches, and the
one thing more sensitive than not having a policy for that is having a
policy and then storing the photo anyway. Only the human's own
confirm/reject choice (already true of every other verification_status
value) gets written.

DeepFace is an optional, heavy dependency (TensorFlow/OpenCV, hundreds
of MB, downloads model weights on first real use) and is imported lazily
so importing this module -- and running the test suite -- never requires
it to be installed.
"""
import io
import tempfile
from pathlib import Path

import requests

from applog import get_logger
from footprint_scanner import validate_target_url

_log = get_logger("facial_recognition")

MODEL_NAME = "VGG-Face"
DISTANCE_METRIC = "cosine"
FETCH_TIMEOUT = 10


def _get_deepface():
    """The DeepFace module, or None if it isn't installed. A function
    rather than a module-level import so tests can monkeypatch it without
    the real package present, and so a missing dependency degrades to
    "feature unavailable" instead of an ImportError at app startup."""
    try:
        from deepface import DeepFace
        return DeepFace
    except ImportError:
        return None


def facial_recognition_available() -> bool:
    """Whether the optional dependency is actually installed. UI code
    uses this to show 'install deepface' instead of crashing."""
    return _get_deepface() is not None


def _fetch_image_bytes(url: str) -> bytes | None:
    """Download an already-public avatar image. Reuses the scanner's own
    SSRF guard rather than duplicating it -- the risk is identical (a
    URL from a dataset or a discovered account, not typed by the user)
    and the existing check is already tested."""
    rejection = validate_target_url(url)
    if rejection:
        _log.info("Refused to fetch avatar for face comparison: %s (%s)", url, rejection)
        return None
    try:
        response = requests.get(url, timeout=FETCH_TIMEOUT)
        if response.ok and response.content:
            return response.content
    except requests.RequestException as exc:
        _log.info("Avatar fetch failed for face comparison: %s", exc)
    return None


def _similarity_pct(distance: float) -> float:
    """A display-only heuristic, not a calibrated probability. Cosine
    distance runs roughly 0 (identical) to 2 (opposite); clamping
    1 - distance to [0, 1] and reading it as a percentage is intuitive
    for a badge but is not a statistically meaningful confidence figure,
    and nothing downstream should treat it as one."""
    return round(max(0.0, min(1.0, 1.0 - distance)) * 100, 1)


def verify_face(master_bytes: bytes, candidate_bytes: bytes) -> dict:
    """Compare two images already in memory. Returns a dict with
    "available" always present; check it before trusting the rest.

    Writes both images to a temp directory only because DeepFace's
    verify() wants file paths, not because this app wants them on disk.
    The directory is removed in a finally block regardless of outcome,
    including on a DeepFace exception (a face was undetectable, a
    corrupt image, a model-loading error) -- those are reported as
    "available: True, error: ..." rather than raised, since a single bad
    avatar in a 700-site sweep must not take the rest of the page down.
    """
    deepface = _get_deepface()
    if deepface is None:
        return {"available": False, "error": "DeepFace is not installed"}

    with tempfile.TemporaryDirectory() as tmp:
        master_path = Path(tmp) / "master.jpg"
        candidate_path = Path(tmp) / "candidate.jpg"
        master_path.write_bytes(master_bytes)
        candidate_path.write_bytes(candidate_bytes)

        try:
            result = deepface.verify(
                img1_path=str(master_path),
                img2_path=str(candidate_path),
                model_name=MODEL_NAME,
                distance_metric=DISTANCE_METRIC,
                enforce_detection=True,
            )
        except Exception as exc:
            _log.info("DeepFace comparison failed: %s", exc)
            return {"available": True, "error": f"Comparison failed: {str(exc)[:120]}"}

    distance = float(result.get("distance", 1.0))
    return {
        "available": True,
        "error": None,
        "verified": bool(result.get("verified", False)),
        "distance": distance,
        "threshold": result.get("threshold"),
        "similarity_pct": _similarity_pct(distance),
        "model": MODEL_NAME,
    }


def suggest_verification(master_bytes: bytes, avatar_url: str) -> dict:
    """The one entry point components/master.py calls: fetch the avatar,
    compare it to the master photo, and hand back a suggestion. Never
    writes anything -- the caller decides whether and when a user's
    click turns this into a real verification_status.
    """
    if not master_bytes or not avatar_url:
        return {"available": False, "error": "No master photo or avatar to compare"}

    candidate_bytes = _fetch_image_bytes(avatar_url)
    if candidate_bytes is None:
        return {"available": False, "error": "Could not fetch the avatar image"}

    outcome = verify_face(master_bytes, candidate_bytes)
    if not outcome.get("available") or outcome.get("error"):
        return outcome

    outcome["suggested_label"] = (
        f"⚡ {outcome['similarity_pct']}% biometric match — suggested: confirm"
        if outcome["verified"]
        else f"⚡ {outcome['similarity_pct']}% similarity — suggested: not a match"
    )
    return outcome
