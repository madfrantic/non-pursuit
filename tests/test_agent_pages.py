"""
Render coverage for the pages/ multi-page app.

Each page in pages/ is its own top-level script, so a NameError or a bad
import in one is invisible to every other test in this suite -- and
invisible until somebody clicks that page in a browser. AppTest runs each
one the way the server does.

No page here triggers a scan, so nothing in this file makes a network
request: the engines are only constructed, never run.
"""
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAGES_DIR = os.path.join(ROOT, "pages")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import broker_ledger  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
from broker_agent_models import Broker  # noqa: E402

PAGES = sorted(f for f in os.listdir(PAGES_DIR) if f.endswith(".py"))

TEST_PASSWORD = "page-render-password"


@pytest.fixture
def unlocked(monkeypatch, tmp_path):
    """A temp database with the vault unlocked and a profile seeded."""
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(config, "TRACKER_DB_PATH", db_path)
    monkeypatch.setattr(config, "REVIEW_QUEUE_DB_PATH", str(tmp_path / "review.db"))
    monkeypatch.setattr(config, "EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "0")

    database.lock_vault()
    database.unlock_vault(TEST_PASSWORD, db_path)
    broker_ledger.init_ledger(db_path)
    yield db_path
    database.lock_vault()


def _run(page_name):
    app = AppTest.from_file(os.path.join(PAGES_DIR, page_name), default_timeout=60)
    app.session_state["_vault_unlocked"] = True
    app.run()
    return app


def test_every_page_is_numbered_for_the_nav():
    """Streamlit orders pages/ by filename prefix."""
    assert PAGES, "no pages found"
    for name in PAGES:
        assert name[0].isdigit(), f"{name} has no sort prefix"


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_empty_state_without_exception(unlocked, page):
    app = _run(page)
    assert not app.exception, f"{page} raised: {app.exception}"


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_data_without_exception(unlocked, page):
    """The populated path takes different branches from the empty one --
    dataframes, metrics, per-row buttons."""
    import review_queue
    from broker_agent_models import BrokerDifficulty, EVIDENCE_BEFORE

    db = unlocked
    pid = broker_ledger.create_profile(db, "Ada Lovelace")
    broker_ledger.save_pii_field(db, pid, "first_name", "Ada")
    broker_ledger.save_pii_field(db, pid, "last_name", "Lovelace")

    bid = broker_ledger.add_broker(db, Broker(
        name="Spokeo", difficulty=BrokerDifficulty.CAPTCHA,
        opt_out_url="https://example.com/optout",
        compliance_email="privacy@example.com"))
    plain = broker_ledger.add_broker(db, Broker(name="WhitePages"))

    sid = broker_ledger.create_scan(db, pid, bid)
    broker_ledger.update_scan(db, sid, found=True, status="completed",
                              result_url="https://example.com/record/1")
    rid = broker_ledger.create_removal(db, sid, pid, bid)
    broker_ledger.update_removal(db, rid, status="submitted")
    broker_ledger.add_evidence(db, rid, EVIDENCE_BEFORE,
                               file_path="/nonexistent/shot.png",
                               file_hash="a" * 64)
    broker_ledger.record_scheduled_task(db, "monitoring", profile_id=pid, interval_days=7)
    review_queue.add_item(config.REVIEW_QUEUE_DB_PATH, target="Spokeo",
                          task_type="broker_search", reason=review_queue.CAPTCHA,
                          note="solve by hand", current_url="https://example.com")

    app = _run(page)
    assert not app.exception, f"{page} raised with data: {app.exception}"


@pytest.mark.parametrize("page", PAGES)
def test_page_is_gated_when_the_vault_is_locked(monkeypatch, tmp_path, page):
    """A page must not render identity data for a locked session.

    The gate lives in components/page_shell.setup(), which every page
    calls; this is what catches a page that forgets to.
    """
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(config, "TRACKER_DB_PATH", db_path)
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "0")
    database.lock_vault()

    app = AppTest.from_file(os.path.join(PAGES_DIR, page), default_timeout=60)
    app.run()

    assert not app.exception
    rendered = " ".join(str(m.value) for m in app.markdown) + \
               " ".join(str(t.value) for t in app.title)
    assert "Non-Pursuit" in rendered or app.text_input, \
        f"{page} rendered past the gate while locked"


def test_evidence_page_flags_a_hash_mismatch(unlocked):
    """The evidence page's whole purpose: a file that no longer matches
    its recorded digest must be surfaced, not silently shown as fine."""
    from broker_agent_models import EVIDENCE_BEFORE

    db = unlocked
    pid = broker_ledger.create_profile(db, "Ada")
    bid = broker_ledger.add_broker(db, Broker(name="Spokeo"))
    sid = broker_ledger.create_scan(db, pid, bid)
    rid = broker_ledger.create_removal(db, sid, pid, bid)
    broker_ledger.add_evidence(db, rid, EVIDENCE_BEFORE,
                               file_path="/nonexistent/gone.png",
                               file_hash="b" * 64)

    app = _run("4_Evidence.py")
    assert not app.exception
    warnings = " ".join(str(w.value) for w in app.warning)
    assert "no longer match" in warnings


# ---------------------------------------------------------------------------
# The background engine is a singleton
# ---------------------------------------------------------------------------

def test_exactly_one_scheduler_survives_many_reruns(monkeypatch, tmp_path):
    """@st.cache_resource must yield one scheduler per server process.

    Streamlit reruns app.py top to bottom on every widget interaction and
    once per browser session. Without the cache_resource guard, each of
    those would start another BackgroundScheduler daemon pool, and a user
    clicking around for a minute would have a dozen of them racing on the
    same tables. This is the test that catches that guard being removed.
    """
    import agent_scheduler

    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(config, "TRACKER_DB_PATH", db_path)
    monkeypatch.setattr(config, "REVIEW_QUEUE_DB_PATH", str(tmp_path / "review.db"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "0")

    database.lock_vault()
    database.unlock_vault(TEST_PASSWORD, db_path)

    built = []
    real_build = agent_scheduler.build_scheduler

    def counting_build(*args, **kwargs):
        scheduler = real_build(*args, **kwargs)
        built.append(scheduler)
        return scheduler

    monkeypatch.setattr(agent_scheduler, "build_scheduler", counting_build)

    app_path = os.path.join(ROOT, "app.py")
    try:
        for _ in range(5):
            app = AppTest.from_file(app_path, default_timeout=60)
            app.session_state["_vault_unlocked"] = True
            app.run()
            assert not app.exception

        assert len(built) == 1, f"{len(built)} schedulers created across 5 reruns"
    finally:
        for scheduler in built:
            scheduler.stop()
        database.lock_vault()


def test_demo_mode_starts_no_background_engine(monkeypatch, tmp_path):
    """The hosted demo runs on a shared IP with a disposable database.

    Unattended broker sweeps from there are exactly what must not happen,
    and there is nothing durable for them to write to anyway.
    """
    import agent_scheduler

    monkeypatch.setattr(config, "TRACKER_DB_PATH", str(tmp_path / "tracker.db"))
    monkeypatch.setenv("NON_PURSUIT_DEMO_MODE", "1")

    built = []
    monkeypatch.setattr(agent_scheduler, "build_scheduler",
                        lambda *a, **k: built.append(1))

    app = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
    app.run()
    assert not app.exception
    assert built == []
