"""Canonical identity state shared by every Non-Pursuit view."""

PROFILE_KEY = "profile"
PROFILE_FIELDS = (
    "full_name",
    "email",
    "handle",
    "city",
    "state",
    "zip_code",
    "phone",
    "domain",
    "country",
)

# `country` drives statutory routing in utils/jurisdiction_router.py. It is
# session-state only -- deliberately not persisted to the target_profile table,
# which would be a schema migration. An empty country routes identically to
# "US", so an older profile that predates this field still behaves correctly.
DEFAULT_COUNTRY = "US"

_DEFAULT_PROFILE = {field: "" for field in PROFILE_FIELDS}
_DEFAULT_PROFILE["country"] = DEFAULT_COUNTRY


def profile_from_saved(saved_profile=None):
    """Adapt the SQLite target profile into the session profile shape."""
    saved_profile = saved_profile or {}
    full_name = " ".join(
        part for part in (
            saved_profile.get("first_name"),
            saved_profile.get("middle_name"),
            saved_profile.get("last_name"),
        ) if part
    )
    return {
        **_DEFAULT_PROFILE,
        "full_name": full_name,
        "email": saved_profile.get("email_address") or "",
        "city": saved_profile.get("current_city") or "",
        "state": saved_profile.get("current_state") or "",
        "zip_code": saved_profile.get("current_zip_code") or "",
        "phone": saved_profile.get("phone_number") or "",
    }


def ensure_profile(state, saved_profile=None):
    """Create the canonical profile once and return the live dictionary."""
    if PROFILE_KEY not in state:
        profile = profile_from_saved(saved_profile)
        # Restore handle and domain from session state if they were previously entered but not persisted
        profile["handle"] = state.get("pf_handle") or profile.get("handle", "")
        profile["domain"] = state.get("pf_domain") or profile.get("domain", "")
        if not saved_profile:
            profile["city"] = "New York"
            profile["state"] = "NY"
        state[PROFILE_KEY] = profile
    else:
        profile = {**_DEFAULT_PROFILE, **state[PROFILE_KEY]}
        state[PROFILE_KEY] = profile
    return state[PROFILE_KEY]


def sync_profile(state, values):
    """Update canonical identity and compatibility aliases atomically."""
    profile = ensure_profile(state)
    for field in PROFILE_FIELDS:
        if field in values and values[field] is not None:
            profile[field] = str(values[field]).strip()
    state[PROFILE_KEY] = profile

    # Existing modules outside the profile flow still consume these names.
    state["user_name"] = profile["full_name"]
    state["user_email"] = profile["email"]
    state["user_phone"] = profile["phone"]
    state["user_location"] = ", ".join(
        value for value in (profile["city"], profile["state"]) if value
    )
    state["footprint_handle"] = profile["handle"]
    state["footprint_email"] = profile["email"]
    state["osint_handle"] = profile["handle"]
    state["osint_domain"] = profile["domain"]
    return profile


def get_profile(state):
    """Return the canonical profile for read-only downstream consumers."""
    return ensure_profile(state)
