"""
Render-level coverage: the app script actually executes without raising.

Everything else in tests/ is a pure-function test, which is where the
real logic lives -- but the Master Dashboard merged two pages into one
script path, and the failure mode for that is a Streamlit exception at
render time that no unit test would ever see. AppTest runs the real
script the same way the server does.

The scan itself is never triggered here, so nothing in this file makes a
network request. Both runtime modes are exercised because they take
different branches: demo mode swaps in mock scan results and hides
browser automation.
"""
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_PATH = os.path.join(ROOT, "app.py")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402  -- needs ROOT on the path first
import database  # noqa: E402

MASTER = "🔍 Intelligence Dossier"

TEST_PASSWORD = "render-test-password"


def _run(monkeypatch, tmp_path, demo=False, unlock=True):
    """Run the app against a throwaway database.

    Local mode resolves every store to config.TRACKER_DB_PATH, so that is
    the one knob that has to be redirected -- without it these tests
    would read and write the user's real campaign data. Demo mode routes
    to its own temp file already.

    `unlock` drives the vault gate added with the chino merge. Local mode
    now renders a master-password form and stops before any other widget,
    so a test that wants to reach the nav has to get past it first --
    which is what the gate is for. The salt and verifier land beside the
    temp database, so each test gets its own vault and none of them touch
    the real one. Pass unlock=False to exercise the gate itself.
    """
    monkeypatch.setattr(config, "TRACKER_DB_PATH", str(tmp_path / "tracker.db"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "1" if demo else "0")

    database.lock_vault()
    if unlock and not demo:
        database.unlock_vault(TEST_PASSWORD, str(tmp_path / "tracker.db"))

    app = AppTest.from_file(APP_PATH, default_timeout=60)
    if unlock and not demo:
        # The gate also checks a session flag, so that a locked vault in a
        # second tab re-prompts rather than riding on the first tab's unlock.
        app.session_state["_vault_unlocked"] = True
    app.run()
    return app


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_app_renders_without_exception(monkeypatch, tmp_path, demo):
    app = _run(monkeypatch, tmp_path, demo)
    assert not app.exception


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_master_dashboard_renders_without_exception(monkeypatch, tmp_path, demo):
    """The merged page is the whole point of this refactor -- if the two
    component renders can't coexist in one script run, it fails here."""
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value(MASTER).run()
    assert not app.exception


def test_master_dashboard_replaces_the_two_old_pages(monkeypatch, tmp_path):
    labels = _run(monkeypatch, tmp_path).radio(key="nav_mode").options
    assert MASTER in labels
    assert "🔍 Results" not in labels
    assert "🌐 Online Footprint" not in labels


def test_city_and_state_default_to_new_york(monkeypatch, tmp_path):
    app = _run(monkeypatch, tmp_path)
    assert app.session_state["pf_city"] == "New York"
    assert app.session_state["pf_state"] == "NY"


def test_app_starts_fresh_without_cached_profile(monkeypatch, tmp_path):
    """Each new session starts with a blank profile for privacy.
    Previously saved profiles are not automatically loaded."""
    import database

    db = str(tmp_path / "tracker.db")
    database.init_db(db)
    database.insert_target_profile(db, {
        "first_name": "Jane", "last_name": "Doe",
        "current_city": "Austin", "current_state": "TX",
    })

    app = _run(monkeypatch, tmp_path)
    # Form should start with defaults, not loaded profile
    assert app.session_state["pf_city"] == "New York"  # Default
    assert app.session_state["pf_state"] == "NY"  # Default
    # Not the saved profile values
    assert app.session_state["pf_first_name"] == ""  # Empty, not "Jane"
    assert app.session_state["pf_last_name"] == ""  # Empty, not "Doe"


# --- Deletion Timeline ------------------------------------------------
# Same reasoning as the Master Dashboard tests above: the timeline's
# failure mode is a render-time Streamlit exception (a bad column count, a
# missing session key) that the campaign_manager unit tests would never
# see, because they never run the component.

TIMELINE = "🗓️ Deletion Timeline"


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_deletion_timeline_renders_without_exception(monkeypatch, tmp_path, demo):
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    assert not app.exception


def test_deletion_timeline_is_in_the_nav(monkeypatch, tmp_path):
    assert TIMELINE in _run(monkeypatch, tmp_path).radio(key="nav_mode").options


def test_deletion_timeline_renders_populated_campaigns(monkeypatch, tmp_path):
    """An empty pipeline takes the early-return path, so the table body
    only actually executes once there are rows to draw."""
    import campaign_manager

    db = str(tmp_path / "tracker.db")
    overdue = campaign_manager.create_or_update_campaign(
        db, "BeenVerified", "beenverified.com", "https://beenverified.com/p/1"
    )
    campaign_manager.mark_dispatched(db, overdue, dispatch_date="2026-01-01")
    delisted = campaign_manager.create_or_update_campaign(db, "TruePeople", "truepeoplesearch.com")
    campaign_manager.record_delisting(db, delisted)
    campaign_manager.create_or_update_campaign(db, "Nuwber", "nuwber.com")

    app = _run(monkeypatch, tmp_path)
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    assert not app.exception

    # Assert the rows were really drawn. Without this the test would still
    # pass if the db path failed to line up -- the page would just take the
    # empty early-return and prove nothing about the table body.
    metric_values = [m.value for m in app.metric]
    assert "3" in metric_values, f"expected 3 tracked campaigns, got {metric_values}"


def test_existing_nav_pages_all_survive_the_timeline_addition(monkeypatch, tmp_path):
    """The timeline is additive -- nothing already in the nav may vanish."""
    labels = _run(monkeypatch, tmp_path).radio(key="nav_mode").options
    for existing in [
        # Renamed from "🛡️ Profile" when the nav was regrouped into
        # Recon / Legal / Tracking sections -- the page is the same one.
        "👤 Identity Profile",
        MASTER,
        "✉️ Data Broker Deletion",
        "⚖️ NY Expungement",
        "🚫 Google De-Indexing",
        "📬 Opt-Out Tracker",
    ]:
        assert existing in labels


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_presentation_mode_still_works_on_the_timeline(monkeypatch, tmp_path, demo):
    """Presentation mode seeds the demo profile from the sidebar, which
    renders before the page body -- the new page must not disturb that."""
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert app.session_state["presentation_mode"] is True
    assert app.session_state["pf_first_name"] == "Jane"


def test_opt_out_tracker_still_renders_alongside_the_new_ledger(monkeypatch, tmp_path):
    """Both tables live in one SQLite file; neither may break the other."""
    import campaign_manager
    import tracker

    db = str(tmp_path / "tracker.db")
    tracker.add_request(db, "Spokeo", "Email", config.CCPA_RESPONSE_WINDOW_DAYS)
    campaign_manager.create_or_update_campaign(db, "Spokeo", "spokeo.com")

    app = _run(monkeypatch, tmp_path)
    app.radio(key="nav_mode").set_value("📬 Opt-Out Tracker").run()
    assert not app.exception
    app.radio(key="nav_mode").set_value(TIMELINE).run()
    assert not app.exception


# ---------------------------------------------------------------------------
# Vault gate (added with the chino merge)
# ---------------------------------------------------------------------------


def test_vault_gate_blocks_the_app_when_locked(monkeypatch, tmp_path):
    """A locked local session must not render the app behind the form.

    This is the test that makes the gate worth having: without it, the
    other tests in this file could be made to pass by deleting the gate.
    """
    app = _run(monkeypatch, tmp_path, demo=False, unlock=False)
    assert not app.exception
    with pytest.raises(KeyError):
        app.radio(key="nav_mode")


def test_vault_gate_lets_an_unlocked_session_through(monkeypatch, tmp_path):
    app = _run(monkeypatch, tmp_path, demo=False, unlock=True)
    assert not app.exception
    assert app.radio(key="nav_mode") is not None


def test_demo_mode_is_exempt_from_the_gate(monkeypatch, tmp_path):
    """The hosted demo build has no real PII to protect -- see vault_gate."""
    app = _run(monkeypatch, tmp_path, demo=True, unlock=False)
    assert not app.exception
    assert app.radio(key="nav_mode") is not None


# --- Navigation regrouping -------------------------------------------
# The sidebar was regrouped into Recon / Legal / Tracking sections and the
# previously orphaned components/footprint.py was wired into the nav. Both
# failure modes are render-time: a caption list that doesn't line up with
# the options, or a component that no code path had ever executed.


def test_nav_options_stay_grouped_by_section(monkeypatch, tmp_path):
    """st.radio pairs captions to options positionally, so the options have
    to stay contiguous per section -- interleaving them would caption a tool
    with the wrong section name rather than raise."""
    nav = _run(monkeypatch, tmp_path).radio(key="nav_mode")
    assert nav.options == [
        "👤 Identity Profile",
        "🔍 Intelligence Dossier",
        "✉️ Data Broker Deletion",
        "🚫 Google De-Indexing",
        "⚖️ NY Expungement",
        "📬 Opt-Out Tracker",
        "🗓️ Deletion Timeline",
    ]


def test_every_page_in_the_mpa_is_reachable_from_the_sidebar(monkeypatch, tmp_path):
    """Turning off Streamlit's automatic page list stranded all of pages/.

    That list was the only route to them -- nothing calls st.page_link or
    st.switch_page otherwise -- so every file in pages/ silently became
    unreachable, including the OSINT Toolkit and the full-registry
    footprint sweep. Any page added later has to be linked too.
    """
    import pathlib

    from components import nav

    linked = {path for _, links in nav.MPA_SECTIONS for path, _, _ in links}
    on_disk = {
        f"pages/{p.name}"
        for p in pathlib.Path(ROOT, "pages").glob("*.py")
        if not p.name.startswith("_")
    }

    assert on_disk, "expected pages/ to contain page scripts"
    assert not (on_disk - linked), f"pages with no sidebar link: {sorted(on_disk - linked)}"
    assert not (linked - on_disk), f"sidebar links to missing pages: {sorted(linked - on_disk)}"


def test_every_page_renders_a_way_back_to_the_main_app(monkeypatch, tmp_path):
    """A pages/ script runs on its own -- app.py's sidebar is not there.

    With Streamlit's automatic page list off, a page that draws no links of
    its own is a dead end: no route back, and a refresh reloads the page
    you are stuck on. page_shell.setup() is the single chokepoint every
    page goes through, so the exit has to be rendered from there.
    """
    import inspect
    import pathlib

    from components import nav, page_shell

    source = inspect.getsource(page_shell.setup)
    assert "include_home=True" in source, "pages must render the home link"
    assert nav.HOME_PAGE == "app.py"
    assert pathlib.Path(ROOT, nav.HOME_PAGE).exists()

    # Every page must actually route through that chokepoint.
    for page in pathlib.Path(ROOT, "pages").glob("*.py"):
        text = page.read_text(encoding="utf-8")
        assert "setup(" in text, f"{page.name} never calls page_shell.setup()"


def test_sidebar_navigation_is_disabled_so_the_links_are_the_only_nav(monkeypatch, tmp_path):
    """The links above only replace the raw file list if it stays off."""
    import pathlib

    toml = pathlib.Path(ROOT, ".streamlit", "config.toml").read_text(encoding="utf-8")
    assert "showSidebarNavigation = false" in toml


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_deep_handle_footprint_renders_without_exception(monkeypatch, tmp_path, demo):
    """components/footprint.py had no caller before it was added to the
    nav, so this is the first path that actually executes its render()."""
    app = _run(monkeypatch, tmp_path, demo)
    app.radio[1].set_value("🖥️ Desktop (full power)").run()
    app.radio(key="nav_mode").set_value("🕸️ Deep Handle Footprint").run()
    assert not app.exception


def test_deep_handle_footprint_is_in_the_nav(monkeypatch, tmp_path):
    # Hidden by default in online mode
    at = _run(monkeypatch, tmp_path)
    assert "🕸️ Deep Handle Footprint" not in at.radio(key="nav_mode").options
    
    # Shown when desktop mode is selected
    at.radio[1].set_value("🖥️ Desktop (full power)").run()
    assert "🕸️ Deep Handle Footprint" in at.radio(key="nav_mode").options


def test_no_nav_target_points_at_a_label_that_does_not_exist(monkeypatch, tmp_path):
    """Quick-action buttons set pending_nav to a nav label. Before the
    regrouping, letters.py and results.py both pointed at "📊 Dashboard",
    which had never been one of the options -- so those buttons silently
    navigated nowhere. Every literal assigned to pending_nav must be a real
    option.
    """
    import ast
    import pathlib

    labels = set(_run(monkeypatch, tmp_path).radio(key="nav_mode").options)

    targets = []
    for path in [pathlib.Path(ROOT, "app.py"), *pathlib.Path(ROOT, "components").glob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # st.session_state.pending_nav = "..."
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "pending_nav":
                        targets.append((path.name, node.value.value))
            # _switch_to("...")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "_switch_to" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                targets.append((path.name, node.args[0].value))

    assert targets, "expected to find at least one navigation target"
    bad = [(name, value) for name, value in targets if value not in labels]
    assert not bad, f"navigation targets that match no nav option: {bad}"


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_ny_expungement_renders_without_exception(monkeypatch, tmp_path, demo):
    """The four-stage rebuild put ~200 lines of new widgets on this page.

    Every unit test around it is a pure-function test of ny_sealing; none
    of them would catch a Streamlit widget raising at render time, which
    is the failure mode a presentation would actually hit.
    """
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value("⚖️ NY Expungement").run()
    assert not app.exception


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_ny_expungement_screening_submits_without_exception(monkeypatch, tmp_path, demo):
    """Submitting drives screen() -> build_record() -> fill_motion() and a
    download button, which is the whole pipeline rendering for real."""
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value("⚖️ NY Expungement").run()

    submit = next(b for b in app.button if b.label == "Run screening")
    submit.set_value(True).run()

    assert not app.exception
    headings = [h.value for h in app.subheader]
    # Proves stage 2 and stage 4 both ran: the pathway table is the
    # screening output, the download button only exists once fill_motion()
    # has returned real PDF bytes.
    assert "Every pathway" in headings
    assert "CPL 160.59 motion" in headings
    assert len(app.dataframe) == 1
    assert any(d.label.endswith("(PDF)") for d in app.download_button)


@pytest.mark.parametrize("demo", [False, True], ids=["local", "demo"])
def test_google_de_indexing_renders_without_exception(monkeypatch, tmp_path, demo):
    app = _run(monkeypatch, tmp_path, demo)
    app.radio(key="nav_mode").set_value("🚫 Google De-Indexing").run()
    assert not app.exception


def test_intelligence_dossier_renders_social_and_email_findings(monkeypatch, tmp_path):
    app = _run(monkeypatch, tmp_path, demo=False)
    app.session_state["pf_full_name"] = "Jane Doe"
    app.session_state["pf_email"] = "jane@example.com"
    app.session_state["pf_handle"] = "janedoe"
    app.session_state["osint_findings"] = {
        "summary": {"total_exposures": 4},
        "vectors": {
            "footprint": {
                "status": "success",
                "count": 2,
                "records": [
                    {"platform": "GitHub", "category": "Development", "confidence": "CONFIRMED", "profile_url": "https://github.com/janedoe"},
                    {"platform": "Reddit", "category": "Social", "confidence": "CONFIRMED", "profile_url": "https://reddit.com/u/janedoe"},
                ],
            },
            "email": {
                "status": "success",
                "count": 2,
                "records": [
                    {"platform": "Gravatar", "service": "Gravatar", "confidence": "CONFIRMED", "reason": "profile found"},
                    {"platform": "Have I Been Pwned", "service": "Have I Been Pwned", "confidence": "CONFIRMED", "reason": "breach found"},
                ],
            },
        },
    }
    app.radio(key="nav_mode").set_value(MASTER).run()
    assert not app.exception
    # Check that metric values include the score of 4 and vector counts
    metric_values = [m.value for m in app.metric]
    assert 4 in metric_values or "4" in [str(v) for v in metric_values]


# --- Actionable dossier cards -----------------------------------------
# The dossier used to be read-only: a finding was rendered and then
# evaporated on the next rerun. Each card now carries an "Add to Closure
# Worklist" action. Asserting only `not app.exception` would not cover it
# -- the render loop catches per-record exceptions and continues, so a
# card that silently failed to draw its button still looks like a clean
# render. These two tests assert the button exists and that pressing it
# actually reaches SQLite.


def _dossier_with_findings(monkeypatch, tmp_path):
    """A dossier run with a populated profile and two confirmed findings.

    The profile has to be written as the canonical `profile` dict rather
    than the pf_* widget keys: profile_state.get_profile() reads the
    former, and the pf_* keys only seed it on the very first run.
    """
    app = _run(monkeypatch, tmp_path)
    app.session_state["profile"] = {
        "full_name": "Jane Doe", "email": "jane@example.com", "handle": "janedoe",
        "city": "New York", "state": "NY", "zip_code": "", "phone": "",
        "domain": "", "country": "US",
    }
    app.session_state["osint_findings"] = {
        "summary": {"total_exposures": 2},
        "vectors": {
            "footprint": {"status": "success", "count": 1, "records": [
                {"platform": "GitHub", "category": "Development",
                 "confidence": "CONFIRMED", "profile_url": "https://github.com/janedoe"}]},
            "email": {"status": "success", "count": 1, "records": [
                {"platform": "Gravatar", "service": "Gravatar",
                 "confidence": "CONFIRMED", "reason": "profile found"}]},
        },
    }
    app.radio(key="nav_mode").set_value(MASTER).run()
    return app


def test_dossier_findings_offer_a_worklist_action(monkeypatch, tmp_path):
    app = _dossier_with_findings(monkeypatch, tmp_path)
    assert not app.exception
    worklist_buttons = [b for b in app.button if "Closure Worklist" in b.label]
    # One per confirmed finding: the GitHub footprint hit and the Gravatar
    # email hit. The card renders inside the dossier's own two-column
    # layout, so this also pins that the nested columns it adds are legal.
    assert len(worklist_buttons) == 2


def test_worklist_action_writes_the_finding_to_the_database(monkeypatch, tmp_path):
    """The button is only worth having if the row survives the rerun."""
    import discovered_accounts

    app = _dossier_with_findings(monkeypatch, tmp_path)
    next(b for b in app.button if "Closure Worklist" in b.label).set_value(True).run()
    assert not app.exception

    rows = discovered_accounts.get_all(str(tmp_path / "tracker.db"))
    assert len(rows) == 1
    row = rows[0]
    assert row["platform"] == "GitHub"
    # The identifier falls back to the profile handle -- without it the
    # upsert key would be empty and every finding would collide on one row.
    assert row["target_identifier"] == "janedoe"
    assert row["profile_url"] == "https://github.com/janedoe"
    assert row["status"] == discovered_accounts.STATUS_NEW


def test_intelligence_dossier_handles_empty_fields_and_clean_diagnostics(monkeypatch, tmp_path):
    app = _run(monkeypatch, tmp_path, demo=False)
    app.session_state["pf_full_name"] = "Jane Doe"
    app.session_state["pf_email"] = "jane@example.com"
    app.session_state["pf_handle"] = ""  # No handle
    app.session_state["osint_findings"] = {
        "summary": {"total_exposures": 0},
        "vectors": {
            "footprint": {"status": "empty", "count": 0, "records": []},
            "email": {"status": "empty", "count": 0, "records": []},
        },
    }
    app.radio(key="nav_mode").set_value(MASTER).run()
    assert not app.exception



# --- sidebar layout ---------------------------------------------------
# Presentation mode swaps the data the tools read for mock findings, so it
# belongs to the tool picker. It used to sit under the pages/ links, which
# put it between two things it has nothing to do with and read as a
# property of the agent console rather than of the tools above it.


def test_presentation_mode_sits_directly_under_the_tools_radio(monkeypatch, tmp_path):
    """Asserted against the source rather than the rendered tree: the
    sidebar is one flat container, so a rendered test can tell you the
    toggle exists but not what it is next to."""
    import pathlib as _pathlib

    source = _pathlib.Path(ROOT, "app.py").read_text(encoding="utf-8")
    radio = source.index('st.sidebar.radio(')
    toggle = source.index('"🎭 Presentation mode"')
    page_links = source.index('nav.render_page_links()')
    assert radio < toggle < page_links, (
        "presentation mode must render between the Tools radio and the "
        "pages/ links"
    )


def test_the_app_opens_on_the_online_runtime(monkeypatch, tmp_path):
    """The desktop build is a separate install; a browser session that has
    chosen nothing starts on the shape it can actually run."""
    import runtime_mode

    app = _run(monkeypatch, tmp_path)
    assert not app.exception
    assert app.session_state["runtime_override"] == runtime_mode.OVERRIDE_CLOUD


def test_the_owner_console_is_not_linked_for_an_ordinary_session(monkeypatch, tmp_path):
    """The gate on pages/11_Admin.py is the real check, but the console
    should not advertise itself in every visitor's sidebar either."""
    import admin_auth

    monkeypatch.delenv(admin_auth.ADMIN_KEY_ENV, raising=False)
    app = _run(monkeypatch, tmp_path)
    assert not app.exception
    captions = [c.value for c in app.sidebar.caption]
    assert "Owner" not in captions


# --- runtime selector -------------------------------------------------
# Auto-detect came off the selector once the app started defaulting to
# online: an "Auto-detect" option and a default are two answers to the
# same question, and the one the user could see was not the one in force.


def _runtime_radio(app):
    return next(r for r in app.sidebar.radio
                if r.label == "Select operating environment")


def test_the_runtime_selector_offers_only_explicit_choices(monkeypatch, tmp_path):
    radio = _runtime_radio(_run(monkeypatch, tmp_path))
    assert radio.options == [
        "☁️ Online / cloud (passive)",
        "🖥️ Desktop (full power)",
    ]
    assert not any("auto" in o.lower() for o in radio.options)


def test_the_selector_opens_on_online(monkeypatch, tmp_path):
    """The default and the selection have to agree -- a radio showing
    Desktop while the session runs online is the confusion removing
    Auto-detect was meant to end."""
    import runtime_mode

    app = _run(monkeypatch, tmp_path)
    assert _runtime_radio(app).value == "☁️ Online / cloud (passive)"
    assert app.session_state["runtime_override"] == runtime_mode.OVERRIDE_CLOUD


def test_a_session_still_carrying_auto_lands_on_online(monkeypatch, tmp_path):
    """Auto is still a valid module-level value (reset_runtime_override
    returns it, and the tests below rely on it), so the selector has to
    resolve it rather than raise on an index it cannot find."""
    app = AppTest.from_file(APP_PATH, default_timeout=60)
    monkeypatch.setattr(config, "TRACKER_DB_PATH", str(tmp_path / "tracker.db"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "1")
    app.session_state["runtime_override"] = "auto"
    app.session_state["_runtime_startup_default_applied"] = True
    app.run()
    assert not app.exception
    assert _runtime_radio(app).value == "☁️ Online / cloud (passive)"


def test_switching_to_desktop_still_works(monkeypatch, tmp_path):
    import runtime_mode

    app = _run(monkeypatch, tmp_path)
    _runtime_radio(app).set_value("🖥️ Desktop (full power)").run()
    assert not app.exception
    assert app.session_state["runtime_override"] == runtime_mode.OVERRIDE_DESKTOP


# --- the campaign panel is desktop-only -------------------------------
# It tracks statutory deadlines against records that only persist on a
# local install, so on the online build it is a panel of zeroes with two
# buttons behind it.

CAMPAIGN_HEADING = "Your Opt-Out Campaign"


def _dashboard_text(app):
    """Rendered markdown, minus the injected stylesheet -- theme.py's own
    comments name the panel, so searching the raw markdown would match
    the CSS that hides it."""
    return " ".join(
        str(m.value) for m in app.markdown if not str(m.value).startswith("<style>")
    )


def test_the_campaign_panel_is_hidden_on_the_online_build(monkeypatch, tmp_path):
    app = _run(monkeypatch, tmp_path)
    assert not app.exception
    assert CAMPAIGN_HEADING not in _dashboard_text(app)


def test_the_campaign_panel_is_there_on_the_desktop_build(monkeypatch, tmp_path):
    """The other half of the assertion -- without it, a change that hid
    the panel everywhere would pass the test above."""
    app = _run(monkeypatch, tmp_path)
    _runtime_radio(app).set_value("🖥️ Desktop (full power)").run()
    assert not app.exception
    assert CAMPAIGN_HEADING in _dashboard_text(app)
