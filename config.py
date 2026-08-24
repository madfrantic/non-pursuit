"""
Configuration for Non-Pursuit.
"""

# Application settings
APP_TITLE = "Non-Pursuit"

# The one headline, lowercase on purpose, and the only place it is written.
# It replaced two competing lines -- "Take yourself off the market." in the
# pitch deck and "They chase. You enforce." in the app footer -- which put
# the product under two different names in the two places a visitor was
# most likely to read it.
APP_HEADLINE = "non-pursuit. the sovereign agent"
APP_TAGLINE = APP_HEADLINE
APP_ICON = "🛡️"  # fallback emoji, kept for any spot the real logo doesn't fit
APP_LOGO_PATH = "assets/logo_shield.png"
APP_WORDMARK_PATH = "assets/logo_wordmark.png"

# What st.logo draws at the top of the sidebar. The wordmark is a text
# image, and it fought with the headline directly beneath it -- the mark
# says the name once and lets the headline carry the words.
APP_TOPMARK_PATH = APP_LOGO_PATH
APP_LAYOUT = "wide"

# The agent console's own name. The section is "Sovereign Engine"
# everywhere the app can actually be reached over the web; the older
# "Broker agent console" label names the console that only the separately
# installed desktop build ships, so it appears only on that runtime.
SOVEREIGN_ENGINE_LABEL = "Sovereign Engine"
DESKTOP_CONSOLE_LABEL = "Broker agent console"

# Theme settings — also mirrored in .streamlit/config.toml, which is what
# actually themes the built-in Streamlit widgets. Read by data_export.py
# and pdf_generator.py to keep exported PDFs on the same palette as the
# app itself.
# Palette source: demo_pitch.html's :root block, by way of
# components/theme.py -- the app, the deck and the exported PDFs are all
# the same document and had drifted onto three different blues.
THEME_BASE = "dark"
PRIMARY_COLOR = "#D4AF37"          # detective brass
BACKGROUND_COLOR = "#0B1325"       # midnight cruiser navy
SECONDARY_BACKGROUND_COLOR = "#152238"  # precinct slate
TEXT_COLOR = "#E8E2D4"             # bone
SUCCESS_COLOR = "#22c55e"
ERROR_COLOR = "#FF3B30"            # stamp red

# File paths
BROKERS_CSV_PATH = "data/brokers.csv"
TEMPLATE_PATH = "utils/statutory_letters/ccpa_deletion_demand.j2"
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
TRACKER_DB_PATH = os.path.join(_ROOT, "data", "tracker.db")

# Statutory response window CCPA gives a business/broker to act on a deletion
# request. Used by the tracker to flag a request as overdue, and by the
# campaign ledger to set each demand's statutory deadline.
CCPA_RESPONSE_WINDOW_DAYS = 45

# Delisting campaign ledger (broker_campaigns table). Points at the same
# local SQLite file as the tracker, exposure checks and profile -- these are
# tables, not separate databases. Named separately so call sites read
# clearly, but routed through runtime_mode.db_path() in the app so demo mode
# still gets its per-session temp file instead of a shared one.
CAMPAIGNS_DB_PATH = os.path.join(_ROOT, "data", "tracker.db")

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

# State-specific starting points, shown on Results before the broker-by-
# broker work. Each entry points at something real -- either an external
# resource (action_url) or an in-app mode already built for that state
# (action_mode, must exactly match a sidebar nav label in app.py). New
# York doesn't have its own DROP-equivalent yet (S9088/A9642 registration
# bill still in committee as of 2026, not law) so it points at the
# closest real, already-built thing instead: this app's own expungement
# guidance, which is a different kind of resource (criminal record
# sealing, not data-broker deletion) and is described as such rather than
# implied to be equivalent. Add more states here only once their resource
# has been verified to actually exist -- never a placeholder guess.
STATE_RESOURCES = {
    "California": {
        "blurb": (
            "The state's own deletion tool, **DROP**, reaches every *registered* data "
            "broker with one request, and brokers have been required to process DROP "
            "requests since Aug 1, 2026. Start there — use Non-Pursuit for brokers "
            "that aren't registered, or to escalate if a broker misses its window."
        ),
        "action_label": "Open DROP (privacy.ca.gov)",
        "action_url": CA_DROP_URL,
    },
    "New York": {
        "blurb": (
            "New York doesn't have a centralized data-broker deletion tool yet — a "
            "registration + one-shot-deletion bill (S9088 / A9642) was introduced in "
            "January 2026 and is still in committee, not law. If it's a criminal "
            "record you're trying to seal instead, this app's own NY Expungement "
            "page screens a conviction against all five New York sealing pathways "
            "(CPL 160.50, 160.55, 160.57 Clean Slate, 160.58 and 160.59) and drafts "
            "a CPL 160.59 motion — screening guidance, not a legal determination, "
            "and a different kind of resource than DROP."
        ),
        "action_label": "Go to NY Expungement",
        "action_mode": "⚖️ NY Expungement",
    },
}

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
EXPOSURE_DB_PATH = os.path.join(_ROOT, "data", "tracker.db")

# Baseline identity/location profile (target_profile table) -- same local
# SQLite file as the tracker and exposure checks.
PROFILE_DB_PATH = os.path.join(_ROOT, "data", "tracker.db")

# Free, already-built alerting services -- rather than reinventing breach or
# web-mention monitoring, point users at the real ones.
GOOGLE_ALERTS_URL = "https://www.google.com/alerts"
HIBP_NOTIFY_URL = "https://haveibeenpwned.com/NotifyMe"

# Digital footprint scanner (utils/footprint_scanner.py). The WhatsMyName
# site list is fetched on first use and cached here rather than committed --
# it's CC BY-SA 4.0, so not vendoring it keeps ShareAlike'd content out of
# git history, and the list genuinely churns as sites die and detection
# strings get fixed upstream. Gitignored alongside the rest of data/.
WMN_DATASET_PATH = "data/wmn-data.json"

# The merged OSINT tool catalog, built by scripts/import_osint_tools.py from
# ten upstream sources. Deliberately a JSON file and not a table in
# tracker.db: it holds no personal data, it is reproducible from upstream at
# any time, and keeping it out of the tracker keeps public reference data
# clear of the retention rules and the schema-migration path that exist for
# the user's own case file. Committed rather than gitignored -- unlike the
# WhatsMyName list above it carries no ShareAlike obligation, and shipping it
# means the page works on a clean checkout without a five-minute import.
OSINT_CATALOG_PATH = "data/osint_catalog.json"

# Discovered accounts live in the same local SQLite file as everything else.
FOOTPRINT_DB_PATH = os.path.join(_ROOT, "data", "tracker.db")

# Bounded concurrency for a scan. The default scan is the full ~700-site
# sweep, so 50 in flight is what keeps it to roughly a minute instead of
# five. Sockets are not the constraint at this level -- 50 is far under
# the usual 1024 file-descriptor limit -- the constraint is how much of
# that concurrency lands on any single host, which is what PER_HOST caps.
# The sweep is wide (one or two requests each across hundreds of distinct
# hosts), never deep on one.
FOOTPRINT_CONCURRENCY = 50
FOOTPRINT_PER_HOST_CONCURRENCY = 4
FOOTPRINT_TIMEOUT_SECONDS = 15

# Diagnostic log, separate from data/ (which is user data, backed up via the
# JSON/CSV/PDF exports and never written to by anything but the user's own
# actions). The log can still end up holding identifying details caught in
# an exception message, so it's gitignored the same way data/tracker.db is.
LOG_PATH = "logs/non_pursuit.log"

# Feature-usage counters (utils/usage_metrics.py). Deliberately NOT the
# per-session tracker database: a count is only meaningful in aggregate, and
# a per-session store would reset "letters generated" to 1 for every visitor.
# Sharing this file across sessions is safe because it holds event names and
# integers -- the module's allowlist makes it structurally incapable of
# holding a typed value. Gitignored by its own rule (data/ is not ignored
# wholesale -- data/brokers.csv is tracked), but for a different reason than
# tracker.db: not to keep PII out of history, just to stop one machine's
# counters shipping as if they were real usage.
USAGE_METRICS_DB_PATH = os.path.join(_ROOT, "data", "usage_metrics.db")

# Outstanding human work (utils/review_queue.py) -- the steps automation
# deliberately stopped short of: a filled-but-unsubmitted opt-out form, a
# CAPTCHA, a broker with no automator. Its own file rather than tracker.db
# for two reasons: a tracker.db schema change is a Human Validation Zone
# under CLAUDE.md, and a chore list churns daily while a statutory campaign
# does not. Gitignored because its `note` column is free text and will
# eventually hold a name someone pasted in.
REVIEW_QUEUE_DB_PATH = os.path.join(_ROOT, "data", "review_queue.db")

# How old a broker's own last_verified date can get before Results flags the
# row as due for a human to re-confirm its compliance email / opt-out URL /
# notes still work. Much longer than RECHECK_STALE_DAYS (30 days) above --
# that one is about whether *your* exposure answer is still fresh, this one
# is about whether a broker's *contact info* is still fresh, and broker
# contact info drifts far slower than a person's exposure status does.
BROKER_STALE_DAYS = 180

# Evidence chain (utils/optout_engine.py capture_evidence). A screenshot of a
# broker's search results is a page with the user's PII rendered on it, so
# these files are gitignored for the same reason logs/ and data/tracker.db
# are. Each capture's SHA256 is recorded in the ledger's `evidence` table at
# capture time -- that digest, not the PNG, is what makes the file evidence.
EVIDENCE_DIR = "data/evidence"

# Broker-agent ledger (utils/broker_ledger.py): profiles, brokers, scans,
# removals, evidence, activity log. Shares data/tracker.db with the Phase 1
# tables so a removal can be joined to the statutory request it belongs to.
LEDGER_DB_PATH = os.path.join(_ROOT, "data", "tracker.db")

# Autonomous background engine (utils/agent_scheduler.py). Intervals in days.
# Scanning and verification only -- never submission; see the module docstring.
AGENT_SCAN_INTERVAL_DAYS = 7
AGENT_VERIFY_INTERVAL_DAYS = 14
AGENT_SCHEDULER_ENABLED = True
