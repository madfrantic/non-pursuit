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
from datetime import datetime, timezone

import streamlit as st
import profile_state
import client_context

import config
import runtime_mode
import database
import exposure_store
import spokeo_automation
from applog import get_logger
from broker_freshness import is_broker_stale, days_since_verified
from google_dork import domain_from_url, build_combined_dork_url, build_broker_dork_url

_log = get_logger("results")

SOCIAL_MEDIA_DOMAINS = ["instagram.com", "facebook.com", "twitter.com", "x.com", "linkedin.com", "tiktok.com"]

TRISTATE_OPTIONS = ["Haven't checked", "Checked — clear", "Found exposure"]


def _switch_to(mode_value):
    # Same pending_nav indirection dashboard.py uses -- the sidebar radio
    # (key="nav_mode") has already rendered by the time a button here runs.
    st.session_state.pending_nav = mode_value
    st.rerun()


def _broker_risk_badge(found_count, checked_count, total_count):
    # st.badge strips a leading emoji from the label itself -- it has to go
    # through the dedicated icon= argument instead, so return it separately.
    # checked_count (not total_count) gates "Not checked" -- 0 found out of
    # 7 means something very different if nobody's looked yet vs. everyone
    # confirmed clear, the same distinction tri-state gives email/phone/social.
    if checked_count == 0:
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
        exposure_store.record_check(runtime_mode.db_path(), category, value)
        persisted[category] = {"value": value, "checked_at": datetime.now().strftime("%Y-%m-%d")}
    return persisted[category]


def _staleness_note(entry):
    """entry is exposure_store's {"value", "checked_at"} dict, or None."""
    if not entry:
        return None
    days = exposure_store.days_since_checked(entry["checked_at"])
    day_word = "day" if days == 1 else "days"
    if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS):
        return f"⏰ Recheck due — last checked {days} {day_word} ago"
    return "Checked today" if days == 0 else f"Last checked {days} {day_word} ago"


_CLIENT_CONTEXT_FALLBACKS = {
    "private": "Private network",
    "rate_limited": "Rate limited",
    "unreachable": "Unavailable",
    "malformed": "Unavailable",
}


def _get_client_context():
    """Best-effort lookup of IP and ISP metadata for the *visitor*.

    This called `ipinfo.io/json` with no address, which geolocates
    whoever makes the request -- meaning this server. On the desktop
    build the server is the visitor's own machine, so the answer was
    right by accident; on the hosted build it described the datacenter
    and told a visitor in Queens they were in Virginia. It now
    geolocates the forwarded client address, and only falls back to a
    self-lookup when no proxy header is present.
    """
    try:
        headers = st.context.headers or {}
    except Exception:
        headers = {}
    ip = client_context.client_ip(headers)
    info = client_context.lookup(ip)
    fallback = _CLIENT_CONTEXT_FALLBACKS.get(info["status"], "Unknown")
    return {
        "ip": info["ip"] if info["status"] != "unreachable" else (ip or "Unavailable"),
        "city": info.get("city") or fallback,
        "region": info.get("region") or "",
        "country": info.get("country") or "",
        "org": info.get("org") or (fallback if fallback != "Unknown" else "Unknown ISP"),
    }


def _escape_graphviz_label(value: str) -> str:
    return (
        str(value).replace("\\", "\\\\").replace('"', '\\"')
        .replace("\r", "").replace("\n", "\\n")
    )


def _flag_emoji(country_code):
    """ISO 3166-1 alpha-2 code -> flag emoji, via the regional-indicator
    trick (each letter maps to a Unicode regional-indicator symbol)."""
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


def _parse_device_info(user_agent):
    """Best-effort "Browser on OS" label from a real User-Agent string --
    not a full UA-parsing library, just the common cases, with an honest
    fallback rather than a guess when something doesn't match."""
    if not user_agent:
        return "Unknown device"

    if "Windows" in user_agent:
        os_name = "Windows"
    elif "Mac OS X" in user_agent:
        os_name = "macOS"
    elif "Android" in user_agent:
        os_name = "Android"
    elif "iPhone" in user_agent or "iPad" in user_agent:
        os_name = "iOS"
    elif "Linux" in user_agent:
        os_name = "Linux"
    else:
        os_name = "an unknown OS"

    if "Edg/" in user_agent:
        browser = "Edge"
    elif "OPR/" in user_agent or "Opera" in user_agent:
        browser = "Opera"
    elif "Firefox/" in user_agent:
        browser = "Firefox"
    elif "Chrome/" in user_agent and "Chromium" not in user_agent:
        browser = "Chrome"
    elif "Safari/" in user_agent and "Chrome" not in user_agent:
        browser = "Safari"
    else:
        browser = "an unrecognized browser"

    return f"{browser} on {os_name}"


def render_entity_map():
    """The household/known-associate graph, drawn from the saved profile.

    Its own function so the master dashboard can place it in a tab
    without dragging the whole results page along. It also has to be
    isolated: built inline, its loop variable shadowed the user's own
    name, and every dork link rendered after it searched for the
    associate instead of the user.
    """
    profile = database.get_latest_target_profile(runtime_mode.db_path()) or {}
    entities = profile.get("relational_entities") or []

    st.subheader("🕸️ Relational Entity Exposure Map")
    if not entities:
        st.info("⚪ No Linked Entities Detected")
        return

    st.success("⚖️ Statutory Relational Severance Clause active")
    st.caption(
        "A local relationship map for reviewing household and known-associate linkages. "
        "It does not prove that a broker maintains each link."
    )
    lines = [
        "digraph {", "rankdir=LR;",
        'node [shape=box, style="rounded,filled", fillcolor="#16213a", fontcolor="white"];',
        'user [label="👤 Primary User"];',
    ]
    for index, entity in enumerate(entities):
        entity_id = f"entity{index}"
        entity_name = _escape_graphviz_label(entity.get("name") or "Associated entity")
        lines.append(f'{entity_id} [label="👥 {entity_name}"];')
        addresses = entity.get("shared_historical_addresses") or []
        loyalty = entity.get("shared_store_loyalty_vectors") or []
        vector_id = f"vector{index}"
        vector_label = "Address / Retail Co-Op" if addresses or loyalty else "Household Linkage"
        lines.append(f'{vector_id} [label="🔗 Shared Vector: {vector_label}"];')
        lines.append(f"user -> {vector_id};")
        lines.append(f"{vector_id} -> {entity_id};")
        for broker in ("WhitePages", "Spokeo", "BeenVerified", "Radaris"):
            broker_id = f"{entity_id}_{broker.lower()}"
            lines.append(
                f'{broker_id} [label="🏢 Data Broker Node: {broker}\\nKnown Associates", fillcolor="#3b2a2a"];'
            )
            lines.append(f"{entity_id} -> {broker_id} [color=red];")
    lines.append("}")
    st.graphviz_chart("\n".join(lines), width="stretch")


def render(brokers_df, show_title=True):
    if show_title:
        st.title("Your results")
    st.caption("Confirm the listings you find, keep track of stale checks, and move from research into a clear removal plan.")

    profile = profile_state.get_profile(st.session_state)
    name = profile["full_name"]
    location = ", ".join(value for value in (profile["city"], profile["state"]) if value)
    email = profile["email"]
    phone = profile["phone"]

    if not name:
        with st.container(border=True):
            st.markdown("#### 📝 Enter your profile details to start")
            st.caption("Results are built from the identity details you enter once on the profile page.")
            if st.button("👤 Go to Identity Profile", type="primary"):
                _switch_to("👤 Identity Profile")
        return

    if brokers_df.empty:
        st.error("Unable to load broker data. Check data/brokers.csv.")
        return

    persisted = exposure_store.get_all_checks(runtime_mode.db_path())
    allow_network_lookup = st.checkbox(
        "Reveal approximate network location",
        value=False,
        help="This sends your IP address to ipinfo.io for approximate location and ISP data.",
    )
    client_context = _get_client_context() if allow_network_lookup else {
        "ip": "Not requested", "city": "Not requested", "region": "",
        "country": "", "org": "Not requested",
    }
    user_agent = st.context.headers.get("User-Agent", "")
    device_label = _parse_device_info(user_agent)
    checked_at = datetime.now(timezone.utc).strftime("%B %d, %Y, %I:%M%p UTC")

    with st.container(border=True):
        first_name = name.split()[0] if name else "friend"
        flag = _flag_emoji(client_context["country"])
        st.markdown(f"## Hello, {first_name}")
        st.caption("Same kind of thing as the \"new sign-in\" email your other accounts send you — except this is what THIS page just learned about you, unprompted, with no login at all.")
        st.markdown(f"**Device type:** {device_label}")
        st.markdown(f"**Location:** {flag} {client_context['city']}, {client_context['region']}, {client_context['country']}")
        st.markdown(f"**Time:** {checked_at}")
        st.caption(f"IP address: {client_context['ip']}")
        st.caption(f"ISP / provider: {client_context['org']}")
        with st.expander("👁️ How does a site learn this from a single visit?"):
            st.markdown(
                "No login, no cookies, no permission prompt needed — this is available to *any* site "
                "you visit, including every data broker in this app:\n\n"
                "📨 **Your IP address** is sent automatically with every web request, the same way a "
                "return address is on an envelope.\n\n"
                "🌍 **Location and ISP** get derived by looking that IP up in a public registry that maps "
                "IP address ranges to the internet provider they were issued to, and roughly where that "
                "provider operates — same technique this page just used against `ipinfo.io`.\n\n"
                "💻 **Device type** comes straight from the `User-Agent` header your browser sends with "
                "every single request, automatically — the exact field the \"new sign-in\" email at the "
                "top of this card is inspired by.\n\n"
                f"🌐 Your browser also quietly reports its **language** (`{st.context.locale or 'unavailable'}`) "
                f"and **timezone** (`{st.context.timezone or 'unavailable'}`) on every visit, sight unseen.\n\n"
                "🎯 It's usually accurate to your **city/region**, not your exact address — precision "
                "depends on your ISP, not the site doing the looking.\n\n"
                "🛡️ A VPN (or your ISP's carrier-grade NAT) is what actually hides this — it swaps your "
                "real IP for the VPN provider's, so sites see the VPN server's location instead of yours."
            )

    with st.expander("🚩 Check your state's resources first"):
        st.caption(
            "A real, state-specific starting point where one exists — more states get added here as "
            "their own resources are verified, never guessed ahead of time."
        )
        state_choice = st.selectbox(
            "Your state", list(config.STATE_RESOURCES.keys()), key="results_state_choice", label_visibility="collapsed",
        )
        resource = config.STATE_RESOURCES[state_choice]
        st.info(resource["blurb"])
        if "action_url" in resource:
            st.link_button(resource["action_label"], resource["action_url"])
        elif "action_mode" in resource:
            if st.button(resource["action_label"], key="state_resource_action"):
                _switch_to(resource["action_mode"])

    exposure_placeholder = st.empty()

    broker_domains = [domain_from_url(u) for u in brokers_df["search_url"] if u]
    if broker_domains:
        with st.container(border=True):
            st.subheader("🌐 One-click search")
            st.link_button(
                "🌐 Search for yourself across every broker at once",
                build_combined_dork_url(name, location, broker_domains),
                type="primary",
                width="stretch",
            )
            st.caption(
                "A real, targeted Google search restricted to the broker sites below — opens in a new tab "
                "(Google blocks showing its results inside another site, so this can't be embedded here). "
                "Good for a quick overview; use each broker's own button below for a cleaner, one-at-a-time result."
            )

            historical_zips = database.parse_historical_zips(
                (database.get_latest_target_profile(runtime_mode.db_path()) or {}).get("historical_zip_codes")
            )
            if historical_zips:
                st.caption(
                    "Broker records are often keyed to a past address rather than your current one — "
                    "search under each former ZIP code from your profile too:"
                )
                for zip_code in historical_zips:
                    st.link_button(
                        f"🌐 Search under {zip_code}",
                        build_combined_dork_url(name, zip_code, broker_domains),
                        width="stretch",
                    )

    with st.container(border=True):
        st.subheader("🔔 Stay alerted automatically")
        st.caption(
            "Two free services that watch for you continuously, instead of waiting for your next manual "
            "check here — worth setting up once."
        )
        alert_cols = st.columns(2)
        alert_cols[0].link_button(
            "✉️ Get emailed on future breaches", config.HIBP_NOTIFY_URL, width="stretch",
        )
        alert_cols[0].caption("HaveIBeenPwned's own free notify-me — register your email once.")
        alert_cols[1].link_button(
            "🔍 Get alerted on new web mentions", config.GOOGLE_ALERTS_URL, width="stretch",
        )
        alert_cols[1].caption(f'Google Alerts — search `"{name}"` and save it as an alert.')

    st.subheader("📊 Exposure by category")
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
        card_cols[1].badge("Not checkable", icon="🚫", color="gray")
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
    st.subheader("🏢 Broker-by-broker detail")

    for _, broker in brokers_df.iterrows():
        broker_name = broker["broker_name"]
        is_automated = bool(broker["automated_search"])

        row_cols = st.columns([3, 2, 3])
        row_cols[0].markdown(f"**{broker_name}**")
        if broker["notes"]:
            row_cols[0].caption(broker["notes"])
        last_verified = broker.get("last_verified", "")
        if is_broker_stale(last_verified, config.BROKER_STALE_DAYS):
            days = days_since_verified(last_verified)
            age_text = f"{days} days ago" if days is not None else "date unknown"
            row_cols[0].caption(
                f"⚠️ This broker's contact info was last verified {age_text} "
                "— the email/opt-out link above may be out of date."
            )

        if is_automated and not runtime_mode.browser_automation_enabled():
            # Auto-search drives a headed Chrome via Playwright, which has no
            # display to launch into inside a container. Rather than let it
            # fail mid-demo, narrate what the local build does.
            if row_cols[1].button("🤖 Auto-search", key=f"autosearch_{broker_name}"):
                st.session_state[f"demo_autosearch_{broker_name}"] = True
            if st.session_state.get(f"demo_autosearch_{broker_name}"):
                with st.container(border=True):
                    st.caption("🛡️ Live browser automation runs locally in the desktop build.")
                    st.markdown(
                        f"""On the local build, **Auto-search** would now:
1. Launch your own Chrome against **{broker_name}**
2. Search for **{name}** in **{location}**
3. Dismiss the cookie/consent banner
4. Wait while you review the results yourself
5. Capture the record URL you click into, and prefill it on the Letters page

Nothing is automated here on the hosted demo — no browser is launched and no
request leaves this server."""
                    )
        elif is_automated:
            if row_cols[1].button("🤖 Auto-search", key=f"autosearch_{broker_name}"):
                with st.spinner(
                    f"Chrome is open and searching {broker_name} — review the results there, "
                    "then close that window to continue."
                ):
                    result = spokeo_automation.run_search_and_wait(name, location)
                if result["outcome"] == "timed_out":
                    wait_minutes = spokeo_automation.MAX_WAIT_SECONDS // 60
                    st.warning(f"Closed the {broker_name} window automatically after {wait_minutes} minutes of inactivity.")
                if result["record_url"]:
                    st.session_state.record_url = result["record_url"]
                    st.success(
                        f"Captured the record URL you clicked into on {broker_name} — "
                        "it's now prefilled on the Letters page."
                    )
        elif broker["search_url"]:
            broker_domain = domain_from_url(broker["search_url"])
            dork_url = build_broker_dork_url(name, location, broker_domain)
            row_cols[1].link_button("🌐 Targeted search", dork_url, key=f"selfsearch_link_{broker_name}")
        else:
            row_cols[1].caption("No search link on file")

        broker_category = f"broker:{broker_name}"
        stored_broker_entry = persisted.get(broker_category)
        broker_answer = row_cols[2].segmented_control(
            "Result", TRISTATE_OPTIONS, default=stored_broker_entry["value"] if stored_broker_entry else None,
            key=f"selfsearch_check_{broker_name}", label_visibility="collapsed",
        )
        # listed_confirmed feeds Letters' "already confirmed" gate, which only
        # needs a plain yes/no -- "Found exposure" is the only tri-state
        # answer that means yes.
        st.session_state.listed_confirmed[broker_name] = (broker_answer == "Found exposure")

        broker_entry = stored_broker_entry
        if broker_answer in ("Checked — clear", "Found exposure"):
            broker_entry = _record_if_changed(broker_category, broker_answer, persisted)
        broker_note = _staleness_note(broker_entry)
        if broker_note:
            row_cols[2].caption(broker_note)

    found_count = sum(1 for v in st.session_state.listed_confirmed.values() if v)
    checked_broker_count = sum(
        1 for _, b in brokers_df.iterrows()
        if persisted.get(f"broker:{b['broker_name']}", {}).get("value") in ("Checked — clear", "Found exposure")
    )
    total_count = len(brokers_df)
    check_count = sum(1 for v in [email_answer, phone_answer, social_answer] if v is not None)
    metric_placeholder.metric("Brokers confirmed listed", f"{found_count} / {total_count} checked")
    broker_label, broker_color, broker_icon = _broker_risk_badge(found_count, checked_broker_count, total_count)
    broker_badge_placeholder.badge(broker_label, icon=broker_icon, color=broker_color)

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
    combined_total = checked_broker_count + checklist_checked
    ratio = combined_found / combined_total if combined_total else 0
    stale_count = sum(
        1 for entry in persisted.values()
        if exposure_store.is_stale(entry["checked_at"], config.RECHECK_STALE_DAYS)
    )

    with exposure_placeholder.container():
        if combined_total == 0:
            st.markdown("## ❓ Not checked yet")
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
                f"⏰ {stale_count} {item_word} haven't been rechecked in "
                f"{config.RECHECK_STALE_DAYS}+ days — worth a fresh look below."
            )

    st.subheader("Other real ways to check yourself")
    link_cols = st.columns(4)
    search_engines = {
        "Google": f"https://www.google.com/search?q={name.replace(' ', '+')}",
        "Bing": f"https://www.bing.com/search?q={name.replace(' ', '+')}",
        "DuckDuckGo": f"https://duckduckgo.com/?q={name.replace(' ', '+')}",
        "Yahoo": f"https://search.yahoo.com/search?p={name.replace(' ', '+')}",
    }
    for idx, (engine_name, url) in enumerate(search_engines.items()):
        link_cols[idx].link_button(engine_name, url, icon="🔍")

    if found_count > 0:
        st.success(
            f"You're listed on {found_count} broker(s). Head to **Data Broker Deletion** "
            "in the sidebar — it already knows which brokers you confirmed here."
        )
    else:
        st.info("Check off any broker above where you found your own information.")
