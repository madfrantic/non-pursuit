"""
Sidebar navigation shared by app.py and the pages/ multi-page app.

Streamlit's automatic page list is switched off (.streamlit/config.toml) so
it cannot draw a second nav of raw filenames -- "1_Vault",
"10_OSINT_Toolkit" -- beside the app's own. But that list was also the only
route between pages, and it is the reason these links have to render in two
places rather than one: Streamlit runs every file in pages/ as its own
top-level script, so app.py's sidebar simply does not exist while a page is
open. A page that rendered no links of its own was a dead end -- no way back
to the main app, and a refresh reloaded the page you were stuck on.

Hence HOME_PAGE: every pages/ script links back to the entrypoint, which is
the one link the automatic list used to provide for free.
"""
import logging

import streamlit as st

_log = logging.getLogger("nav")

# Streamlit resolves page links against the entrypoint script, so this is
# the main app's own path as it appears to st.page_link.
HOME_PAGE = "app.py"

MPA_SECTIONS = [
    ("Deep OSINT", [
        ("pages/8_OSINT_Footprint.py", "🕸️ OSINT Footprint",
         "Sweep one handle across the full merged ~3,000-site registry (desktop runtime)."),
        ("pages/10_OSINT_Toolkit.py", "🧰 OSINT Toolkit",
         "Reference index of OSINT tools and datasets. Scans nothing."),
    ]),
    ("Broker agent console", [
        ("pages/1_Vault.py", "🔐 Vault", "Master password and encrypted-ledger controls."),
        ("pages/2_Scanner.py", "🔍 Scanner", "Scan brokers for listings matching your profile."),
        ("pages/3_Queue.py", "📋 Review queue", "Findings awaiting your triage."),
        ("pages/4_Evidence.py", "🧾 Evidence chain", "Captured proof and its SHA256 chain."),
        ("pages/5_Removals.py", "✉️ Removals", "Opt-out requests — every submission stays manual."),
        ("pages/6_Verification.py", "✅ Verification", "Re-check whether a broker actually delisted you."),
        ("pages/7_Reports.py", "📊 Reports", "Ledger reporting and exports."),
        ("pages/9_Settings.py", "⚙️ Settings", "Agent scheduler and runtime configuration."),
    ]),
]


def render_page_links(include_home: bool = False) -> None:
    """Draw the page links in the sidebar.

    `include_home` adds the link back to the main app, and belongs on every
    pages/ script: without it a page has no exit. app.py leaves it off --
    it is the home page, so a link to itself would be noise.

    Unlike app.py's tool radio, these can carry real section headers:
    st.caption draws above the group, where st.radio's captions are pinned
    under each individual option.
    """
    if include_home:
        st.sidebar.markdown("---")
        _page_link(
            HOME_PAGE,
            "⬅️ Back to the main app",
            "Identity profile, intelligence dossier, letters and tracking.",
        )

    for section, links in MPA_SECTIONS:
        st.sidebar.markdown("---")
        st.sidebar.caption(section)
        for page_path, label, help_text in links:
            _page_link(page_path, label, help_text)


def _page_link(page_path: str, label: str, help_text: str) -> bool:
    """One sidebar link, which must never take down the page drawing it.

    st.page_link resolves against Streamlit's page registry, and that
    registry is only populated when the app was launched through its
    entrypoint. Running a page script directly -- which is exactly what
    AppTest.from_file does, and what `streamlit run pages/9_Settings.py`
    would do -- leaves it empty and st.page_link raises KeyError deep in
    Streamlit rather than a catchable StreamlitAPIException.

    Navigation chrome is not worth a dead page, so a link that cannot be
    resolved is dropped. Returns whether it was drawn, so a caller (or a
    test) can tell the difference between "drawn" and "skipped".

    Dropping a link silently is only safe because
    test_every_page_in_the_mpa_is_reachable_from_the_sidebar checks
    MPA_SECTIONS against pages/ on disk -- a typo'd or deleted path is
    caught there, at the data level, rather than quietly vanishing from
    the sidebar the way the whole page list did.
    """
    try:
        st.sidebar.page_link(page_path, label=label, help=help_text)
        return True
    except Exception as exc:
        _log.debug("Skipping unresolvable page link %s (%s)", page_path, exc)
        return False
