# NON-PURSUIT — CANONICAL ARCHITECTURE

**Canonical Path:** `/home/b0t/PORTFOLIO/ventures/non_pursuit`  
**Classification:** VENTURE / CONSUMER DATA PRIVACY PLATFORM  
**Status:** ARCHITECTURE_FROZEN  

---

## 1. Executive Summary & Purpose
NON-PURSUIT is an automated consumer data privacy, footprint reconnaissance, and statutory opt-out delivery platform. It enables individuals to discover their exposed personal identifiable information (PII) across hundreds of data brokers, people-search engines, and OSINT registries, automate the compilation of formal opt-out and deletion request templates (`LEGAL_REQUEST / STATUTORY REQUEST TEMPLATE`) under the California Consumer Privacy Act (CCPA / CPRA) and the General Data Protection Regulation (GDPR), and monitor broker compliance against jurisdiction-specific statutory deadlines.

---

## 2. Technical & Domain Architecture

```
+───────────────────────────────────────────────────────────────+
|                  NON-PURSUIT Application Layer                |
|      - Streamlit Executive Interface (app.py)                 |
|      - FastAPI REST Endpoints (api/)                          |
|      - Non-Pursuit CLI Suite (main.py, agent_recon.py)        |
+───────────────────────────────+───────────────────────────────+
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
+────────────────+     +────────────────+     +────────────────+
| Recon Engine   |     | Opt-Out Engine |     | Vault & Review |
| - OSINT Catalog|     | - Template Gen |     | - Client-Side  |
| - Holehe Scan  |     | - Juris. Router|     |   Encrypted PII|
| - Maigret Data |     | - Playwright   |     |   Vault (Fernet|
| - Sherlock Data|     | - Mailto Bldr  |     | - SHA-256 Chain|
|                |     |                |     | - Review Queue |
|                |     |                |     |   (tracker.db) |
+────────────────+     +────────────────+     +────────────────+
```

### Core Subsystems
1. **Reconnaissance & OSINT Engine (`importers/`, `utils/`, `data/`):**
   - Integrates extensive broker datasets (`brokers.csv`, `broker_probes.json`, `sites-unified.json`, `osint_catalog.json`).
   - Scans digital footprints across social platforms, databases, and leak registries while operating air-gapped during testing.
2. **Statutory Opt-Out & Template Compiler (`components/`, `utils/`):**
   - `jurisdiction_router.py`: Distinguishes statutory frameworks and rules:
     - **CCPA / CPRA (`CURRENT_VERIFIED`):** Base response period is 45 calendar days. Extendable once by an additional 45 days (up to 90 days total) when reasonably necessary with written notice to consumer within initial 45 days (Cal. Civ. Code § 1798.130(a)(2)).
     - **GDPR (`CURRENT_VERIFIED`):** Base response period is 1 month without undue delay (Art. 12(3)). Not normalized to a fixed 30-day constant. Extendable by up to 2 additional months (up to 3 months total) taking into account complexity and number of requests.
     - **New York Hybrid (`JURISDICTION_SPECIFIC`):** 30 calendar days for FCRA § 380-f reinvestigation; SHIELD Act § 899-bb security demand.
     - **Generic Fallback (`UNVERIFIED`):** 45 calendar days discretionary window based on published broker policy.
   - `letter_compiler.py`: Dynamically drafts formal statutory request templates (`LEGAL_REQUEST / STATUTORY REQUEST TEMPLATE`) citing applicable legal provisions, subject to jurisdiction and factual consumer eligibility.
   - `mailto_builder.py`: Compiles standardized RFC-compliant mailto links for consumer review.
3. **Execution & Browser Automation (`components/optout_engine.py`):**
   - Playwright browser automation drivers navigating data broker suppression forms, reCAPTCHA detection, and automated submission confirmation recording.
4. **Client-Side Encrypted PII Vault (`utils/crypto.py`):**
   - Local client-side encrypted PII vault (`LOCAL_ENCRYPTED_PII_VAULT`) using Fernet symmetric encryption with PBKDF2 HMAC-SHA256 key derivation (`vault.salt`, `vault.verifier`).
   - Consumer PII (names, SSNs, driver licenses, addresses) is encrypted at rest locally; secret isolation prevents plain-text leakage to application logs or database records.
5. **Tracking & Audit Queue (`data/tracker.db`, `data/review_queue.db`):**
   - Full SHA-256 evidence chain of custody with UTC timestamps logging request records, broker recipients, delivery confirmation hashes, and statutory clock metadata.

---

## 3. Data Contracts & Evidence
- **Test Suite:** 1,088/1,088 unit and integration tests passing (`tests/`).
- **Pass Rate:** 100% (0 failures, 0 errors, 0 skipped).
- **Database Integrity:** `tracker.db`, `review_queue.db`, and `usage_metrics.db` verified via `PRAGMA integrity_check;` (`ok`).
- **Statutory Deadlines:** Detailed rule records explicitly support CCPA 45-day extension logic and GDPR 1-month statutory clock.
