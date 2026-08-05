"""
Results: a dedicated page for what searching actually found. Split out
from the Dashboard on purpose — stacking the identity form, the full
broker-by-broker results, and campaign metrics all on one long page made
it easy to miss that results were there at all.

Reads the identity info entered on the Dashboard; doesn't collect it
again. The exposure-level readout below is real, derived from your own
confirmed checkboxes — not the old simulated per-category risk cards
(SSN/email/phone/etc.) that got removed for being fabricated data.
"""
import streamlit as st

import spokeo_automation
from google_dork import domain_from_url, build_combined_dork_url, build_broker_dork_url


def render(brokers_df):
    st.header(":material/travel_explore: Your Results")

    name = st.session_state.user_name
    location = st.session_state.user_location
    email = st.session_state.user_email

    if not name:
        st.warning("Enter your name on the **Dashboard** first, then come back here to see your results.")
        return

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
        return

    st.markdown(f"Showing results for **{name}**" + (f", {location}" if location else "") + ".")
    st.markdown("---")

    broker_domains = [domain_from_url(u) for u in brokers_df["search_url"] if u]
    if broker_domains:
        st.link_button(
            ":material/travel_explore: Search for yourself across every broker at once",
            build_combined_dork_url(name, location, broker_domains),
        )
        st.caption(
            "A real, targeted Google search restricted to the broker sites below — not simulated. "
            "Good for a quick overview; use each broker's own button below for a cleaner, one-at-a-time result."
        )

    exposure_placeholder = st.empty()
    metric_placeholder = st.empty()
    st.markdown("---")

    for _, broker in brokers_df.iterrows():
        broker_name = broker["broker_name"]
        is_automated = bool(broker["automated_search"])

        row_cols = st.columns([3, 2, 3])
        row_cols[0].markdown(f"**{broker_name}**")
        if broker["notes"]:
            row_cols[0].caption(broker["notes"])

        if is_automated:
            if row_cols[1].button("🤖 Auto-search", key=f"autosearch_{broker_name}"):
                with st.spinner(
                    f"Chrome is open and searching {broker_name} — review the results there, "
                    "then close that window to continue."
                ):
                    outcome = spokeo_automation.run_search_and_wait(name, location)
                if outcome == "timed_out":
                    wait_minutes = spokeo_automation.MAX_WAIT_SECONDS // 60
                    st.warning(f"Closed the {broker_name} window automatically after {wait_minutes} minutes of inactivity.")
        elif broker["search_url"]:
            broker_domain = domain_from_url(broker["search_url"])
            dork_url = build_broker_dork_url(name, location, broker_domain)
            row_cols[1].link_button(":material/travel_explore: Targeted search", dork_url, key=f"selfsearch_link_{broker_name}")
        else:
            row_cols[1].caption("No search link on file")

        checked = row_cols[2].checkbox(
            "Found myself listed here",
            key=f"selfsearch_check_{broker_name}",
        )
        st.session_state.listed_confirmed[broker_name] = checked

    found_count = sum(1 for v in st.session_state.listed_confirmed.values() if v)
    total_count = len(brokers_df)
    metric_placeholder.metric("Brokers confirmed listed", f"{found_count} / {total_count} checked")

    # A real exposure readout based on your own confirmations above — not a
    # simulated score. Thresholds are simple and disclosed as such.
    if found_count == 0:
        exposure_placeholder.info(":material/check_circle: No confirmed listings yet — check off brokers below as you verify them.")
    elif found_count / total_count < 0.34:
        exposure_placeholder.success(f":material/check_circle: Confirmed on {found_count} of {total_count} brokers checked so far.")
    elif found_count / total_count < 0.67:
        exposure_placeholder.warning(f":material/warning: Confirmed on {found_count} of {total_count} brokers checked so far.")
    else:
        exposure_placeholder.error(f":material/error: Confirmed on {found_count} of {total_count} brokers checked so far — worth prioritizing deletion letters.")

    st.markdown("---")
    st.subheader("Other ways to check yourself")
    st.caption("Broader web presence and email breach checks — both real, not simulated.")

    link_cols = st.columns(4)
    search_engines = {
        "Google": f"https://www.google.com/search?q={name.replace(' ', '+')}",
        "Bing": f"https://www.bing.com/search?q={name.replace(' ', '+')}",
        "DuckDuckGo": f"https://duckduckgo.com/?q={name.replace(' ', '+')}",
        "Yahoo": f"https://search.yahoo.com/search?p={name.replace(' ', '+')}",
    }
    for idx, (engine_name, url) in enumerate(search_engines.items()):
        link_cols[idx].link_button(f"🔍 {engine_name}", url)

    if email:
        st.link_button(
            ":material/lock_open: Check on HaveIBeenPwned",
            f"https://haveibeenpwned.com/account/{email}",
        )

    st.markdown("---")
    if found_count > 0:
        st.success(
            f"You're listed on {found_count} broker(s). Head to **Data Broker Deletion Letters** "
            "in the sidebar — it already knows which brokers you confirmed here."
        )
    else:
        st.info("Check off any broker above where you found your own information.")
