"""
Search-results section: identity inputs + broker search results. Rendered
as part of the merged Dashboard page rather than its own separate mode —
folded in because a standalone "Should I Worry?" tab was redundant with
Dashboard once Dashboard also became the place you enter your info.

Also referenced conceptually by Letters' "already confirmed" check via the
shared st.session_state.listed_confirmed dict populated here.
"""
import streamlit as st

import spokeo_automation
from google_dork import domain_from_url, build_combined_dork_url, build_broker_dork_url


def render(brokers_df):
    st.subheader("Your info")
    st.caption("Enter this once — it's reused everywhere else in the app (letters, tracker).")
    ss_col1, ss_col2, ss_col3 = st.columns(3)
    with ss_col1:
        self_search_name = st.text_input(
            "Full Name", value=st.session_state.user_name, placeholder="Enter your full legal name"
        )
    with ss_col2:
        self_search_location = st.text_input(
            "Location", value=st.session_state.user_location, placeholder="City, State"
        )
    with ss_col3:
        self_search_email = st.text_input(
            "Email", value=st.session_state.user_email, placeholder="your.email@example.com"
        )
    st.session_state.user_name = self_search_name
    st.session_state.user_location = self_search_location
    st.session_state.user_email = self_search_email

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
        return

    if not self_search_name:
        st.info("Enter your name above to search for yourself across data broker sites.")
        return

    st.markdown("---")
    st.subheader(":material/travel_explore: Search results")
    st.caption(
        "Search each data broker's site for your own name **before** generating a deletion "
        "letter. There's no point demanding a broker delete a record you haven't confirmed exists."
    )

    broker_domains = [domain_from_url(u) for u in brokers_df["search_url"] if u]
    if broker_domains:
        st.link_button(
            ":material/travel_explore: Search for yourself across every broker at once",
            build_combined_dork_url(self_search_name, self_search_location, broker_domains),
        )
        st.caption(
            "A real, targeted Google search restricted to the broker sites below — not simulated. "
            "Good for a quick overview; use each broker's own button below for a cleaner, one-at-a-time result."
        )

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
                    outcome = spokeo_automation.run_search_and_wait(
                        self_search_name, self_search_location
                    )
                if outcome == "timed_out":
                    wait_minutes = spokeo_automation.MAX_WAIT_SECONDS // 60
                    st.warning(f"Closed the {broker_name} window automatically after {wait_minutes} minutes of inactivity.")
        elif broker["search_url"]:
            broker_domain = domain_from_url(broker["search_url"])
            dork_url = build_broker_dork_url(self_search_name, self_search_location, broker_domain)
            row_cols[1].link_button(":material/travel_explore: Targeted search", dork_url, key=f"selfsearch_link_{broker_name}")
        else:
            row_cols[1].caption("No search link on file")

        checked = row_cols[2].checkbox(
            "Found myself listed here",
            key=f"selfsearch_check_{broker_name}",
        )
        st.session_state.listed_confirmed[broker_name] = checked

    found_count = sum(1 for v in st.session_state.listed_confirmed.values() if v)
    metric_placeholder.metric("Brokers confirmed listed", f"{found_count} / {len(brokers_df)} checked")

    st.markdown("---")
    st.subheader("Other ways to check yourself")
    st.caption("Broader web presence and email breach checks — both real, not simulated.")

    link_cols = st.columns(4)
    search_engines = {
        "Google": f"https://www.google.com/search?q={self_search_name.replace(' ', '+')}",
        "Bing": f"https://www.bing.com/search?q={self_search_name.replace(' ', '+')}",
        "DuckDuckGo": f"https://duckduckgo.com/?q={self_search_name.replace(' ', '+')}",
        "Yahoo": f"https://search.yahoo.com/search?p={self_search_name.replace(' ', '+')}",
    }
    for idx, (engine_name, url) in enumerate(search_engines.items()):
        link_cols[idx].link_button(f"🔍 {engine_name}", url)

    if self_search_email:
        st.link_button(
            ":material/lock_open: Check on HaveIBeenPwned",
            f"https://haveibeenpwned.com/account/{self_search_email}",
        )

    st.markdown("---")
    if found_count > 0:
        st.success(
            f"You're listed on {found_count} broker(s). Head to **Data Broker Deletion Letters** "
            "in the sidebar — it already knows which brokers you confirmed here."
        )
    else:
        st.info("Check off any broker above where you found your own information.")
