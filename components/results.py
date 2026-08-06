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

Every answer is stamped with the date it was given (via exposure_store,
backed by the same SQLite file as the campaign tracker) so it survives
closing the browser -- previously this only lived in session state and
reset every session, making it impossible to know if an answer was fresh
or six months stale.
"""
from datetime import datetime

import streamlit as st
import requests

import config
import exposure_store
import spokeo_automation
from google_dork import domain_from_url, build_combined_dork_url, build_broker_dork_url

SOCIAL_MEDIA_DOMAINS = ["instagram.com", "facebook.com", "twitter.com", "x.com", "linkedin.com", "tiktok.com"]

TRISTATE_OPTIONS = ["Haven't checked", "Checked — clear", "Found exposure"]


def _broker_risk_badge(found_count, total_count):
    # st.badge strips a leading emoji from the label itself -- it has to go
    # through the dedicated icon= argument instead, so return it separately.
    if total_count == 0:
        return "Not checked", "gray", "⚪"
    ratio = found_count / total_count
    if found_count == 0:
        return "Low", "green", "🟢"
    elif ratio < 0.34:
        return "Medium", "yellow", "🟡"
    elif ratio < 0.67:
        return "High", "orange", "🟠"
    else:
        return "Critical", "red", "🔴"


def _tristate_badge(value):
    if value == "Found exposure":
        return "Critical", "red", "🔴"
    elif value == "Checked — clear":
        return "Low", "green", "🟢"
    else:
        return "Not checked", "gray", "⚪"


def _record_if_changed(category, value, persisted):
    """Persist a real answer if it's new, updating the in-memory snapshot
    too so the staleness caption reflects it on this same run."""
    current = persisted.get(category)
    if not current or current["value"] != value:
        exposure_store.record_check(config.EXPOSURE_DB_PATH, category, value)
        persisted[category] = {"value": value, "checked_at": datetime.now().strftime("%Y-%m-%d")}
    return persisted[category]


def _staleness_note(entry):
    """entry is exposure_store's {"value", "checked_at"} dict, or None."""
    if not entry:
        return None
    days = exposure_store.days_since_checked(entry["checked_at"])
    day_word = "day" if days == 1 else "days"
    if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS):
        return f":material/schedule: Recheck due — last checked {days} {day_word} ago"
    return "Checked today" if days == 0 else f"Last checked {days} {day_word} ago"


def _get_client_context():
    """Best-effort lookup of IP and ISP metadata for the current user."""
    try:
        response = requests.get("https://ipinfo.io/json", timeout=4)
        if response.ok:
            payload = response.json()
            return {
                "ip": payload.get("ip", "Unknown"),
                "city": payload.get("city", "Unknown"),
                "region": payload.get("region", "Unknown"),
                "country": payload.get("country", "Unknown"),
                "org": payload.get("org", "Unknown ISP"),
            }
    except Exception:
        pass
    return {"ip": "Unavailable", "city": "Unknown", "region": "Unknown", "country": "Unknown", "org": "Unknown ISP"}


def _flag_emoji(country_code):
    """ISO 3166-1 alpha-2 code -> flag emoji, via the regional-indicator
    trick (each letter maps to a Unicode regional-indicator symbol)."""
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


def render(brokers_df):
    st.markdown(
        """
        <div class="np-hero">
            <div class="np-card-label">Results</div>
            <h3 style="margin: 0 0 0.4rem 0;">Review your exposure and decide what deserves action first</h3>
            <p class="np-quiet" style="margin: 0;">
                Confirm the listings you find, keep track of stale checks, and move from research into a clear removal plan.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

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

    persisted = exposure_store.get_all_checks(config.EXPOSURE_DB_PATH)
    client_context = _get_client_context()

    with st.container(border=True):
        first_name = name.split()[0] if name else "friend"
        flag = _flag_emoji(client_context["country"])
        st.markdown(f"## Hello, {first_name}")
        st.markdown(
            f"**This is what any site can see about you right now: "
            f"{flag} {client_context['city']}, {client_context['region']}, {client_context['country']}**"
        )
        st.caption(f"IP address: {client_context['ip']}")
        st.caption(f"ISP / provider: {client_context['org']}")
        with st.expander(":material/visibility: How does a site learn this from a single visit?"):
            st.markdown(
                "No login, no cookies, no permission prompt needed — this is available to *any* site "
                "you visit, including every data broker in this app:\n\n"
                "📨 **Your IP address** is sent automatically with every web request, the same way a "
                "return address is on an envelope.\n\n"
                "🌍 **Location and ISP** get derived by looking that IP up in a public registry that maps "
                "IP address ranges to the internet provider they were issued to, and roughly where that "
                "provider operates — same technique this page just used against `ipinfo.io`.\n\n"
                "🎯 It's usually accurate to your **city/region**, not your exact address — precision "
                "depends on your ISP, not the site doing the looking.\n\n"
                "🛡️ A VPN (or your ISP's carrier-grade NAT) is what actually hides this — it swaps your "
                "real IP for the VPN provider's, so sites see the VPN server's location instead of yours."
            )

    st.markdown("---")

    action_col, next_col = st.columns([2, 1])
    with action_col.container(border=True):
        st.subheader(":material/flag: What to do next")
        st.caption("Use the evidence you confirm here to prioritize deletion letters, follow-up searches, or alerts.")
    with next_col.container(border=True):
        st.subheader(":material/bolt: Suggested path")
        st.caption("1. Confirm listings\n2. Check the risk cards\n3. Send a deletion request")

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

    with st.container(border=True):
        st.subheader(":material/notifications_active: Stay alerted automatically")
        st.caption(
            "Two free services that watch for you continuously, instead of waiting for your next manual "
            "check here — worth setting up once."
        )
        alert_cols = st.columns(2)
        alert_cols[0].link_button(
            ":material/mail: Get emailed on future breaches", config.HIBP_NOTIFY_URL, width="stretch",
        )
        alert_cols[0].caption("HaveIBeenPwned's own free notify-me — register your email once.")
        alert_cols[1].link_button(
            ":material/search: Get alerted on new web mentions", config.GOOGLE_ALERTS_URL, width="stretch",
        )
        alert_cols[1].caption(f'Google Alerts — search `"{name}"` and save it as an alert.')

    st.markdown("---")
    with st.container(border=True):
        st.subheader(":material/analytics: Exposure by category")
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
                "Result", TRISTATE_OPTIONS, default=persisted.get("email_breach", {}).get("value"),
                key="check_email_breach", label_visibility="collapsed",
            )
            email_entry = persisted.get("email_breach")
            if email_answer in ("Checked — clear", "Found exposure"):
                email_entry = _record_if_changed("email_breach", email_answer, persisted)
            email_note = _staleness_note(email_entry)
            if email_note:
                st.caption(email_note)
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
                "Result", TRISTATE_OPTIONS, default=persisted.get("phone_exposure", {}).get("value"),
                key="check_phone_exposure", label_visibility="collapsed",
            )
            phone_entry = persisted.get("phone_exposure")
            if phone_answer in ("Checked — clear", "Found exposure"):
                phone_entry = _record_if_changed("phone_exposure", phone_answer, persisted)
            phone_note = _staleness_note(phone_entry)
            if phone_note:
                st.caption(phone_note)
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
            "Result", TRISTATE_OPTIONS, default=persisted.get("social_media_public", {}).get("value"),
            key="check_social_media", label_visibility="collapsed",
        )
        social_entry = persisted.get("social_media_public")
        if social_answer in ("Checked — clear", "Found exposure"):
            social_entry = _record_if_changed("social_media_public", social_answer, persisted)
        social_note = _staleness_note(social_entry)
        if social_note:
            st.caption(social_note)

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

    # Fill in the badges now that we know the answers
    email_label, email_color, email_icon = _tristate_badge(email_answer)
    if email:
        email_state.badge(email_label, icon=email_icon, color=email_color)
    phone_label, phone_color, phone_icon = _tristate_badge(phone_answer)
    if phone and broker_domains:
        phone_state.badge(phone_label, icon=phone_icon, color=phone_color)
    social_label, social_color, social_icon = _tristate_badge(social_answer)
    social_state.badge(social_label, icon=social_icon, color=social_color)

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

        broker_category = f"broker:{broker_name}"
        stored_broker_entry = persisted.get(broker_category)
        checked = row_cols[2].checkbox(
            "Found myself listed here",
            value=stored_broker_entry["value"] == "true" if stored_broker_entry else False,
            key=f"selfsearch_check_{broker_name}",
        )
        st.session_state.listed_confirmed[broker_name] = checked

        # Only record a check the first time there's an actual interaction --
        # an untouched, still-default-unchecked box isn't a "checked, clear"
        # answer, it's just nobody having looked yet.
        broker_entry = stored_broker_entry
        if stored_broker_entry or checked:
            broker_entry = _record_if_changed(broker_category, "true" if checked else "false", persisted)
        broker_note = _staleness_note(broker_entry)
        if broker_note:
            row_cols[2].caption(broker_note)

    found_count = sum(1 for v in st.session_state.listed_confirmed.values() if v)
    total_count = len(brokers_df)
    check_count = sum(1 for v in [email_answer, phone_answer, social_answer] if v is not None)
    metric_placeholder.metric("Brokers confirmed listed", f"{found_count} / {total_count} checked")
    broker_label, broker_color, broker_icon = _broker_risk_badge(found_count, total_count)
    broker_badge_placeholder.badge(broker_label, icon=broker_icon, color=broker_color)

    st.markdown("---")
    summary_cols = st.columns(3)
    with summary_cols[0]:
        st.metric("Brokers confirmed", f"{found_count} / {total_count}", help="How many broker listings you have confirmed so far.")
    with summary_cols[1]:
        st.metric("Checks reviewed", f"{check_count} / 3", help="How many of the core exposure checks you have filled in.")
    with summary_cols[2]:
        stale_count = sum(
            1 for entry in persisted.values()
            if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS)
        )
        st.metric("Needs recheck", stale_count, help="Items that have gone stale and may need a fresh look.")

    # Combined real exposure readout: broker ratio + the three tri-state
    # answers, equally weighted and disclosed as such — no invented
    # per-category weights like the old simulated version had.
    checklist_found = sum(1 for v in [email_answer, phone_answer, social_answer] if v == "Found exposure")
    checklist_checked = sum(1 for v in [email_answer, phone_answer, social_answer] if v is not None)
    combined_found = found_count + checklist_found
    combined_total = total_count + checklist_checked
    ratio = combined_found / combined_total if combined_total else 0
    stale_count = sum(
        1 for entry in persisted.values()
        if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS)
    )

    with exposure_placeholder.container():
        if combined_total == total_count and found_count == 0:
            st.markdown("## :material/help: Not checked yet")
            st.info("Nothing confirmed yet — check items off below as you verify them.")
        elif combined_found == 0:
            st.markdown("## 🎉 All clear so far")
            st.success("Nothing found exposed in what's been checked so far.")
        elif ratio < 0.34:
            st.markdown(f"## 🙂 Low exposure — {combined_found} of {combined_total}")
            st.success(f"Confirmed exposed in {combined_found} of {combined_total} checks so far.")
        elif ratio < 0.67:
            st.markdown(f"## ⚠️ Moderate exposure — {combined_found} of {combined_total}")
            st.warning(f"Confirmed exposed in {combined_found} of {combined_total} checks so far.")
        else:
            st.markdown(f"## 🚨 High exposure — {combined_found} of {combined_total}")
            st.error(f"Confirmed exposed in {combined_found} of {combined_total} checks so far — worth prioritizing deletion letters.")

        if stale_count:
            item_word = "item" if stale_count == 1 else "items"
            st.caption(
                f":material/schedule: {stale_count} {item_word} haven't been rechecked in "
                f"{config.RECHECK_STALE_DAYS}+ days — worth a fresh look below."
            )

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
