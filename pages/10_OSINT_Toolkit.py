"""OSINT Toolkit — browse the merged catalog of OSINT tools and datasets.

The read surface for data/osint_catalog.json, which scripts/import_osint_tools.py
builds from ten upstream sources (the OSINT Framework's arf.json, the OSINT
Tools Library, four GitHub projects, and four static service entries). Nothing
on this page fetches anything: the import is an explicit command, so opening
the page can never egress and can never rewrite the catalog.

WHY A DATAFRAME AND A CATEGORY VIEW, NOT CARDS

Same reasoning as pages/8_OSINT_Footprint.py: at ~1300 rows a widget-per-tool
layout rebuilds thousands of widgets on every keystroke, because Streamlit
re-runs the script per interaction. The table is one widget that sorts and
searches client-side. The category view exists for the other way people use a
catalog -- not "find this tool" but "what is there for phone numbers" -- and
it renders one expander per category, so its widget count is bounded by the
number of categories rather than by the number of tools.

FILTERS RUN OUTSIDE THE CACHE

load_catalog() is cached on (path, mtime); every filter below it is a cheap
pandas mask over the already-loaded frame. Caching the filtered result instead
would key the cache on the filter state and re-read the file whenever anyone
moved a control.
"""
from components import page_shell

page_shell.setup("OSINT Toolkit", "🧰")

import os

import pandas as pd
import streamlit as st

import config
import osint_catalog

CATALOG_PATH = os.path.join(page_shell.ROOT_DIR, config.OSINT_CATALOG_PATH)

_ACCESS_ICON = {"free": "🟢", "freemium": "🟡", "paid": "🔴", "unknown": "⚪"}
_STATUS_ICON = {"live": "🟢", "degraded": "🟠", "down": "🔴",
                "deprecated": "⚫", "unknown": "⚪"}
_OPSEC_ICON = {"passive": "🔵", "active": "🟠", "unknown": "⚪"}

# How many category expanders the "By category" view will build at once.
_MAX_CATEGORY_PANELS = 30

# Columns the frame carries for filtering but never shows or exports.
_INTERNAL_COLUMNS = ["_id", "_deprecated", "_local_raw", "_signup_raw"]


@st.cache_data(show_spinner=False)
def load_catalog(path: str, mtime: float) -> dict:
    """Read the catalog off disk.

    `mtime` is unused in the body and present only as a cache key, so that
    re-running the importer invalidates this entry instead of serving the
    previous catalog for the life of the session.
    """
    return osint_catalog.load_document(path) or {}


def catalog_or_none():
    try:
        return load_catalog(CATALOG_PATH, os.path.getmtime(CATALOG_PATH)) or None
    except OSError:
        return None


st.title("🧰 OSINT Toolkit")
st.caption("A merged, deduplicated index of OSINT tools and datasets — "
           "reference material for planning a search, not a scanner.")

document = catalog_or_none()

if not document:
    page_shell.empty_state(
        "No tool catalog on disk yet.",
        "Build it with `python scripts/import_osint_tools.py` — it reads ten "
        "upstream sources and writes data/osint_catalog.json, so it needs "
        "network access and takes about five minutes. Add `--limit 20` for a "
        "quick partial run.")
    st.stop()

tools = document.get("tools", [])
summary = osint_catalog.summarize(document)

# --- header -----------------------------------------------------------

cols = st.columns(4)
cols[0].metric("Tools", f"{summary['tools']:,}")
cols[1].metric("Sources", summary["sources"])
cols[2].metric("Categories", summary["categories"])
cols[3].metric("In 2+ sources", summary["multi_source"],
               help="Tools that more than one upstream catalog lists. Their "
                    "records are composites — see Provenance in the detail "
                    "panel for which source supplied which field.")

generated = (summary.get("generated_at") or "").replace("T", " ").removesuffix("+00:00")
st.caption(f"Imported {generated} UTC · "
           f"{', '.join(f'{k} ({v})' for k, v in summary['by_source'].items())}")

# --- filters ----------------------------------------------------------

frame = pd.DataFrame([
    {
        "Tool": tool.get("name") or "",
        "What it does": tool.get("description") or "",
        "Categories": tool.get("categories") or [],
        "Access": tool.get("access_type") or "unknown",
        "Status": tool.get("status") or "unknown",
        "OPSEC": tool.get("opsec") or "unknown",
        "Input": tool.get("input_type") or "",
        "Output": tool.get("output_type") or "",
        "Local": bool(tool.get("local_install")),
        "Signup": bool(tool.get("registration_required")),
        "API": bool(tool.get("api_available")),
        "URL": tool.get("url") or "",
        "Sources": tool.get("sources") or [],
        "_id": tool.get("id"),
        "_deprecated": bool(tool.get("deprecated")),
        # The tri-state originals. Upstream leaves these null for ~90 tools,
        # and a filter must not read "we don't know" as "no" -- that is the
        # same over-claim the verification engine exists to avoid.
        "_local_raw": tool.get("local_install"),
        "_signup_raw": tool.get("registration_required"),
    }
    for tool in tools
])

deprecated_count = int(frame["_deprecated"].sum())

all_categories = sorted({c for tool in tools for c in tool.get("categories") or []})
all_sources = sorted(summary["by_source"])

with st.container(border=True):
    search = st.text_input(
        "Search", placeholder="name, description, input type…",
        help="Matches the tool name, what it does, and its categories.")

    picked_categories = st.multiselect(
        "Categories", options=all_categories, default=[],
        help="Empty means every category.")

    row = st.container(horizontal=True)
    with row:
        access = st.pills("Access", options=["free", "freemium", "paid", "unknown"],
                          selection_mode="multi", default=[])
        opsec = st.pills("OPSEC", options=["passive", "active", "unknown"],
                         selection_mode="multi", default=[],
                         help="Passive tools do not contact the target; active "
                              "ones do, and can leave traces in the target's logs.")

    toggles = st.container(horizontal=True)
    with toggles:
        hide_deprecated = st.toggle(
            "Hide deprecated", value=True,
            help=f"{deprecated_count} entries are flagged dead or superseded "
                 "upstream.")
        local_only = st.toggle("Runs locally", value=False,
                               help="Self-hosted or CLI tools only. Tools whose "
                                    "upstream does not say are excluded.")
        no_signup = st.toggle("No signup", value=False,
                              help="Only tools upstream confirms need no account. "
                                   "A tool with no answer either way is excluded "
                                   "rather than assumed open.")
        picked_sources = st.multiselect(
            "Sources", options=all_sources, default=[],
            label_visibility="collapsed", placeholder="All sources")

view = frame
if hide_deprecated:
    view = view[~view["_deprecated"]]
if local_only:
    view = view[view["_local_raw"].apply(lambda v: v is True)]
if no_signup:
    # `is False`, not `not v` -- None means unstated, not "no account needed".
    view = view[view["_signup_raw"].apply(lambda v: v is False)]
if access:
    view = view[view["Access"].isin(access)]
if opsec:
    view = view[view["OPSEC"].isin(opsec)]
if picked_categories:
    wanted = {c.lower() for c in picked_categories}
    view = view[view["Categories"].apply(
        lambda cats: bool(wanted & {c.lower() for c in cats}))]
if picked_sources:
    wanted_sources = set(picked_sources)
    view = view[view["Sources"].apply(lambda s: bool(wanted_sources & set(s)))]
if search:
    needle = search.strip().lower()
    haystack = (
        view["Tool"] + " " + view["What it does"] + " " +
        view["Categories"].apply(" ".join) + " " + view["Input"]
    ).str.lower()
    view = view[haystack.str.contains(needle, regex=False, na=False)]

st.caption(f"Showing **{len(view):,}** of {len(frame):,} tools.")

if view.empty:
    page_shell.empty_state("Nothing matches those filters.",
                           "Clear a filter or widen the search.")
    st.stop()

# --- results ----------------------------------------------------------

layout = st.segmented_control(
    "View", options=["Table", "By category"], default="Table",
    label_visibility="collapsed")

if layout == "By category":
    # Tools sit in several categories at once, so filtering to "Conflict OSINT"
    # still leaves its members' other categories in the grouping. When the user
    # has named categories, those are the only panels they asked for.
    wanted_panels = {c.lower() for c in picked_categories}

    grouped: dict = {}
    for record in view.to_dict("records"):
        for category in record["Categories"] or ["Uncategorised"]:
            if wanted_panels and category.lower() not in wanted_panels:
                continue
            grouped.setdefault(category, []).append(record)

    ordered = sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    # A collapsed expander still builds its contents, so 240 categories would
    # mean 240 dataframe widgets constructed on every rerun whether or not
    # anyone opened one. Densest categories first, capped; the Categories
    # filter above is how you reach the rest.
    if len(ordered) > _MAX_CATEGORY_PANELS:
        st.caption(f"Showing the {_MAX_CATEGORY_PANELS} largest of "
                   f"{len(ordered)} categories — use the Categories filter "
                   f"above to open a specific one.")
        ordered = ordered[:_MAX_CATEGORY_PANELS]

    for category, records in ordered:
        with st.expander(f"{category} · {len(records)}"):
            st.dataframe(
                pd.DataFrame(records)[
                    ["Tool", "What it does", "Access", "OPSEC", "URL"]],
                hide_index=True,
                column_config={
                    "Tool": st.column_config.TextColumn(pinned=True),
                    "What it does": st.column_config.TextColumn(width="large"),
                    "URL": st.column_config.LinkColumn("Link", display_text="open"),
                },
            )
else:
    display = view.drop(columns=_INTERNAL_COLUMNS)
    event = st.dataframe(
        display,
        hide_index=True,
        height=520,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Tool": st.column_config.TextColumn(pinned=True),
            "What it does": st.column_config.TextColumn(width="large"),
            "Categories": st.column_config.ListColumn("Categories", width="medium"),
            "Sources": st.column_config.ListColumn("Sources", width="small"),
            "Local": st.column_config.CheckboxColumn("Local"),
            "Signup": st.column_config.CheckboxColumn("Signup"),
            "API": st.column_config.CheckboxColumn("API"),
            "URL": st.column_config.LinkColumn("Link", display_text="open"),
        },
    )

    # --- detail panel -------------------------------------------------

    picked = event.selection.rows if event and event.selection else []
    if picked:
        tool_id = view.iloc[picked[0]]["_id"]
        record = next((t for t in tools if t.get("id") == tool_id), None)
        if record:
            with st.container(border=True):
                st.subheader(record.get("name") or "—")
                if record.get("url"):
                    st.markdown(f"[{record['url']}]({record['url']})")
                if record.get("description"):
                    st.write(record["description"])

                facts = st.columns(3)
                facts[0].markdown(
                    f"**Access** {_ACCESS_ICON.get(record.get('access_type'), '')} "
                    f"{record.get('access_type', 'unknown')}\n\n"
                    f"**Status** {_STATUS_ICON.get(record.get('status'), '')} "
                    f"{record.get('status', 'unknown')}")
                facts[1].markdown(
                    f"**Input** {record.get('input_type') or '—'}\n\n"
                    f"**Output** {record.get('output_type') or '—'}")
                facts[2].markdown(
                    f"**OPSEC** {_OPSEC_ICON.get(record.get('opsec'), '')} "
                    f"{record.get('opsec', 'unknown')}\n\n"
                    f"**Licence** {record.get('license') or '—'}")

                if record.get("best_for"):
                    st.markdown(f"**Best for** — {record['best_for']}")
                if record.get("opsec_note"):
                    st.warning(record["opsec_note"], icon="⚠️")

                links = [
                    (label, url) for label, url in (
                        ("Repository", record.get("github_repo")),
                        ("Documentation", record.get("documentation_url")),
                        ("API docs", record.get("api_docs_url")),
                    ) if url
                ]
                if links:
                    st.markdown(" · ".join(f"[{label}]({url})" for label, url in links))

                related = record.get("related_tools") or []
                if related:
                    st.caption(f"Related: {', '.join(related)}")

                # The catalog is a composite of overlapping upstreams. Without
                # this a merged row is an unattributable claim.
                with st.expander("Provenance"):
                    st.caption(f"Listed by: {', '.join(record.get('sources') or [])}")
                    provenance = record.get("provenance") or {}
                    if provenance:
                        st.dataframe(
                            pd.DataFrame(
                                sorted(provenance.items()),
                                columns=["Field", "Supplied by"]),
                            hide_index=True, height=240)
    else:
        st.caption("Select a row for the full record, its source attribution "
                   "and any OPSEC caveats.")

st.download_button(
    "Download CSV",
    data=view.drop(columns=_INTERNAL_COLUMNS).to_csv(index=False).encode("utf-8"),
    file_name="osint-toolkit.csv",
    mime="text/csv")

st.caption("Catalog data is imported from public upstream sources and is only "
           "as current as the last import. Shodan, Censys, Maltego and Epieos "
           "are fixed entries written from public documentation rather than "
           "scraped — verify pricing and capability with the vendor before "
           "relying on either.")
