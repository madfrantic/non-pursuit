"""
Coverage for the chino merge: models, vault, ledger, evidence chain,
engines, scheduler.

The emphasis is on the invariants the merge introduced that a later change
could quietly break -- the legacy-decrypt fallback, the non-destructive
migration, the tamper check, and the fact that nothing here submits.
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UTILS = os.path.join(ROOT, "utils")
for _p in (ROOT, UTILS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_engines
import agent_scheduler
import broker_ledger
import broker_probe
import config
import database
import optout_engine
import review_queue
from broker_agent_models import (
    EVIDENCE_BEFORE,
    Broker,
    BrokerDifficulty,
    PIIField,
    Profile,
    RemovalStatus,
    ScanStatus,
    coerce_difficulty,
    map_queue_reason,
)


@pytest.fixture
def ledger(tmp_path):
    path = str(tmp_path / "tracker.db")
    broker_ledger.init_ledger(path)
    return path


@pytest.fixture
def review_db(tmp_path):
    return str(tmp_path / "review_queue.db")


@pytest.fixture(autouse=True)
def _locked_vault():
    """Every test starts with no vault unlocked, and leaves none behind."""
    database.lock_vault()
    yield
    database.lock_vault()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TestModels:
    def test_captcha_and_manual_only_never_get_automated(self):
        assert BrokerDifficulty.CAPTCHA.queues_immediately
        assert BrokerDifficulty.MANUAL_ONLY.queues_immediately
        assert not BrokerDifficulty.EASY.queues_immediately
        assert not BrokerDifficulty.STANDARD.queues_immediately
        assert not BrokerDifficulty.HARD.queues_immediately

    def test_difficulty_maps_to_a_real_review_queue_reason(self):
        for difficulty in BrokerDifficulty:
            if difficulty.queues_immediately:
                assert difficulty.queue_reason in review_queue.REASONS

    def test_chino_queue_reasons_map_onto_the_existing_vocabulary(self):
        assert map_queue_reason("captcha") == review_queue.CAPTCHA
        assert map_queue_reason("phone_verification") == review_queue.IDENTITY_VERIFICATION
        assert map_queue_reason("email_verification") == review_queue.IDENTITY_VERIFICATION
        assert map_queue_reason("error") == review_queue.SITE_UNREACHABLE

    def test_other_is_rejected_rather_than_bucketed(self):
        """A catch-all reason is what review_queue's closed vocabulary exists
        to prevent -- it must not be reintroduced through the chino map."""
        with pytest.raises(ValueError):
            map_queue_reason("other")
        with pytest.raises(ValueError):
            map_queue_reason("something_invented")

    def test_unknown_difficulty_falls_back_without_raising(self):
        assert coerce_difficulty("nonsense") is BrokerDifficulty.STANDARD
        assert coerce_difficulty(None) is BrokerDifficulty.STANDARD
        assert coerce_difficulty("CAPTCHA") is BrokerDifficulty.CAPTCHA
        assert coerce_difficulty(BrokerDifficulty.HARD) is BrokerDifficulty.HARD

    def test_profile_flattens_for_the_automators(self):
        profile = Profile(id=1, name="Test", fields=[
            PIIField("first_name", "Ada"), PIIField("last_name", "Lovelace")])
        assert profile.as_target_data() == {"first_name": "Ada", "last_name": "Lovelace"}
        assert profile.get("first_name") == "Ada"
        assert profile.get("absent", "fallback") == "fallback"

    def test_pii_field_labels_itself(self):
        assert PIIField("first_name", "x").label == "First Name"


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

class TestVault:
    def test_salt_is_random_per_install_not_chino_s_hardcoded_one(self, tmp_path):
        """chino used a literal salt in source, so every install shared one.

        Two separate installs must not derive the same key from the same
        password, which is the entire purpose of a salt.
        """
        a = database.load_or_create_salt(str(tmp_path / "a" / "t.db"))
        b = database.load_or_create_salt(str(tmp_path / "b" / "t.db"))
        assert a != b
        assert len(a) == database.SALT_LENGTH
        assert b"data_broker_agent_v1" not in a

    def test_salt_is_stable_across_calls(self, tmp_path):
        db = str(tmp_path / "t.db")
        assert database.load_or_create_salt(db) == database.load_or_create_salt(db)

    def test_same_password_and_salt_derive_the_same_key(self, tmp_path):
        salt = database.load_or_create_salt(str(tmp_path / "t.db"))
        assert database.derive_vault_key("pw", salt) == database.derive_vault_key("pw", salt)
        assert database.derive_vault_key("pw", salt) != database.derive_vault_key("other", salt)

    def test_empty_password_is_refused(self, tmp_path):
        salt = database.load_or_create_salt(str(tmp_path / "t.db"))
        with pytest.raises(ValueError):
            database.derive_vault_key("", salt)

    def test_unlock_then_wrong_password_is_rejected(self, tmp_path):
        db = str(tmp_path / "t.db")
        assert database.unlock_vault("correct-password", db)
        database.lock_vault()
        with pytest.raises(ValueError, match="Incorrect master password"):
            database.unlock_vault("wrong-password", db)

    def test_correct_password_reopens_the_vault(self, tmp_path):
        db = str(tmp_path / "t.db")
        database.unlock_vault("correct-password", db)
        database.lock_vault()
        assert database.unlock_vault("correct-password", db)
        assert database.vault_is_unlocked()

    def test_vault_roundtrips_a_value(self, tmp_path):
        database.unlock_vault("pw12345678", str(tmp_path / "t.db"))
        assert database._decrypt(database._encrypt("Ada Lovelace")) == "Ada Lovelace"

    def test_rows_written_before_the_vault_still_decrypt_after_it(self, tmp_path):
        """The data-loss regression this fallback exists to prevent.

        There is real data in data/tracker.db encrypted under the .env key.
        Introducing the vault must not make any of it unreadable.
        """
        db = str(tmp_path / "t.db")
        database.init_db(db)
        database.insert_target_profile(db, {
            "first_name": "Legacy", "last_name": "Row",
            "email_address": "legacy@example.com"})

        database.unlock_vault("a-new-master-password", db)

        row = database.get_latest_target_profile(db)
        assert row["first_name"] == "Legacy"
        assert row["email_address"] == "legacy@example.com"

    def test_vault_and_legacy_rows_coexist(self, tmp_path):
        db = str(tmp_path / "t.db")
        database.init_db(db)
        database.insert_target_profile(db, {"first_name": "Before", "last_name": "Vault"})
        database.unlock_vault("pw12345678", db)
        database.insert_target_profile(db, {"first_name": "After", "last_name": "Vault"})

        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            names = [database._decrypt(r["first_name"])
                     for r in conn.execute("SELECT first_name FROM target_profile")]
        assert "Before" in names and "After" in names

    def test_hash_for_verification_is_one_way_and_stable(self):
        h = database.VaultManager.hash_for_verification("secret")
        assert h == database.VaultManager.hash_for_verification("secret")
        assert h != database.VaultManager.hash_for_verification("secret ")
        assert "secret" not in h
        assert len(h) == 64

    def test_vault_exists_reports_whether_a_password_was_set(self, tmp_path):
        db = str(tmp_path / "t.db")
        assert not database.vault_exists(db)
        database.unlock_vault("pw12345678", db)
        assert database.vault_exists(db)


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class TestLedgerMigration:
    def test_migration_preserves_existing_phase_1_rows(self, tmp_path):
        """tracker.db holds real broker requests. Adding the agent tables
        must not disturb a single row of what was already there."""
        db = str(tmp_path / "tracker.db")
        database.init_db(db)
        database.insert_target_profile(db, {"first_name": "Keep", "last_name": "Me"})
        import tracker
        tracker.add_request(db, "Spokeo", "Email", 45, notes="phase 1 state")

        before_profiles = len(database.get_latest_target_profile(db) or {})
        broker_ledger.init_ledger(db)

        assert database.get_latest_target_profile(db)["first_name"] == "Keep"
        requests = tracker.get_all_requests(db)
        assert len(requests) == 1
        assert requests[0]["broker_name"] == "Spokeo"
        assert requests[0]["status"] == "Sent"

    def test_init_is_idempotent(self, tmp_path):
        db = str(tmp_path / "t.db")
        for _ in range(3):
            broker_ledger.init_ledger(db)
        pid = broker_ledger.create_profile(db, "Once")
        broker_ledger.init_ledger(db)
        assert len(broker_ledger.get_profiles(db)) == 1
        assert broker_ledger.get_profile(db, pid)["name"] == "Once"

    def test_pragma_migration_adds_missing_columns(self, tmp_path):
        """An older ledger without the added columns must gain them rather
        than being recreated (which would drop its rows)."""
        db = str(tmp_path / "t.db")
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE agent_brokers (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE, url TEXT, opt_out_url TEXT, search_url TEXT, "
            "difficulty TEXT, removal_method TEXT, supports_gdpr INTEGER, "
            "supports_ccpa INTEGER, notes TEXT, handler_module TEXT, "
            "is_active INTEGER DEFAULT 1, last_updated TEXT);"
        )
        conn.execute("INSERT INTO agent_brokers (name) VALUES ('Pre-existing')")
        conn.commit()
        conn.close()

        broker_ledger.init_ledger(db)

        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_brokers)")}
        rows = conn.execute("SELECT name FROM agent_brokers").fetchall()
        conn.close()
        assert "compliance_email" in cols
        assert ("Pre-existing",) in rows


class TestLedgerCrud:
    def test_profile_lifecycle(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        assert broker_ledger.get_profile(ledger, pid)["name"] == "Ada"
        broker_ledger.update_profile(ledger, pid, "Ada L")
        assert broker_ledger.get_profile(ledger, pid)["name"] == "Ada L"
        broker_ledger.delete_profile(ledger, pid)
        assert broker_ledger.get_profile(ledger, pid) is None

    def test_pii_is_encrypted_at_rest(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        broker_ledger.save_pii_field(ledger, pid, "first_name", "Ada")

        conn = sqlite3.connect(ledger)
        stored = conn.execute("SELECT encrypted_value FROM pii_fields").fetchone()[0]
        conn.close()
        assert "Ada" not in stored

        fields = broker_ledger.get_pii_fields(ledger, pid)
        assert fields[0].value == "Ada"

    def test_saving_a_field_twice_replaces_rather_than_duplicates(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        broker_ledger.save_pii_field(ledger, pid, "email", "old@example.com")
        broker_ledger.save_pii_field(ledger, pid, "email", "new@example.com")
        fields = broker_ledger.get_pii_fields(ledger, pid)
        assert len(fields) == 1
        assert fields[0].value == "new@example.com"

    def test_deleting_a_profile_takes_its_pii(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        broker_ledger.save_pii_field(ledger, pid, "first_name", "Ada")
        broker_ledger.delete_profile(ledger, pid)
        assert broker_ledger.get_pii_fields(ledger, pid) == []

    def test_broker_upsert_does_not_duplicate(self, ledger):
        first = broker_ledger.add_broker(ledger, Broker(name="Spokeo", opt_out_url="a"))
        second = broker_ledger.add_broker(ledger, Broker(name="Spokeo", opt_out_url="b"))
        assert first == second
        assert len(broker_ledger.get_brokers(ledger)) == 1
        assert broker_ledger.get_broker(ledger, first)["opt_out_url"] == "b"

    def test_activity_log_records_mutations(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        actions = [e["action"] for e in broker_ledger.get_activity_log(ledger)]
        assert "create_profile" in actions

    def test_activity_log_never_holds_a_field_value(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        broker_ledger.save_pii_field(ledger, pid, "first_name", "SuperSecretName")
        blob = " ".join(str(e) for e in broker_ledger.get_activity_log(ledger))
        assert "SuperSecretName" not in blob
        assert "first_name" in blob

    def test_unknown_scan_column_is_rejected(self, ledger):
        """chino interpolated caller keys into SQL directly."""
        pid = broker_ledger.create_profile(ledger, "Ada")
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        with pytest.raises(ValueError, match="Unknown scan column"):
            broker_ledger.update_scan(ledger, sid, status_or_drop_table="x")

    def test_unknown_removal_column_is_rejected(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)
        with pytest.raises(ValueError, match="Unknown removal column"):
            broker_ledger.update_removal(ledger, rid, bogus="x")

    def test_get_removal_exists(self, ledger):
        """chino's VerificationEngine called a method its DatabaseManager
        never defined, so verification could not have run at all."""
        pid = broker_ledger.create_profile(ledger, "Ada")
        bid = broker_ledger.add_broker(ledger, Broker(name="Spokeo"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)
        removal = broker_ledger.get_removal(ledger, rid)
        assert removal is not None
        assert removal["broker_name"] == "Spokeo"

    def test_retry_respects_max_retries(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)
        broker_ledger.update_removal(ledger, rid, status=RemovalStatus.FAILED.value,
                                     retry_count=3, max_retries=3)
        assert broker_ledger.get_removals_for_retry(ledger) == []
        broker_ledger.update_removal(ledger, rid, retry_count=1)
        assert len(broker_ledger.get_removals_for_retry(ledger)) == 1

    def test_dashboard_stats_counts(self, ledger):
        pid = broker_ledger.create_profile(ledger, "Ada")
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        broker_ledger.update_scan(ledger, sid, found=True,
                                  status=ScanStatus.COMPLETED.value)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)
        broker_ledger.add_evidence(ledger, rid, EVIDENCE_BEFORE, "/x.png", "abc")

        stats = broker_ledger.get_dashboard_stats(ledger)
        assert stats["profiles"] == 1
        assert stats["brokers"] == 1
        assert stats["scans_found"] == 1
        assert stats["removals_pending"] == 1
        assert stats["evidence_captured"] == 1

    def test_queue_for_human_routes_to_review_queue(self, ledger, review_db):
        broker_ledger.queue_for_human(
            review_db, target="Spokeo", task_type="broker_search",
            reason=review_queue.CAPTCHA, note="solve it",
            current_url="https://example.com", screenshot_path="/tmp/a.png")
        items = review_queue.pending_items(review_db)
        assert len(items) == 1
        assert items[0]["target"] == "Spokeo"
        assert items[0]["current_url"] == "https://example.com"
        assert items[0]["screenshot_path"] == "/tmp/a.png"

    def test_no_second_manual_queue_table_was_created(self, ledger):
        """One queue, not two -- see the broker_ledger module docstring."""
        conn = sqlite3.connect(ledger)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "manual_queue" not in tables


# ---------------------------------------------------------------------------
# Evidence chain
# ---------------------------------------------------------------------------

class FakePage:
    """Stands in for a Playwright page."""

    def __init__(self, ok=True, html="<html><body>results</body></html>"):
        self.ok = ok
        self.html = html
        self.frames = []

    def screenshot(self, path, full_page=False):
        if not self.ok:
            raise RuntimeError("browser crashed")
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + b"pixel" * 200)

    def content(self):
        return self.html


class TestEvidenceChain:
    @pytest.fixture(autouse=True)
    def _isolate_evidence_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "EVIDENCE_DIR", str(tmp_path / "evidence"))

    def test_capture_writes_a_file_and_hashes_it(self):
        result = optout_engine.capture_evidence(FakePage(), "Spokeo", EVIDENCE_BEFORE)
        assert os.path.isfile(result["file_path"])
        assert len(result["file_hash"]) == 64
        assert optout_engine.verify_evidence(result["file_path"], result["file_hash"])

    def test_a_modified_file_fails_verification(self):
        """The point of storing the digest: alteration must be detectable."""
        result = optout_engine.capture_evidence(FakePage(), "Spokeo", EVIDENCE_BEFORE)
        with open(result["file_path"], "ab") as handle:
            handle.write(b"tampered")
        assert not optout_engine.verify_evidence(result["file_path"], result["file_hash"])

    def test_a_deleted_file_fails_verification(self):
        result = optout_engine.capture_evidence(FakePage(), "Spokeo", EVIDENCE_BEFORE)
        os.remove(result["file_path"])
        assert not optout_engine.verify_evidence(result["file_path"], result["file_hash"])

    def test_verification_of_an_empty_hash_is_false(self):
        assert not optout_engine.verify_evidence("/nonexistent", "")

    def test_capture_failure_degrades_instead_of_raising(self):
        """A failed screenshot must not fail the removal it documents."""
        result = optout_engine.capture_evidence(FakePage(ok=False), "Spokeo", EVIDENCE_BEFORE)
        assert result["file_hash"] == ""
        assert "error" in result

    def test_unknown_evidence_type_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown evidence type"):
            optout_engine.capture_evidence(FakePage(), "Spokeo", "invented_type")

    def test_captures_do_not_collide(self):
        a = optout_engine.capture_evidence(FakePage(), "Spokeo", EVIDENCE_BEFORE)
        b = optout_engine.capture_evidence(FakePage(), "Spokeo", EVIDENCE_BEFORE)
        assert a["file_path"] != b["file_path"]

    def test_broker_name_is_made_filesystem_safe(self):
        result = optout_engine.capture_evidence(FakePage(), "That's Them / Inc.", EVIDENCE_BEFORE)
        name = os.path.basename(result["file_path"])
        assert "/" not in name and "'" not in name

    def test_hash_of_a_missing_file_is_empty(self):
        assert optout_engine.calculate_file_hash("/no/such/file.png") == ""


class TestCaptchaDetection:
    @pytest.mark.parametrize("marker", [
        "g-recaptcha", "h-captcha", "cf-turnstile", "recaptcha", "captcha"])
    def test_detects_common_bot_checks(self, marker):
        assert optout_engine.detect_captcha(
            FakePage(html=f"<div class='{marker}'></div>"))

    def test_clean_page_is_not_flagged(self):
        assert not optout_engine.detect_captcha(
            FakePage(html="<html><body>search results</body></html>"))

    def test_detection_failure_is_not_a_false_positive(self):
        class Broken:
            frames = []

            def content(self):
                raise RuntimeError("page closed")

        assert not optout_engine.detect_captcha(Broken())


class TestDifficultyTaxonomy:
    def test_known_captcha_brokers_are_flagged(self):
        for name in ("Spokeo", "TruePeopleSearch", "Nuwber"):
            assert optout_engine.difficulty_for(name).queues_immediately

    def test_lookup_is_case_insensitive(self):
        assert optout_engine.difficulty_for("spokeo") is BrokerDifficulty.CAPTCHA
        assert optout_engine.difficulty_for("  SPOKEO  ") is BrokerDifficulty.CAPTCHA

    def test_unknown_broker_defaults_to_standard(self):
        assert optout_engine.difficulty_for("Brand New Broker") is BrokerDifficulty.STANDARD


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def _seed_profile(ledger_path, name="Ada Lovelace"):
    pid = broker_ledger.create_profile(ledger_path, name)
    first, _, last = name.partition(" ")
    broker_ledger.save_pii_field(ledger_path, pid, "first_name", first)
    broker_ledger.save_pii_field(ledger_path, pid, "last_name", last)
    broker_ledger.save_pii_field(ledger_path, pid, "state", "CA")
    return pid


class TestScannerEngine:
    def test_captcha_broker_is_queued_and_never_probed(self, ledger, review_db, monkeypatch):
        """Automation must not open a CAPTCHA-gated page at all."""
        called = []
        monkeypatch.setattr(broker_probe, "probe_brokers_sync",
                            lambda *a, **k: called.append(1) or [])
        monkeypatch.setattr(agent_engines, "SCAN_DELAY_SECONDS", 0)

        pid = _seed_profile(ledger)
        broker_ledger.add_broker(ledger, Broker(
            name="Spokeo", difficulty=BrokerDifficulty.CAPTCHA,
            search_url="https://spokeo.example/search"))

        results = agent_engines.ScannerEngine(ledger, review_db).scan_profile(pid)

        assert called == []
        assert results[0]["status"] == "queued"
        queued = review_queue.pending_items(review_db)
        assert queued[0]["reason"] == review_queue.CAPTCHA

    def test_found_record_is_written_to_the_scan(self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(broker_probe, "probe_brokers_sync", lambda *a, **k: [{
            "verdict": broker_probe.RECORD_FOUND,
            "record_urls": ["https://broker.example/record/1"],
            "search_url": "https://broker.example/search", "reason": ""}])
        monkeypatch.setattr(agent_engines, "SCAN_DELAY_SECONDS", 0)

        pid = _seed_profile(ledger)
        broker_ledger.add_broker(ledger, Broker(
            name="WhitePages", difficulty=BrokerDifficulty.STANDARD,
            search_url="https://broker.example/search"))

        agent_engines.ScannerEngine(ledger, review_db).scan_profile(pid)

        scans = broker_ledger.get_scans(ledger, profile_id=pid)
        assert scans[0]["found"] == 1
        assert scans[0]["status"] == ScanStatus.COMPLETED.value
        assert scans[0]["result_url"] == "https://broker.example/record/1"

    def test_manual_check_verdict_queues_a_human(self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(broker_probe, "probe_brokers_sync", lambda *a, **k: [{
            "verdict": broker_probe.MANUAL_CHECK, "record_urls": [],
            "search_url": "https://b.example", "reason": "blocked by Cloudflare"}])
        monkeypatch.setattr(agent_engines, "SCAN_DELAY_SECONDS", 0)

        pid = _seed_profile(ledger)
        broker_ledger.add_broker(ledger, Broker(name="Nuwber2",
                                                difficulty=BrokerDifficulty.STANDARD))
        agent_engines.ScannerEngine(ledger, review_db).scan_profile(pid)
        assert review_queue.pending_items(review_db)

    def test_one_broker_failing_does_not_end_the_sweep(self, ledger, review_db, monkeypatch):
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("network died")
            return [{"verdict": broker_probe.NO_RECORD, "record_urls": [],
                     "search_url": "", "reason": ""}]

        monkeypatch.setattr(broker_probe, "probe_brokers_sync", flaky)
        monkeypatch.setattr(agent_engines, "SCAN_DELAY_SECONDS", 0)

        pid = _seed_profile(ledger)
        broker_ledger.add_broker(ledger, Broker(name="AAA"))
        broker_ledger.add_broker(ledger, Broker(name="BBB"))

        results = agent_engines.ScannerEngine(ledger, review_db).scan_profile(pid)
        assert len(results) == 2
        assert {r["status"] for r in results} == {"failed", ScanStatus.COMPLETED.value}

    def test_profile_without_pii_is_refused(self, ledger, review_db):
        pid = broker_ledger.create_profile(ledger, "Empty")
        assert agent_engines.ScannerEngine(ledger, review_db).scan_profile(pid) == []

    def test_unknown_profile_is_refused(self, ledger, review_db):
        assert agent_engines.ScannerEngine(ledger, review_db).scan_profile(9999) == []

    def test_monitoring_scan_only_revisits_brokers_that_had_a_record(
            self, ledger, review_db, monkeypatch):
        probed = []

        def record(subject, brokers=None, **kwargs):
            probed.append(brokers[0]["broker"])
            return [{"verdict": broker_probe.RECORD_FOUND,
                     "record_urls": [], "search_url": "", "reason": ""}]

        monkeypatch.setattr(broker_probe, "probe_brokers_sync", record)
        monkeypatch.setattr(agent_engines, "SCAN_DELAY_SECONDS", 0)

        pid = _seed_profile(ledger)
        hit = broker_ledger.add_broker(ledger, Broker(name="HasRecord"))
        broker_ledger.add_broker(ledger, Broker(name="Clean"))
        sid = broker_ledger.create_scan(ledger, pid, hit)
        broker_ledger.update_scan(ledger, sid, found=True,
                                  status=ScanStatus.COMPLETED.value)

        probed.clear()
        agent_engines.ScannerEngine(ledger, review_db).run_monitoring_scan(pid)
        assert probed == ["HasRecord"]

    def test_monitoring_scan_with_no_prior_findings_is_a_noop(self, ledger, review_db):
        pid = _seed_profile(ledger)
        assert agent_engines.ScannerEngine(ledger, review_db).run_monitoring_scan(pid) == []

    def test_progress_callback_errors_do_not_kill_the_scan(
            self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(broker_probe, "probe_brokers_sync", lambda *a, **k: [
            {"verdict": broker_probe.NO_RECORD, "record_urls": [],
             "search_url": "", "reason": ""}])
        monkeypatch.setattr(agent_engines, "SCAN_DELAY_SECONDS", 0)

        pid = _seed_profile(ledger)
        broker_ledger.add_broker(ledger, Broker(name="AAA"))
        scanner = agent_engines.ScannerEngine(ledger, review_db)
        scanner.set_progress_callback(lambda *a: 1 / 0)
        assert len(scanner.scan_profile(pid)) == 1


class TestRemovalPlanner:
    def test_removals_open_only_for_found_records(self, ledger, review_db):
        pid = _seed_profile(ledger)
        found_broker = broker_ledger.add_broker(ledger, Broker(name="Found"))
        clean_broker = broker_ledger.add_broker(ledger, Broker(name="Clean"))

        sid = broker_ledger.create_scan(ledger, pid, found_broker)
        broker_ledger.update_scan(ledger, sid, found=True)
        clean_sid = broker_ledger.create_scan(ledger, pid, clean_broker)
        broker_ledger.update_scan(ledger, clean_sid, found=False)

        created = agent_engines.RemovalPlanner(ledger, review_db).prepare_removals(pid)
        assert len(created) == 1
        assert created[0]["broker"] == "Found"

    def test_nothing_is_ever_marked_submitted(self, ledger, review_db):
        """The safety invariant: automation opens removals, never submits them."""
        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="Found"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        broker_ledger.update_scan(ledger, sid, found=True)

        agent_engines.RemovalPlanner(ledger, review_db).prepare_removals(pid)

        for removal in broker_ledger.get_removals(ledger, profile_id=pid):
            assert removal["status"] == RemovalStatus.REQUIRES_MANUAL.value
            assert removal["submitted_at"] is None

    def test_a_human_task_is_queued_for_each_removal(self, ledger, review_db):
        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="Found"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        broker_ledger.update_scan(ledger, sid, found=True)

        agent_engines.RemovalPlanner(ledger, review_db).prepare_removals(pid)
        items = review_queue.pending_items(review_db)
        assert any(i["reason"] == review_queue.SUBMIT_REQUIRES_SIGNOFF for i in items)

    def test_preparing_twice_does_not_duplicate(self, ledger, review_db):
        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="Found"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        broker_ledger.update_scan(ledger, sid, found=True)

        planner = agent_engines.RemovalPlanner(ledger, review_db)
        planner.prepare_removals(pid)
        assert planner.prepare_removals(pid) == []
        assert len(broker_ledger.get_removals(ledger, profile_id=pid)) == 1


class TestVerificationEngine:
    def test_no_record_confirms_the_removal(self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(broker_probe, "probe_brokers_sync", lambda *a, **k: [
            {"verdict": broker_probe.NO_RECORD, "record_urls": [],
             "search_url": "", "reason": ""}])

        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)

        assert agent_engines.VerificationEngine(ledger, review_db).verify_removal(rid) is True
        assert broker_ledger.get_removal(ledger, rid)["status"] == RemovalStatus.CONFIRMED.value

    def test_still_listed_returns_false(self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(broker_probe, "probe_brokers_sync", lambda *a, **k: [
            {"verdict": broker_probe.RECORD_FOUND, "record_urls": [],
             "search_url": "", "reason": ""}])

        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)

        assert agent_engines.VerificationEngine(ledger, review_db).verify_removal(rid) is False
        assert broker_ledger.get_removal(ledger, rid)["status"] != RemovalStatus.CONFIRMED.value

    def test_a_blocked_check_is_unknown_not_still_listed(
            self, ledger, review_db, monkeypatch):
        """Recording 'still listed' for a broker that refused to answer would
        manufacture evidence of non-compliance out of a failed request."""
        monkeypatch.setattr(broker_probe, "probe_brokers_sync", lambda *a, **k: [
            {"verdict": broker_probe.PROBE_ERROR, "record_urls": [],
             "search_url": "", "reason": "timeout"}])

        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)

        assert agent_engines.VerificationEngine(ledger, review_db).verify_removal(rid) is None

    def test_probe_exception_is_unknown_not_false(self, ledger, review_db, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("network died")

        monkeypatch.setattr(broker_probe, "probe_brokers_sync", boom)
        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)

        assert agent_engines.VerificationEngine(ledger, review_db).verify_removal(rid) is None

    def test_captcha_broker_verification_queues_a_human(self, ledger, review_db):
        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(
            name="Spokeo", difficulty=BrokerDifficulty.CAPTCHA))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)

        assert agent_engines.VerificationEngine(ledger, review_db).verify_removal(rid) is None
        assert review_queue.pending_items(review_db)

    def test_unknown_removal_is_none(self, ledger, review_db):
        assert agent_engines.VerificationEngine(ledger, review_db).verify_removal(9999) is None

    def test_confirmation_records_evidence(self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(broker_probe, "probe_brokers_sync", lambda *a, **k: [
            {"verdict": broker_probe.NO_RECORD, "record_urls": [],
             "search_url": "", "reason": ""}])

        pid = _seed_profile(ledger)
        bid = broker_ledger.add_broker(ledger, Broker(name="B"))
        sid = broker_ledger.create_scan(ledger, pid, bid)
        rid = broker_ledger.create_removal(ledger, sid, pid, bid)
        agent_engines.VerificationEngine(ledger, review_db).verify_removal(rid)

        assert broker_ledger.get_evidence(ledger, rid)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class TestAgentScheduler:
    def test_starts_and_stops(self, ledger, review_db):
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            assert scheduler.start()
            assert scheduler.is_running
        finally:
            scheduler.stop()
        assert not scheduler.is_running

    def test_start_is_idempotent(self, ledger, review_db):
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            assert scheduler.start()
            assert scheduler.start()
        finally:
            scheduler.stop()

    def test_jobs_register_for_both_kinds(self, ledger, review_db):
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            scheduler.start()
            scheduler.add_monitoring_job(1)
            scheduler.add_verification_job(1)
            ids = {j["id"] for j in scheduler.get_jobs()}
            assert ids == {"monitoring_profile_1", "verification_profile_1"}
        finally:
            scheduler.stop()

    def test_re_adding_a_job_replaces_rather_than_duplicates(self, ledger, review_db):
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            scheduler.start()
            scheduler.add_monitoring_job(1)
            scheduler.add_monitoring_job(1)
            assert len(scheduler.get_jobs()) == 1
        finally:
            scheduler.stop()

    def test_jobs_are_recorded_in_the_ledger(self, ledger, review_db):
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            scheduler.start()
            scheduler.add_monitoring_job(7)
            tasks = broker_ledger.get_scheduled_tasks(ledger)
            assert tasks[0]["task_type"] == agent_scheduler.MONITORING_JOB
            assert tasks[0]["profile_id"] == 7
        finally:
            scheduler.stop()

    def test_removing_an_absent_job_is_not_an_error(self, ledger, review_db):
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            scheduler.start()
            scheduler.remove_job("does_not_exist")
        finally:
            scheduler.stop()

    def test_adding_a_job_without_starting_is_refused(self, ledger, review_db):
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        assert scheduler.add_monitoring_job(1) is None

    def test_jobs_run_with_one_instance_and_coalesce(self, ledger, review_db):
        """An overrunning scan must not stack a second copy on itself."""
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            scheduler.start()
            scheduler.add_monitoring_job(1)
            job = scheduler._scheduler.get_job("monitoring_profile_1")
            assert job.max_instances == 1
            assert job.coalesce is True
        finally:
            scheduler.stop()

    def test_a_failing_job_body_does_not_propagate(self, ledger, review_db, monkeypatch):
        """An uncaught exception inside APScheduler ends that job's future
        runs, so one network blip would silently stop monitoring for good."""
        def boom(*args, **kwargs):
            raise RuntimeError("scan exploded")

        monkeypatch.setattr(agent_engines.ScannerEngine, "run_monitoring_scan", boom)
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        scheduler._run_monitoring_scan(1)  # must not raise

    def test_build_scheduler_respects_the_config_switch(self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(config, "AGENT_SCHEDULER_ENABLED", False)
        scheduler = agent_scheduler.build_scheduler(ledger, review_db)
        try:
            assert not scheduler.is_running
        finally:
            scheduler.stop()

    def test_build_scheduler_enrols_existing_profiles(self, ledger, review_db, monkeypatch):
        monkeypatch.setattr(config, "AGENT_SCHEDULER_ENABLED", True)
        broker_ledger.create_profile(ledger, "Ada")
        scheduler = agent_scheduler.build_scheduler(ledger, review_db)
        try:
            assert len(scheduler.get_jobs()) == 2
        finally:
            scheduler.stop()

    def test_the_scheduler_has_no_submission_job(self, ledger, review_db):
        """CLAUDE.md's automation gate: unattended code scans and verifies,
        it does not assert a legal demand in the user's name."""
        scheduler = agent_scheduler.AgentScheduler(ledger, review_db)
        try:
            scheduler.start()
            scheduler.add_monitoring_job(1)
            scheduler.add_verification_job(1)
            for job in scheduler.get_jobs():
                assert "submit" not in job["id"].lower()
            assert not hasattr(scheduler, "add_submission_job")
        finally:
            scheduler.stop()
