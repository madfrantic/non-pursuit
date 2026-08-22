"""OSINT Footprint — sweep one handle across the full merged site registry.

The UI surface for utils/recon_engine.py. That engine already does the hard
part (async aiohttp, bounded concurrency, per-host caps, token-bucket rate
limiting, SSRF guard, capped body reads) across the ~3000-site registry
utils/site_registry.py merges out of WhatsMyName, Sherlock and Maigret. Until
this page existed the engine had no way into the app: components/footprint.py
covers only the ~700-site WhatsMyName list through the older synchronous
scanner, and nothing rendered the merged registry at all.

WHY A DATAFRAME AND NOT CARDS

components/footprint_matrix.py draws st.metric cards in a column grid. That
reads well for the 15-row simulated matrix it was built for and falls apart
at 3000: Streamlit re-runs the whole script per widget interaction, so three
thousand card widgets are rebuilt on every keystroke. One st.dataframe is a
single widget holding the same rows, sorts and searches client-side, and the
filters below it cut the row count before it ever reaches the browser.

REGISTRY LOADING IS READ-ONLY HERE

This page reads data/sites-unified.json directly and never calls
site_registry.ensure_registry() on render. ensure_registry() fetches three
upstreams and, when every fetch fails AND no cache exists, writes the
resulting empty registry over sites-unified.json -- which under a
network-blocked test run would silently replace the real 2971-site file with
an empty one. A rebuild is therefore an explicit button, never a side effect
of opening the page.
"""
from components import page_shell

db_path = page_shell.setup("OSINT Footprint", "🕸️")

import json
import os

import pandas as pd
import streamlit as st

import recon_engine
import runtime_mode
import site_registry
from footprint_scanner import CONFIRMED, ERROR, NOT_FOUND, POSSIBLE

REGISTRY_PATH = os.path.join(page_shell.ROOT_DIR, "data", "sites-unified.json")

_STATE_ROWS = "osint_footprint_rows"
_STATE_HANDLE = "osint_footprint_handle"
_STATE_SUMMARY = "osint_footprint_summary"

# Ordered worst-news-first: a CONFIRMED hit is what the operator opened this
# page for, and SKIPPED/NOT_FOUND are the tail they scroll past.
_VERDICT_ORDER = [CONFIRMED, POSSIBLE, ERROR, recon_engine.SKIPPED, NOT_FOUND]
_VERDICT_ICON = {
    CONFIRMED: "🔴", POSSIBLE: "🟠", ERROR: "⚠️",
    recon_engine.SKIPPED: "➖", NOT_FOUND: "⚪",
}

# Redrawing the bar on all ~3000 completions costs more than the scan does.
_PROGRESS_EVERY = 25


@st.cache_data(show_spinner=False)
def load_registry(path: str, mtime: float) -> dict:
    """Read the merged registry off disk. Never rebuilds, never egresses.

    `mtime` is unused in the body and present only as a cache key, so that
    pressing Rebuild below produces a file with a new mtime and invalidates
    this entry instead of serving the pre-rebuild site list forever.
    """
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def registry_or_none():
    try:
        return load_registry(REGISTRY_PATH, os.path.getmtime(REGISTRY_PATH))
    except (OSError, json.JSONDecodeError):
        return None


st.title("🕸️ OSINT Footprint")
st.caption("Sweep one handle across the merged WhatsMyName + Sherlock + Maigret "
           "registry using the async recon engine.")

registry = registry_or_none()

if registry is None:
    page_shell.empty_state(
        "No merged site registry on disk.",
        "data/sites-unified.json is gitignored and built at runtime. "
        "Build it with `python -c \"import sys; sys.path.insert(0,'utils'); "
        "import site_registry; site_registry.ensure_registry()\"` — it fetches "
        "three upstream datasets, so it needs network access.")
    st.stop()

all_sites = registry.get("sites", [])
sources = sorted({s.get("source") for s in all_sites if s.get("source")})
categories = sorted({s.get("cat") for s in all_sites if s.get("cat")})

# --- target and scope -------------------------------------------------

handle = st.text_input(
    "Handle", key="osint_handle_input", placeholder="nonpursuit",
    help="A username, not an email. Sites whose regexCheck rejects this "
         "handle are reported as SKIPPED rather than probed as a false miss.")

with st.expander("Scope", expanded=False):
    scope_left, scope_right = st.columns(2)
    with scope_left:
        chosen_sources = st.multiselect(
            "Datasets", options=sources, default=sources,
            help="Merge precedence is WhatsMyName > Sherlock > Maigret.")
        include_nsfw = st.toggle(
            "Include adult platforms", value=True,
            help="On by default. A forgotten adult-platform account is the "
                 "most damaging kind of exposure, so excluding it reports a "
                 "clean result the sweep did not earn.")
    with scope_right:
        chosen_categories = st.multiselect(
            "Categories", options=categories, default=[],
            help="Empty means every category.")
        extract_metadata = st.toggle(
            "Extract profile metadata", value=True,
            help="Pulls display name, avatar and bio out of bodies already "
                 "in hand. No extra requests.")

    speed_left, speed_right = st.columns(2)
    with speed_left:
        concurrency = st.slider(
            "Concurrency", 10, 200, recon_engine.DEFAULT_CONCURRENCY, step=10,
            help="In-flight requests. Per-host is capped separately at "
                 f"{recon_engine.DEFAULT_PER_HOST} regardless of this.")
    with speed_right:
        timeout = st.slider("Per-site timeout (s)", 5, 60,
                            recon_engine.DEFAULT_TIMEOUT, step=5)

    if st.button("Rebuild registry from upstream", key="osint_rebuild_registry"):
        with st.spinner("Fetching WhatsMyName, Sherlock and Maigret..."):
            try:
                _, status = site_registry.ensure_registry(
                    data_dir=os.path.join(page_shell.ROOT_DIR, "data"), refresh=True)
                load_registry.clear()
                st.success(f"Registry {status}.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
                st.error(f"Rebuild failed, keeping the cached registry: {exc}")

selected = site_registry.select_sites(
    registry,
    include_nsfw=include_nsfw,
    sources=tuple(chosen_sources) or site_registry.SOURCE_PRECEDENCE,
    categories=chosen_categories or None,
)

st.caption(f"**{len(selected):,}** of {len(all_sites):,} sites in scope · "
           f"{site_registry.attribution()}")

# --- run --------------------------------------------------------------

if not runtime_mode.live_scanning_enabled():
    st.warning("Live scanning is disabled in this runtime. Switch to desktop "
               "mode in the sidebar to run a real sweep.")
elif st.button("Run sweep", type="primary", key="osint_run_sweep",
                disabled=not handle.strip()):
    progress = st.progress(0.0, text="Starting...")

    def on_progress(done, total, row):
        if done % _PROGRESS_EVERY and done != total:
            return
        progress.progress(done / max(total, 1), text=f"{done:,} / {total:,} probed")

    with st.spinner(f"Sweeping {len(selected):,} sites..."):
        rows = recon_engine.scan_sync(
            handle, selected, concurrency=concurrency, timeout=timeout,
            extract_metadata=extract_metadata, on_progress=on_progress)

    progress.empty()
    st.session_state[_STATE_ROWS] = rows
    st.session_state[_STATE_HANDLE] = handle.strip()
    st.session_state[_STATE_SUMMARY] = recon_engine.summarize(rows)

# --- results ----------------------------------------------------------

rows = st.session_state.get(_STATE_ROWS)

if not rows:
    page_shell.empty_state("No sweep run yet.",
                           "Enter a handle and press Run sweep.")
    st.stop()

summary = st.session_state.get(_STATE_SUMMARY, {})
by_verdict = summary.get("by_verdict", {})

st.subheader(f"Results for `{st.session_state.get(_STATE_HANDLE, '')}`")

# Five counters, not 3000 cards: the aggregate is the only place a metric
# widget still earns its place at this row count.
cols = st.columns(5)
for col, verdict in zip(cols, _VERDICT_ORDER):
    col.metric(f"{_VERDICT_ICON[verdict]} {verdict.replace('_', ' ').title()}",
               f"{by_verdict.get(verdict, 0):,}")

st.caption(f"{summary.get('sites_probed', 0):,} probed of "
           f"{summary.get('sites_in_registry', 0):,} in scope · median response "
           f"{summary.get('median_response_ms') or '—'} ms")

frame = pd.DataFrame([
    {
        "": _VERDICT_ICON.get(row["verdict"], ""),
        "Verdict": row["verdict"],
        "Platform": row.get("platform", ""),
        "Category": row.get("category", ""),
        "URL": row.get("url", ""),
        "Status": row.get("http_status"),
        "ms": row.get("response_time_ms"),
        "Source": row.get("source", ""),
        "Name": (row.get("metadata") or {}).get("display_name", ""),
        "Bio": (row.get("metadata") or {}).get("bio", ""),
        "Reason": row.get("reason", ""),
    }
    for row in rows
])

# Sort by verdict severity, then platform, so the table opens on the hits.
frame["_rank"] = frame["Verdict"].map(
    {v: i for i, v in enumerate(_VERDICT_ORDER)}).fillna(len(_VERDICT_ORDER))
frame = frame.sort_values(["_rank", "Platform"]).drop(columns="_rank")

filter_left, filter_right = st.columns([1, 2])
with filter_left:
    hits_only = st.toggle("Hits only", value=True,
                          help=f"{CONFIRMED} and {POSSIBLE} rows.")
with filter_right:
    search = st.text_input("Filter", placeholder="platform, category or URL...",
                           label_visibility="collapsed")

view = frame
if hits_only:
    view = view[view["Verdict"].isin([CONFIRMED, POSSIBLE])]
if search:
    needle = search.strip().lower()
    haystack = (view["Platform"] + " " + view["Category"] + " " +
                view["URL"] + " " + view["Source"]).str.lower()
    view = view[haystack.str.contains(needle, regex=False, na=False)]

st.caption(f"Showing {len(view):,} of {len(frame):,} rows.")

st.dataframe(
    view,
    width="stretch",
    hide_index=True,
    height=560,
    column_config={
        "": st.column_config.TextColumn(width="small"),
        "URL": st.column_config.LinkColumn("URL", display_text="open"),
        "Status": st.column_config.NumberColumn("Status", format="%d"),
        "ms": st.column_config.NumberColumn("ms", format="%d"),
        "Bio": st.column_config.TextColumn("Bio", width="medium"),
    },
)

st.download_button(
    "Download CSV", data=view.to_csv(index=False).encode("utf-8"),
    file_name=f"footprint-{st.session_state.get(_STATE_HANDLE, 'scan')}.csv",
    mime="text/csv")

st.caption("Verdicts come from the live sweep, not the simulated matrix. "
           "A POSSIBLE row means detection was ambiguous — confirm it by hand "
           "before it goes into a demand letter.")
