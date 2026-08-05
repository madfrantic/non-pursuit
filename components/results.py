"""
Results: a dedicated page for what searching actually found. Split out
from the Dashboard on purpose — stacking the identity form, the full
broker-by-broker results, and campaign metrics all on one long page made
it easy to miss that results were there at all.

Reads the identity info entered on the Dashboard; doesn't collect it
again. The risk cards below bring back the visual style of the old
Privacy Exposure Dashboard (colored Low/Medium/High/Critical badges per
category) but driven by real signals this time: your own confirmed
broker listings and self-reported tri-state answers (haven't checked /
checked clear / found exposure), not a hash of your input. Where a
category has no free, safe way to check automatically (SSN), the badge
says so plainly instead of inventing a level.
"""
import streamlit as st

import spokeo_automation
from google_dork import domain_from_url, build_combined_dork_url, build_broker_dork_url

SOCIAL_MEDIA_DOMAINS = ["instagram.com", "facebook.com", "twitter.com", "x.com", "linkedin.com", "tiktok.com"]

TRISTATE_OPTIONS = ["Haven't checked", "Checked — clear", "Found exposure"]


def _broker_risk_badge(found_count, total_count):
    if total_count == 0:
        return "Not checked", "gray"
    ratio = found_count / total_count
    if found_count == 0:
        return "Low", "green"
    elif ratio < 0.34:
        return "Medium", "yellow"
    elif ratio < 0.67:
        return "High", "orange"
    else:
        return "Critical", "red"


def _tristate_badge(value):
    if value == "Found exposure":
        return "Critical", "red"
    elif value == "Checked — clear":
        return "Low", "green"
    else:
        return "Not checked", "gray"


def render(brokers_df):
    st.header(":material/travel_explore: Your Results")

    name = st.session_state.user_name
    location = st.session_state.user_location
    email = st.session_state.user_email
    phone = st.session_state.user_phone

    if not name:
        st.warning("Enter your name on the **Dashboard** first, then come back here to see your results.")
        return

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
        return

    st.markdown(f"Showing results for **{name}**" + (f", {location}" if location else "") + ".")
    st.markdown("---")

    exposure_placeholder = st.empty()
    st.markdown("---")

    broker_domains = [domain_from_url(u) for u in brokers_df["search_url"] if u]
    if broker_domains:
        with st.container(border=True):
            st.subheader(":material/travel_explore: One-click search")
            st.link_button(
                ":material/travel_explore: Search for yourself across every broker at once",
                build_combined_dork_url(name, location, broker_domains),
                type="primary",
                width="stretch",
            )
            st.caption(
                "A real, targeted Google search restricted to the broker sites below — opens in a new tab "
                "(Google blocks showing its results inside another site, so this can't be embedded here). "
                "Good for a quick overview; use each broker's own button below for a cleaner, one-at-a-time result."
            )

    st.markdown("---")
    st.subheader("Exposure by category")
    st.caption(
        "Each card is a real signal, not a computed score: broker listings come from your confirmations "
        "below, the rest from what you report after checking the real link — same honest data, brought "
        "back with the risk-card look from the old dashboard."
    )

    # --- Data broker listings card ---
    with st.container(border=True):
        card_cols = st.columns([4, 2])
        card_cols[0].markdown("### 🏢 Data broker listings")
        broker_badge_placeholder = card_cols[1].empty()
        metric_placeholder = st.empty()
        st.caption("Confirm each broker below after searching — this is the same list as the section further down.")

    # --- Email breaches card ---
    with st.container(border=True):
        card_cols = st.columns([4, 2])
        card_cols[0].markdown("### 📧 Email breaches")
        if email:
            email_state = card_cols[1].empty()
            st.link_button("Check on HaveIBeenPwned", f"https://haveibeenpwned.com/account/{email}", key="hibp_link")
            email_answer = st.segmented_control(
                "Result", TRISTATE_OPTIONS, default=st.session_state.exposure_checklist.get("email_breach"),
                key="check_email_breach", label_visibility="collapsed",
            )
        else:
            card_cols[1].empty()
            st.caption("Add an email on the Dashboard to check this")
            email_answer = None

    # --- Phone number card ---
    with st.container(border=True):
        card_cols = st.columns([4, 2])
        card_cols[0].markdown("### 📱 Phone number")
        if phone and broker_domains:
            phone_state = card_cols[1].empty()
            phone_dork_url = build_combined_dork_url(phone, "", broker_domains)
            st.link_button("Search for this number", phone_dork_url, key="phone_dork_link")
            phone_answer = st.segmented_control(
                "Result", TRISTATE_OPTIONS, default=st.session_state.exposure_checklist.get("phone_exposure"),
                key="check_phone_exposure", label_visibility="collapsed",
            )
        else:
            card_cols[1].empty()
            st.caption("Add a phone number on the Dashboard to check this")
            phone_answer = None

    # --- Social media card ---
    with st.container(border=True):
        card_cols = st.columns([4, 2])
        card_cols[0].markdown("### 👤 Social media")
        social_state = card_cols[1].empty()
        social_dork_url = build_combined_dork_url(name, location, SOCIAL_MEDIA_DOMAINS)
        st.link_button("Search my name on social platforms", social_dork_url, key="social_dork_link")
        social_answer = st.segmented_control(
            "Result", TRISTATE_OPTIONS, default=st.session_state.exposure_checklist.get("social_media_public"),
            key="check_social_media", label_visibility="collapsed",
        )

    # --- SSN card: informational only, no risk level ---
    with st.container(border=True):
        card_cols = st.columns([4, 2])
        card_cols[0].markdown("### 🔑 SSN / identity theft")
        card_cols[1].badge("Not checkable", icon=":material/block:", color="gray")
        st.caption(
            "There's no free, safe way to automatically check this — anything that claims to is either "
            "paid or guessing. If you're concerned: check your credit report for free at "
            "[annualcreditreport.com](https://www.annualcreditreport.com), or get real guidance at "
            "[IdentityTheft.gov](https://www.identitytheft.gov)."
        )

    # Persist the tri-state answers
    st.session_state.exposure_checklist = {
        "email_breach": email_answer,
        "phone_exposure": phone_answer,
        "social_media_public": social_answer,
    }

    # Fill in the badges now that we know the answers
    email_label, email_color = _tristate_badge(email_answer)
    if email:
        email_state.badge(email_label, color=email_color)
    phone_label, phone_color = _tristate_badge(phone_answer)
    if phone and broker_domains:
        phone_state.badge(phone_label, color=phone_color)
    social_label, social_color = _tristate_badge(social_answer)
    social_state.badge(social_label, color=social_color)

    st.markdown("---")
    st.subheader(":material/apartment: Broker-by-broker detail")

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
    broker_label, broker_color = _broker_risk_badge(found_count, total_count)
    broker_badge_placeholder.badge(broker_label, color=broker_color)

    # Combined real exposure readout: broker ratio + the three tri-state
    # answers, equally weighted and disclosed as such — no invented
    # per-category weights like the old simulated version had.
    checklist_found = sum(1 for v in [email_answer, phone_answer, social_answer] if v == "Found exposure")
    checklist_checked = sum(1 for v in [email_answer, phone_answer, social_answer] if v is not None)
    combined_found = found_count + checklist_found
    combined_total = total_count + checklist_checked
    ratio = combined_found / combined_total if combined_total else 0

    if combined_total == total_count and found_count == 0:
        exposure_placeholder.info(":material/check_circle: Nothing confirmed yet — check items off below as you verify them.")
    elif combined_found == 0:
        exposure_placeholder.success(":material/check_circle: Nothing found exposed in what's been checked so far.")
    elif ratio < 0.34:
        exposure_placeholder.success(f":material/check_circle: Confirmed exposed in {combined_found} of {combined_total} checks so far.")
    elif ratio < 0.67:
        exposure_placeholder.warning(f":material/warning: Confirmed exposed in {combined_found} of {combined_total} checks so far.")
    else:
        exposure_placeholder.error(f":material/error: Confirmed exposed in {combined_found} of {combined_total} checks so far — worth prioritizing deletion letters.")

    st.markdown("---")
    st.subheader("Other real ways to check yourself")
    link_cols = st.columns(4)
    search_engines = {
        "Google": f"https://www.google.com/search?q={name.replace(' ', '+')}",
        "Bing": f"https://www.bing.com/search?q={name.replace(' ', '+')}",
        "DuckDuckGo": f"https://duckduckgo.com/?q={name.replace(' ', '+')}",
        "Yahoo": f"https://search.yahoo.com/search?p={name.replace(' ', '+')}",
    }
    for idx, (engine_name, url) in enumerate(search_engines.items()):
        link_cols[idx].link_button(f"🔍 {engine_name}", url)

    st.markdown("---")
    if found_count > 0:
        st.success(
            f"You're listed on {found_count} broker(s). Head to **Data Broker Deletion Letters** "
            "in the sidebar — it already knows which brokers you confirmed here."
        )
    else:
        st.info("Check off any broker above where you found your own information.")
