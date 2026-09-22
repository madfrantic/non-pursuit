"""
Digital Footprint Matrix: an instant, offline platform grid rendered
alongside the active campaign ledger.

The Online Footprint page (components/footprint.py) runs the real thing
-- a live ~700-site sweep off the WhatsMyName dataset, which takes time
and puts the handle on the wire. This page is the opposite trade: a
15-platform grid that resolves from a local JSON file with no network at
all, so it renders instantly and is safe to run from a shared demo host
against an audience member's address.

That speed is bought with simulated data, and the cost of a viewer not
realising that is high in an app whose output is a binding demand
letter. So the simulation is stated three times over -- a banner above
the grid, a caption under it, and POSSIBLE (never CONFIRMED) confidence
on anything pushed into the triage worklist. Do not quietly drop those.

Pairing the grid with the campaign ledger is the point of the layout.
The grid is where exposure is discovered; the ledger is what is already
being enforced. Side by side they answer "what did we find, and what are
we already chasing?" on one screen.
"""
import pandas as pd
import streamlit as st

import campaign_manager
import footprint_matrix as matrix
import runtime_mode

VERDICT_BADGE = {
    matrix.FOUND: "🟢 Found",
    matrix.NOT_FOUND: "⚪ Not Found",
    matrix.UNSUPPORTED: "⚫ N/A",
}

_STATE_RESULTS = "footprint_matrix_results"
_STATE_IDENTIFIER = "footprint_matrix_identifier"

_SIMULATION_NOTICE = (
    "**Simulated dataset — not a live scan.** This grid resolves against a local "
    "offline file (`data/footprint_map.json`). Results are deterministic "
    "placeholders for demonstration and are **not** evidence of real accounts. "
    "Use **Online Footprint** for a live sweep."
)


@st.cache_data(show_spinner=False)
def _load_map():
    """Cached so the registry parse doesn't repeat on every rerun. Safe to
    cache with no TTL: the file is a static asset that ships with the
    repo, not something a scan mutates."""
    return matrix.load_map()


def _badge(verdict: str) -> str:
    return VERDICT_BADGE.get(verdict, verdict)


def _render_grid(results: list) -> None:
    """The matrix itself, four platforms to a row.

    A dataframe would be denser, but the badge grid is the thing being
    demonstrated -- and a hit needs to carry a clickable profile link,
    which a dataframe cell can't.
    """
    columns_per_row = 4
    for start in range(0, len(results), columns_per_row):
        chunk = results[start:start + columns_per_row]
        columns = st.columns(columns_per_row)
        for column, result in zip(columns, chunk):
            with column:
                st.markdown(f"**{result['platform']}**")
                st.markdown(_badge(result["verdict"]))
                if result["verdict"] == matrix.FOUND and result["profile_url"]:
                    st.caption(f"[{result['domain']}]({result['profile_url']})")
                else:
                    st.caption(result["domain"] or "—")
        # Pad the final row so a partial chunk doesn't leave the layout
        # ragged against the ledger column beside it.
        st.write("")


def _render_recovery_table(results: list) -> None:
    """holehe-schema detail for the hits: masked recovery address, masked
    phone, and which upstream holehe module the platform corresponds to.

    Only rendered when at least one hit actually carries recovery data --
    an all-empty table implies the lookup failed rather than that these
    platforms simply expose nothing.
    """
    found = matrix.hits(results)
    detailed = [r for r in found if r["emailrecovery"] or r["phoneNumber"]]
    if not detailed:
        return

    st.markdown("**Recovery hints** (holehe-schema fields)")
    st.dataframe(
        pd.DataFrame([
            {
                "Platform": r["platform"],
                "holehe module": r["holehe_module"] or "—",
                "Recovery email": r["emailrecovery"] or "—",
                "Recovery phone": r["phoneNumber"] or "—",
                "Rate limited": "yes" if r["rateLimit"] else "no",
            }
            for r in detailed
        ]),
        hide_index=True,
        width="stretch",
    )
    st.caption("Masked placeholder values from the offline dataset — not retrieved from any platform.")


def _render_ledger() -> None:
    """The active campaign ledger, beside the grid."""
    st.subheader("Active campaign ledger")
    try:
        campaigns = campaign_manager.get_active_campaigns(runtime_mode.db_path())
    except Exception as exc:
        st.caption(f"Ledger unavailable: {exc}")
        return

    if not campaigns:
        st.caption("No active campaigns. Discovered exposure gets escalated here.")
        return

    st.dataframe(
        pd.DataFrame([
            {
                "Broker": record.get("broker_name", ""),
                "Status": record.get("effective_status", ""),
                "Days left": record.get("days_remaining"),
            }
            for record in campaigns
        ]),
        hide_index=True,
        width="stretch",
    )
    overdue = sum(1 for record in campaigns if record.get("is_overdue"))
    if overdue:
        st.markdown(f":red[**{overdue} campaign(s) past the statutory deadline.**]")


def render() -> None:
    st.header("Digital Footprint Matrix")
    st.warning(_SIMULATION_NOTICE)

    if not matrix.available_probers():
        st.caption("Offline mode — no live prober registered. Zero outbound requests.")

    identifier = st.text_input(
        "Email or handle",
        key=_STATE_IDENTIFIER,
        placeholder="you@example.com or @handle",
    )

    # Gated on an explicit press for consistency with the live scan page,
    # even though this one is free -- the two pages should not teach
    # different interaction models for the same action.
    if st.button("Run matrix lookup", type="primary"):
        if not identifier.strip():
            st.error("Enter an email address or a handle first.")
        else:
            results = matrix.scan(identifier, footprint_map=_load_map())
            st.session_state[_STATE_RESULTS] = results
            if runtime_mode.persistent_storage_enabled():
                stored = matrix.save_results(runtime_mode.db_path(), results)
                st.success(f"Matrix complete — {stored} hit(s) saved to the local ledger.")
            else:
                st.info("Matrix complete — session-only mode, nothing written to disk.")

    results = st.session_state.get(_STATE_RESULTS)
    if not results:
        return

    summary = matrix.summarize(results)
    grid_column, ledger_column = st.columns([2, 1])

    with grid_column:
        found = summary[matrix.FOUND]
        st.markdown(
            f"**{found} found** · {summary[matrix.NOT_FOUND]} not found · "
            f"{summary[matrix.UNSUPPORTED]} not applicable"
        )
        _render_grid(results)
        _render_recovery_table(results)
        st.caption(
            f"Identifier read as: `{matrix.classify_identifier(results[0]['identifier'])}` · "
            "simulated results, verify independently before acting."
        )

    with ledger_column:
        _render_ledger()
