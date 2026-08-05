"""
Configuration for Non-Pursuit.
"""

# Application settings
APP_TITLE = "Non-Pursuit"
APP_TAGLINE = "Take yourself off the market."
APP_ICON = "🛡️"
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

# Privacy Dashboard settings
PRIVACY_SCORE_CATEGORIES = {
    "social_security": {"weight": 25, "label": "Social Security Number", "icon": "🔑"},
    "email": {"weight": 20, "label": "Email Address", "icon": "📧"},
    "phone": {"weight": 15, "label": "Phone Number", "icon": "📱"},
    "address": {"weight": 20, "label": "Physical Address", "icon": "🏠"},
    "social_media": {"weight": 10, "label": "Social Media Profiles", "icon": "👤"},
    "data_brokers": {"weight": 10, "label": "Data Broker Listings", "icon": "🏢"},
}

# Known data breach databases (for dark web monitoring simulation)
BREACH_CHECK_SERVICES = {
    "haveibeenpwned": "https://haveibeenpwned.com",
    "dehashed": "https://dehashed.com",
    "firefox_monitor": "https://monitor.firefox.com",
}

# Search engines to check for PII exposure
SEARCH_ENGINE_URLS = {
    "google": "https://www.google.com/search?q=",
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "yahoo": "https://search.yahoo.com/search?p=",
}

PRIVACY_SCORE_THRESHOLDS = {
    "excellent": 80,
    "good": 60,
    "poor": 40,
    "critical": 20,
}
