"""
Dashboard: enter your info once, then head to Results to see what
searching for yourself actually found. Also shows your tracked campaign
progress. Kept separate from Results on purpose -- stacking the input
form, the full broker-by-broker results, and campaign metrics on one long
page made it too easy to miss that results were there at all.

There used to be two overlapping ways to enter your name/email/location
here -- a set of "quick" fields that saved instantly to session only, and
a fuller form below it that saved to SQLite. They could disagree with
each other, and either way you were typing the same details twice. This
is now the one form; everything else in the app reads from it.
"""
import streamlit as st

import config
import runtime_mode
import database
import exposure_store
import facial_recognition
import profile_state
from tracker import get_all_requests
from validators import is_valid_email

_MODE_RESULTS = "🔍 Master Intelligence Dossier"
_MODE_LETTERS = "✉️ Data Broker Deletion Letters"
_MODE_TRACKER = "📬 Opt-Out Tracker"

# Widget key -> target_profile column, for the fields backed by SQLite.
_PROFILE_FIELDS = {
    "pf_first_name": "first_name",
    "pf_middle_name": "middle_name",
    "pf_last_name": "last_name",
    "pf_email": "email_address",
    "pf_phone": "phone_number",
    "pf_city": "current_city",
    "pf_state": "current_state",
    "pf_zip": "current_zip_code",
    "pf_historical_zips": "historical_zip_codes",
}

# Prefilled only when nothing is on file -- a saved profile always wins,
# so this never overwrites a real answer with a guess.
_FIELD_DEFAULTS = {
    "pf_city": "New York",
    "pf_state": "NY",
}


def _switch_to(mode_value):
    # Can't set nav_mode directly here -- the sidebar radio (key="nav_mode")
    # has already rendered earlier in this same run. app.py applies this
    # before the radio is created on the next run.
    st.session_state.pending_nav = mode_value
    st.rerun()


def _seed_profile_fields():
    """Initialize form fields with defaults only - start each session fresh.
    No auto-loading of previously saved profiles to ensure privacy."""
    if st.session_state.get("_profile_fields_seeded"):
        return
    st.session_state._profile_fields_seeded = True
    # Initialize all fields with defaults, not saved profile data
    for key, column in _PROFILE_FIELDS.items():
        st.session_state.setdefault(key, _FIELD_DEFAULTS.get(key, ""))
    st.session_state.setdefault("pf_birth_year", None)
    st.session_state.setdefault("pf_handle", "")
    st.session_state.setdefault("pf_domain", "")
    st.session_state.setdefault("pf_associated_name", "")
    st.session_state.setdefault("pf_shared_addresses", "")
    st.session_state.setdefault("pf_shared_phones", "")
    st.session_state.setdefault("pf_shared_loyalty", "")


def _load_demo_profile():
    city, _, state = config.DEMO_PROFILE["location"].partition(", ")
    first_name, _, last_name = config.DEMO_PROFILE["name"].partition(" ")
    st.session_state.pf_first_name = first_name
    st.session_state.pf_last_name = last_name
    st.session_state.pf_middle_name = ""
    st.session_state.pf_email = config.DEMO_PROFILE["email"]
    st.session_state.pf_phone = ""
    st.session_state.pf_city = city
    st.session_state.pf_state = state
    st.session_state.pf_zip = ""
    st.session_state.pf_birth_year = None
    st.session_state.pf_historical_zips = ""
    st.session_state.pf_associated_name = ""
    st.session_state.pf_shared_addresses = ""
    st.session_state.pf_shared_phones = ""
    st.session_state.pf_shared_loyalty = ""
    st.session_state.record_url = config.DEMO_PROFILE["record_url"]


def _save_profile():
    full_name = " ".join(
        part for part in [
            st.session_state.pf_first_name,
            st.session_state.pf_middle_name,
            st.session_state.pf_last_name,
        ] if part
    )
    profile_state.sync_profile(st.session_state, {
        "full_name": full_name,
        "email": st.session_state.pf_email,
        "handle": st.session_state.pf_handle,
        "domain": st.session_state.pf_domain,
        "city": st.session_state.pf_city,
        "state": st.session_state.pf_state,
        "zip_code": st.session_state.pf_zip,
        "phone": st.session_state.pf_phone,
    })
    database.insert_target_profile(
        runtime_mode.db_path(),
        {
            "first_name": st.session_state.pf_first_name,
            "last_name": st.session_state.pf_last_name,
            "middle_name": st.session_state.pf_middle_name,
            "birth_year": int(st.session_state.pf_birth_year) if st.session_state.pf_birth_year else None,
            "email_address": st.session_state.pf_email,
            "phone_number": st.session_state.pf_phone,
            "current_city": st.session_state.pf_city,
            "current_state": st.session_state.pf_state,
            "current_zip_code": st.session_state.pf_zip,
            "historical_zip_codes": st.session_state.pf_historical_zips,
            "relational_entities": ([{
                "name": st.session_state.pf_associated_name.strip(),
                "shared_historical_addresses": [line.strip() for line in st.session_state.pf_shared_addresses.splitlines() if line.strip()],
                "shared_phone_numbers": [line.strip() for line in st.session_state.pf_shared_phones.splitlines() if line.strip()],
                "shared_store_loyalty_vectors": [line.strip() for line in st.session_state.pf_shared_loyalty.splitlines() if line.strip()],
            }] if st.session_state.pf_associated_name.strip() else []),
        },
    )
    # Scorched-earth cache clear: remove all stale data on profile update
    st.cache_data.clear()
    for key in list(st.session_state.keys()):
        if any(x in key for x in ["osint", "footprint", "email_results", "audit", "exposure", "facial"]):
            st.session_state.pop(key, None)
    # Signal to Master Dashboard: new or changed profile, trigger auto-scan.
    # The flag is consumed after scan completes so we don't rescan on every rerun.
    st.session_state.profile_saved_auto_scan = True


def render():
    st.title("👤 Profile & Setup")
    st.caption("Enter your personal and contact details. All data is encrypted at rest (Fernet) and stored locally.")
    _seed_profile_fields()

    with st.container(border=True):
        with st.form("profile_form"):
            name_cols = st.columns(2)
            name_cols[0].text_input("👤 First", key="pf_first_name", placeholder="Jane")
            name_cols[1].text_input("👤 Last", key="pf_last_name", placeholder="Doe")

            contact_cols = st.columns(2)
            contact_cols[0].text_input("✉️ Email", key="pf_email", placeholder="you@example.com")
            contact_cols[1].text_input("📱 Phone", key="pf_phone", placeholder="Optional")

            target_cols = st.columns(2)
            target_cols[0].text_input("👤 Username / handle", key="pf_handle", placeholder="Optional")
            target_cols[1].text_input("🌐 Domain", key="pf_domain", placeholder="Optional, e.g. example.com")

            location_cols = st.columns(2)
            location_cols[0].text_input("🏙️ City", key="pf_city", placeholder="New York")
            location_cols[1].text_input("📍 State", key="pf_state", placeholder="NY")

            with st.expander("🕰️ Previous names & addresses"):
                extra_cols = st.columns(2)
                extra_cols[0].text_input("👤 Middle", key="pf_middle_name", placeholder="Optional")
                extra_cols[1].number_input(
                    "🎂 Born", min_value=1900, max_value=2026, step=1, format="%d", key="pf_birth_year", placeholder="YYYY"
                )
                st.text_input("📮 ZIP", key="pf_zip", placeholder="Optional")
                st.text_area("📮 Prior ZIPs", key="pf_historical_zips", placeholder="94105, 10001", height=80)

            with st.expander("🔗 Household & Relational Entities (Ex-Spouses, Co-habitants, Shared Addresses)"):
                st.caption("Optional local-only mapping for former household and shared-account relationships.")
                st.text_input("👤 Associated Individual Full Name", key="pf_associated_name", placeholder="Ex-spouse or former co-habitant")
                st.text_area("🏠 Historical Shared Addresses (Street, City, State, ZIP)", key="pf_shared_addresses", placeholder="One address per line", height=90)
                st.text_area("📞 Shared Landlines / Phone Numbers", key="pf_shared_phones", placeholder="One per line", height=90)
                st.text_area("🛍️ Shared Store Card / Loyalty Vectors (Optional)", key="pf_shared_loyalty", placeholder="Retailer or loyalty-account relationship", height=90)

            submitted = st.form_submit_button("💾 Save", type="primary", width="stretch")
            if submitted:
                if not st.session_state.pf_first_name or not st.session_state.pf_last_name:
                    st.error("First and last name are required.")
                elif st.session_state.pf_email and not is_valid_email(st.session_state.pf_email):
                    st.error("That email address doesn't look valid.")
                else:
                    _save_profile()
                    # Force immediate navigation to results page
                    st.session_state.pending_nav = _MODE_RESULTS
                    st.toast("Profile saved! Navigating to results…")
                    st.rerun()

    if runtime_mode.facial_recognition_enabled():
        with st.container(border=True):
            st.markdown("##### 🧬 Biometric verification (optional)")
            st.caption(
                "Upload a clear photo of yourself and the Master Dashboard will suggest — never "
                "auto-confirm — whether a discovered account's avatar looks like you. Nothing here "
                "is written to disk: the photo lives only in this browser session and is gone when "
                "you close the tab."
            )
            uploaded = st.file_uploader(
                "Upload Master Face Image", type=["jpg", "jpeg", "png"],
                key="master_face_uploader",
                help="Used only in-memory for on-device comparison against discovered avatars.",
            )
            if uploaded is not None:
                st.session_state.master_face_image_bytes = uploaded.getvalue()
                st.image(uploaded, width=96, caption="Master photo (session only)")
            if st.session_state.get("master_face_image_bytes") and st.button(
                "🗑️ Remove master photo", key="clear_master_face"
            ):
                st.session_state.master_face_image_bytes = None
                st.rerun()
            if not facial_recognition.facial_recognition_available():
                st.caption(
                    "⚠️ The optional `deepface` dependency isn't installed, so matching won't run "
                    "yet — the photo can still be uploaded, but no suggestion will be shown until "
                    "it's available."
                )
    else:
        st.session_state.master_face_image_bytes = None

    action_cols = st.columns([2, 1])
    if action_cols[0].button(
        "🔍 Open Master Intelligence Dossier", type="primary", width="stretch",
        disabled=not st.session_state.user_name,
    ):
        _switch_to(_MODE_RESULTS)
    if action_cols[1].button("🧪 Try a demo profile", width="stretch"):
        _load_demo_profile()
        st.toast("Demo profile loaded -- click Save to use it.")
        st.rerun()

    stale_count = sum(
        1 for entry in exposure_store.get_all_checks(runtime_mode.db_path()).values()
        if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS)
    )
    if stale_count:
        item_word = "item" if stale_count == 1 else "items"
        st.warning(f"⏰ {stale_count} {item_word} on your Master Dashboard haven't been rechecked in {config.RECHECK_STALE_DAYS}+ days.")

    requests = get_all_requests(runtime_mode.db_path())
    total = len(requests)
    overdue = sum(1 for r in requests if r["is_overdue"])
    complete = sum(1 for r in requests if r["status"] == "Complete")
    active = total - complete

    with st.container(border=True):
        st.markdown("##### 📬 Your Opt-Out Campaign")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("🔴 Overdue", overdue)
        col2.metric("🟡 Active", active)
        col3.metric("🟢 Complete", complete)
        col4.metric("📊 Total tracked", total)

        if overdue:
            st.error(f"🚨 {overdue} request(s) are past their statutory deadline -- check the Campaign Tracker to follow up.")

        active_requests = [r for r in requests if r["status"] != "Complete"]
        if not active_requests:
            st.caption("Nothing active yet -- generate a letter below to start your first request.")
        else:
            for r in sorted(active_requests, key=lambda r: r["deadline"]):
                window = max(r["response_window_days"], 1)
                elapsed = window - r["days_remaining"]
                progress = min(max(elapsed / window, 0.0), 1.0)
                label = f"{r['broker_name']} — due {r['deadline']}"
                if r["is_overdue"]:
                    label += f" (overdue by {abs(r['days_remaining'])}d)"
                else:
                    label += f" ({r['days_remaining']}d left)"
                st.progress(progress, text=label)

        action_cols = st.columns(2)
        if action_cols[0].button("✉️ Generate a letter", width="stretch"):
            _switch_to(_MODE_LETTERS)
        if action_cols[1].button("📈 Open full tracker", width="stretch"):
            _switch_to(_MODE_TRACKER)
