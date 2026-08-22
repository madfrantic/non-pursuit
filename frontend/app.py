"""Streamlit dashboard for the Non-Pursuit compliance API."""
from __future__ import annotations

import io
import os
import re
import textwrap

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="Non-Pursuit | Exposure desk",
    page_icon=":material/shield:",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_DEFAULT = "http://127.0.0.1:8000"
REQUEST_TIMEOUT = (8, 300)
HIGH_CONFIDENCE = 70.0


class ApiError(RuntimeError):
    """An API failure safe to show without exposing a traceback."""


def _api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _request_json(method: str, base_url: str, path: str, **kwargs) -> dict:
    try:
        response = requests.request(
            method, _api_url(base_url, path), timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
    except requests.Timeout as exc:
        raise ApiError("The backend timed out. Check that the API is running and try again.") from exc
    except requests.RequestException as exc:
        detail = getattr(exc.response, "text", "").strip() if exc.response is not None else ""
        detail = detail[:240] if detail else "connection failed"
        raise ApiError(f"Backend request failed: {detail}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError("The backend returned malformed JSON.") from exc
    if not isinstance(payload, dict):
        raise ApiError("The backend returned an unexpected response shape.")
    return payload


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip()).strip("_.-")


def _effective_handle(username: str, email: str, full_name: str, domain: str) -> str:
    if username.strip():
        return username.strip()
    if email.strip() and "@" in email:
        return _slug(email.split("@", 1)[0])
    if full_name.strip():
        return _slug(full_name).lower()
    return _slug(domain.split(".", 1)[0]).lower()


def _subject_payload(username: str, email: str, full_name: str, domain: str,
                    jurisdiction: str | None = None) -> dict:
    subject = {
        "handle": _effective_handle(username, email, full_name, domain),
        "name": full_name.strip(),
        "email": email.strip() or None,
    }
    if jurisdiction == "CCPA":
        subject.update({"state": "CA", "country": "US"})
    elif jurisdiction == "GDPR":
        subject.update({"country": "DE"})
    return subject


def _scan_payload(username: str, email: str, full_name: str, domain: str,
                  include_brokers: bool) -> dict:
    payload = {
        "subject": _subject_payload(username, email, full_name, domain),
        "options": {
            "probe_brokers": include_brokers,
            "discover_contacts": True,
        },
    }
    return payload


def _exposures(report: dict) -> list[dict]:
    rows = report.get("exposures", [])
    return [row for row in rows if isinstance(row, dict) and row.get("platform")]


def _signal_text(row: dict) -> str:
    evidence = row.get("evidence") or []
    signals = [item.get("kind", "") for item in evidence if item.get("kind")]
    return ", ".join(dict.fromkeys(signals)) or "Existence check only"


def _verified_label(row: dict) -> str:
    verdict = str(row.get("verdict", "")).upper()
    if verdict == "CONFIRMED":
        return "Confirmed"
    if verdict == "POSSIBLE":
        return "Needs review"
    return verdict.replace("_", " ").title() or "Unknown"


def _table_rows(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Select": False,
            "Platform": row.get("platform", ""),
            "Profile URL": row.get("url", ""),
            "Confidence Score": float(row.get("confidence", 0.0) or 0.0),
            "Matched Signals": _signal_text(row),
            "Verified Status": _verified_label(row),
        }
        for row in rows
    ])


def _selected_rows(rows: list[dict], table: pd.DataFrame) -> list[dict]:
    selected = table["Select"].astype(bool).tolist() if not table.empty else []
    return [row for row, is_selected in zip(rows, selected) if is_selected]


def _metric_values(report: dict) -> tuple[int, int, int]:
    scan = report.get("scan") or {}
    exposures = _exposures(report)
    confirmed = sum(str(row.get("verdict", "")).upper() == "CONFIRMED" for row in exposures)
    high = sum(float(row.get("confidence", 0) or 0) >= HIGH_CONFIDENCE for row in exposures)
    return int(scan.get("sites_probed", scan.get("sites_in_registry", 0)) or 0), confirmed, high


def _contact_breakdown(report: dict) -> pd.DataFrame:
    labels = {
        "privacy_email": "Privacy email",
        "dsar_portal": "DSAR portal",
        "abuse_fallback": "Abuse fallback",
    }
    counts = {label: 0 for label in labels.values()}
    for payload in (report.get("remediation") or {}).get("payloads", []):
        label = labels.get(payload.get("delivery_channel"))
        if label:
            counts[label] += 1
    return pd.DataFrame({"Contact method": list(counts), "Exposures": list(counts.values())})


def _render_pdf(text: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas
    except ImportError:
        return text.encode("utf-8")

    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=LETTER)
    _, height = LETTER
    y = height - 54
    for paragraph in text.splitlines() or [""]:
        for line in textwrap.wrap(paragraph, width=92) or [""]:
            if y < 48:
                document.showPage()
                y = height - 54
            document.drawString(48, y, line)
            y -= 14
    document.save()
    return output.getvalue()


def _render_payloads(payloads: list[dict]) -> None:
    if not payloads:
        st.info("No demand payloads have been generated yet.", icon=":material/drafts:")
        return
    options = [f"{item.get('target_name', 'Unknown')} | {item.get('subject', 'Demand')}"
               for item in payloads]
    left, right = st.columns([0.32, 0.68])
    with left:
        index = st.radio("Letters", range(len(payloads)),
                         format_func=lambda value: options[value])
    with right:
        payload = payloads[index]
        body = payload.get("body", "")
        st.caption(
            f"{payload.get('jurisdiction', 'Generic')} · {payload.get('delivery_channel', 'none')} · "
            f"{payload.get('confidence', 0):.1f}% confidence")
        st.code(body, language="text")
        stem = _slug(payload.get("target_name", "demand")) or "demand"
        st.download_button("Download Markdown", f"# {payload.get('subject', 'Demand')}\n\n{body}",
                           file_name=f"{stem}.md", mime="text/markdown", icon=":material/markdown:")
        st.download_button("Download plain text", body, file_name=f"{stem}.txt",
                           mime="text/plain", icon=":material/description:")
        st.download_button("Download PDF", _render_pdf(body), file_name=f"{stem}.pdf",
                           mime="application/pdf", icon=":material/picture_as_pdf:")


def _optout_brokers(base_url: str) -> list[str]:
    """Broker ids the backend can actually drive, straight from the backend."""
    try:
        brokers = _request_json("GET", base_url, "/api/optout/brokers").get("brokers", [])
    except ApiError:
        return []
    return [str(broker) for broker in brokers if str(broker).strip()]


def _init_state() -> None:
    st.session_state.setdefault("report", None)
    st.session_state.setdefault("table", pd.DataFrame())
    st.session_state.setdefault("remediation", None)
    st.session_state.setdefault("target_subject", None)


def _sidebar() -> str:
    with st.sidebar:
        st.markdown("## Non-Pursuit")
        st.caption("Public exposure review and rights-request drafting")
        return st.text_input(
            "Backend URL", value=os.getenv("NONPURSUIT_API_URL", API_DEFAULT),
            help="The FastAPI service exposing /api/scan and /api/remediate.")


def _scan_tab(base_url: str) -> None:
    st.subheader("Target input")
    with st.form("scan_form", clear_on_submit=False):
        left, right = st.columns(2)
        with left:
            username = st.text_input("Username", placeholder="jane_doe")
            email = st.text_input("Email", placeholder="jane@example.com")
        with right:
            full_name = st.text_input("Full name", placeholder="Jane Doe")
            domain = st.text_input("Domain", placeholder="example.com")
        include_brokers = st.toggle("Include data broker probes", value=True)
        submitted = st.form_submit_button("Run scan", type="primary", icon=":material/search:")

    if not submitted:
        st.caption("Provide at least one target field. The backend requires a handle; one is derived when Username is blank.")
        return
    if not any(value.strip() for value in (username, email, full_name, domain)):
        st.warning("Enter a username, email, full name, or domain before running a scan.", icon=":material/warning:")
        return

    with st.status("Running exposure scan", expanded=True) as status:
        st.write("Querying the configured site registry and broker probes...")
        try:
            report = _request_json(
                "POST", base_url, "/api/scan?wait=true",
                json=_scan_payload(username, email, full_name, domain, include_brokers),
            )
        except ApiError as exc:
            status.update(label="Scan failed", state="error")
            st.error(str(exc), icon=":material/error:")
            return
        st.session_state.report = report
        st.session_state.target_subject = _subject_payload(username, email, full_name, domain)
        st.session_state.table = _table_rows(_exposures(report))
        st.session_state.remediation = None
        status.update(label="Scan complete", state="complete")
    st.success("Results are ready in the Results & graph tab.", icon=":material/check_circle:")


def _results_tab() -> None:
    report = st.session_state.report
    if not report:
        st.info("Run a scan to populate the exposure graph.", icon=":material/analytics:")
        return

    scanned, confirmed, high = _metric_values(report)
    with st.container(horizontal=True):
        st.metric("Sites scanned", scanned, border=True)
        st.metric("Confirmed exposures", confirmed, border=True)
        st.metric("High-confidence matches", high, border=True)

    st.subheader("Exposure graph")
    st.caption("Each row is a scored identity claim. Select confirmed rows for remediation.")
    table = st.session_state.table
    if table.empty:
        st.info("No scored account exposures were returned.", icon=":material/search_off:")
    else:
        edited = st.data_editor(
            table,
            key="exposure_selector",
            hide_index=True,
            disabled=[column for column in table.columns if column != "Select"],
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", help="Include in demand payload"),
                "Profile URL": st.column_config.LinkColumn("Profile URL"),
                "Confidence Score": st.column_config.ProgressColumn(
                    "Confidence Score", min_value=0, max_value=100, format="%.1f%%"),
            },
        )
        st.session_state.table = edited

    st.subheader("Contact resolution")
    breakdown = _contact_breakdown(report)
    st.bar_chart(breakdown, x="Contact method", y="Exposures", horizontal=True)


def _remediation_tab(base_url: str) -> None:
    st.subheader("Remediation desk")
    report = st.session_state.report
    if not report:
        st.info("Run a scan before generating a demand payload.", icon=":material/drafts:")
        return

    rows = _exposures(report)
    selected = _selected_rows(rows, st.session_state.table)
    st.metric("Selected exposures", len(selected))
    jurisdiction = st.segmented_control(
        "Jurisdiction", ["CCPA", "GDPR", "Generic"], default="Generic",
        selection_mode="single")
    if jurisdiction is None:
        jurisdiction = "Generic"
    st.caption("CCPA and GDPR route through the backend’s existing state/country fields; Generic uses its fallback route.")

    if st.button("Generate demand payload", type="primary", icon=":material/edit_document:"):
        if not selected:
            st.warning("Select at least one exposure in the Results & graph tab.", icon=":material/check_box:")
        else:
            with st.spinner("Rendering deletion demands..."):
                subject = dict(st.session_state.target_subject or {})
                if jurisdiction == "CCPA":
                    subject.update({"state": "CA", "country": "US"})
                elif jurisdiction == "GDPR":
                    subject.update({"country": "DE"})
                else:
                    subject.pop("state", None)
                    subject.pop("country", None)
                payload = {"subject": subject, "exposures": selected,
                           "min_confidence": 0.0, "discover_contacts": True}
                try:
                    st.session_state.remediation = _request_json(
                        "POST", base_url, "/api/remediate", json=payload)
                except ApiError as exc:
                    st.error(str(exc), icon=":material/error:")

    result = st.session_state.remediation
    if result:
        payloads = (result.get("remediation") or {}).get("payloads", [])
        ready = (result.get("remediation") or {}).get("ready_to_send", 0)
        if ready:
            st.warning(f"{ready} payload(s) are marked ready by the backend. Review every letter before dispatch.", icon=":material/gavel:")
        else:
            st.info("Payloads are drafts and require human sign-off before dispatch.", icon=":material/preview:")
        _render_payloads(payloads)


def _takedown_tab(base_url: str) -> None:
    st.subheader("Broker opt-out engine")
    st.markdown(
        "Fills a broker's opt-out form in a headless browser. It stops before "
        "submitting — review the request and send it yourself.")

    brokers = _optout_brokers(base_url)
    if not brokers:
        st.info(
            "The backend reports no opt-out automators. Check that it is running, "
            "or add a broker class in utils/optout_engine.py.",
            icon=":material/info:")
        return

    with st.form("takedown_form"):
        left, right = st.columns(2)
        first_name = left.text_input("First name", placeholder="John")
        last_name = right.text_input("Last name", placeholder="Doe")
        city = left.text_input("City", placeholder="New York")
        state = right.text_input("State", placeholder="NY")
        email = st.text_input(
            "Confirmation email", placeholder="takedowns@example.com",
            help="Where the broker sends its confirmation link. Use a masked address.")
        broker = st.selectbox("Target broker", brokers)
        submitted = st.form_submit_button("Fill opt-out form", icon=":material/robot_2:")

    if not submitted:
        return
    if not (first_name.strip() and last_name.strip() and email.strip()):
        st.error("First name, last name, and email are required.", icon=":material/error:")
        return

    payload = {
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "city": city.strip(),
        "state": state.strip(),
        "email": email.strip(),
        "broker": broker,
    }
    with st.spinner(f"Queueing the opt-out worker for {broker}..."):
        try:
            data = _request_json("POST", base_url, "/api/optout/trigger", json=payload)
        except ApiError as exc:
            st.error(str(exc), icon=":material/error:")
            return

    st.success(f"Queued for {data.get('broker', broker)}.", icon=":material/check_circle:")
    st.info(data.get("note", "The form is filled and left unsubmitted."),
            icon=":material/preview:")


def main() -> None:
    _init_state()
    base_url = _sidebar()
    st.title("Exposure desk")
    st.caption("Trace public identity signals, inspect corroboration, and prepare rights requests.")
    scan_tab, results_tab, remediation_tab, takedown_tab = st.tabs([
        "Target input", "Results & graph", "Remediation & letters", "Broker opt-out"
    ])
    with scan_tab:
        _scan_tab(base_url)
    with results_tab:
        _results_tab()
    with remediation_tab:
        _remediation_tab(base_url)
    with takedown_tab:
        _takedown_tab(base_url)


if __name__ == "__main__":
    main()