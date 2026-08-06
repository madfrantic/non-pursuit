"""
Configuration for Non-Pursuit.
"""

# Application settings
APP_TITLE = "Non-Pursuit"
APP_TAGLINE = "They chase. You enforce."
APP_ICON = "🛡️"  # fallback emoji, kept for any spot the real logo doesn't fit
APP_LOGO_PATH = "assets/logo_shield.png"
APP_WORDMARK_PATH = "assets/logo_wordmark.png"
APP_LAYOUT = "wide"

# Theme settings — also mirrored in .streamlit/config.toml, which is what
# actually themes the built-in Streamlit widgets. These are read by the
# small amount of custom CSS we still ship (for the link-button styling
# Streamlit doesn't cover natively), so update both files together.
THEME_BASE = "dark"
PRIMARY_COLOR = "#2563eb"
BACKGROUND_COLOR = "#0b1120"
SECONDARY_BACKGROUND_COLOR = "#16213a"
TEXT_COLOR = "#e2e8f0"
SUCCESS_COLOR = "#22c55e"
ERROR_COLOR = "#ef4444"

# File paths
BROKERS_CSV_PATH = "data/brokers.csv"
TEMPLATE_PATH = "templates/ccpa_deletion_demand.j2"
TRACKER_DB_PATH = "data/tracker.db"

# Statutory response window CCPA gives a business/broker to act on a deletion
# request. Used by the tracker to flag a request as overdue.
CCPA_RESPONSE_WINDOW_DAYS = 45

# Demo profile data (fake — safe to load for a walkthrough)
DEMO_PROFILE = {
    "name": "John Doe",
    "email": "john.doe@example.com",
    "location": "San Francisco, CA",
    "record_url": "https://example.com/record/12345",
}

# External links
GOOGLE_PII_REMOVAL_URL = "https://support.google.com/websearch/contact/privacy_concern"
NY_COURT_EXPUNGEMENT_URL = "https://www.nycourts.gov/courthelp/criminal/expungement.shtml"

# California's Delete Request and Opt-Out Platform (DROP) — live since Jan 1,
# 2026; registered CA data brokers have been required to process DROP
# requests since Aug 1, 2026. Non-Pursuit points CA residents there first and
# positions itself as the tool for what DROP doesn't reach: unregistered
# brokers, non-CA residents, and escalation when a broker misses its window.
CA_DROP_URL = "https://privacy.ca.gov"

# A URL-encoded mailto body longer than this is unreliable across clients —
# Outlook and several mobile mail apps have been observed truncating mailto
# bodies well under this. Above the threshold, the UI should steer the user
# to download + paste instead of the one-click mailto button.
MAILTO_SAFE_LENGTH = 1800

# A privacy tool that keeps user data forever is a bad look. The tracker's
# only free-text field that could hold anything identifying is `notes` — once
# a request has been Complete for this many days, its notes are cleared
# automatically. Everything else (dates, status, broker, deadline) stays, so
# your own history/metrics remain intact.
PII_RETENTION_DAYS = 30

# Self-search results (broker listings, breach/phone/social answers) used to
# live only in st.session_state and reset every time the browser tab closed
# -- there was no way to know how long ago something was actually checked.
# Once a check is older than this many days, Results/Dashboard flag it as
# due for a recheck instead of silently trusting a stale answer.
RECHECK_STALE_DAYS = 30
EXPOSURE_DB_PATH = "data/tracker.db"

# Free, already-built alerting services -- rather than reinventing breach or
# web-mention monitoring, point users at the real ones.
GOOGLE_ALERTS_URL = "https://www.google.com/alerts"
HIBP_NOTIFY_URL = "https://haveibeenpwned.com/NotifyMe"
