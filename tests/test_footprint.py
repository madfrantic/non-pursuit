"""
Offline coverage for the Digital Footprint Matrix.

The load-bearing claim of this component is that it makes no network
request, so that is tested structurally rather than by observation. The
conftest socket block would catch an unmocked call at runtime, but only
on a code path a test happens to walk -- test_module_imports_no_network_client
parses the module's own AST instead, which catches a network import that
nothing exercises yet. Both matter: the AST test proves the capability is
absent, the socket test proves the live path doesn't need it.

Determinism is the other property with teeth. The matrix is meant to be
rehearsable -- the same identifier produces the same grid on every
machine and every run -- which rules out both randomness and Python's
per-process-salted hash().
"""
import ast
import os
import socket

import pytest

import footprint_matrix as matrix

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAP_PATH = os.path.join(ROOT, "data", "footprint_map.json")
MODULE_PATH = os.path.join(ROOT, "utils", "footprint_matrix.py")

# Anything that can open a connection. If one of these ever appears in
# the module, the offline guarantee is gone.
NETWORK_MODULES = {
    "requests", "aiohttp", "httpx", "urllib", "urllib3", "urllib.request",
    "http", "http.client", "socket", "ftplib", "telnetlib", "smtplib",
    "asyncio", "holehe", "websockets", "paramiko",
}


@pytest.fixture
def footprint_map():
    return matrix.load_map(MAP_PATH)


# --- the offline guarantee -------------------------------------------

def test_module_imports_no_network_client():
    """No network-capable module is imported, at any depth, anywhere in
    utils/footprint_matrix.py."""
    tree = ast.parse(open(MODULE_PATH, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    offenders = {name for name in imported
                 if name in NETWORK_MODULES or name.split(".")[0] in NETWORK_MODULES}
    assert offenders == set(), f"footprint_matrix imported network module(s): {offenders}"


def test_scan_runs_with_sockets_hard_blocked(footprint_map):
    """The conftest fixture already blocks AF_INET; this asserts the scan
    completes anyway, i.e. the lookup never reaches for one."""
    with pytest.raises(RuntimeError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    assert len(results) == len(footprint_map["platforms"])


def test_no_live_probers_registered_by_default():
    """A default install must be offline-only. If a live prober (a holehe
    adapter, say) is ever registered at import time, this fails loudly."""
    assert matrix.available_probers() == []


# --- dataset integrity ------------------------------------------------

def test_dataset_has_fifteen_unique_platforms(footprint_map):
    platforms = footprint_map["platforms"]
    assert len(platforms) == 15
    assert len({p["key"] for p in platforms}) == 15


def test_every_platform_declares_required_fields(footprint_map):
    for platform in footprint_map["platforms"]:
        for field in ("key", "name", "domain", "category", "profile_url",
                      "accepts", "holehe_module", "hit_rate", "recovery"):
            assert field in platform, f"{platform.get('key')} missing {field}"
        assert 0.0 <= platform["hit_rate"] <= 1.0
        assert set(platform["accepts"]) <= {"handle", "email"}


def test_seeded_identifiers_reference_real_platform_keys(footprint_map):
    """A typo in a seed list would silently produce a demo that misses the
    platform it was meant to light up."""
    keys = {p["key"] for p in footprint_map["platforms"]}
    for identifier, seed in footprint_map["seeded_identifiers"].items():
        unknown = set(seed["found"]) - keys
        assert unknown == set(), f"{identifier} seeds unknown platform(s): {unknown}"


# --- identifier handling ----------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("jane.doe@example.com", matrix.IDENTIFIER_EMAIL),
    ("a.b+tag@sub.example.co.uk", matrix.IDENTIFIER_EMAIL),
    ("nonpursuit", matrix.IDENTIFIER_HANDLE),
    ("@nonpursuit", matrix.IDENTIFIER_HANDLE),
    ("not@an", matrix.IDENTIFIER_HANDLE),
])
def test_classify_identifier(value, expected):
    assert matrix.classify_identifier(matrix.normalize_identifier(value)) == expected


@pytest.mark.parametrize("raw", ["  @NonPursuit ", "nonpursuit", "NONPURSUIT", "@nonpursuit"])
def test_normalisation_collapses_handle_variants(raw):
    assert matrix.normalize_identifier(raw) == "nonpursuit"


def test_handle_variants_produce_identical_matrix(footprint_map):
    """Normalisation must happen before hashing, or '@X' and 'x' become
    two rows for one account."""
    a = matrix.scan("@NonPursuit", footprint_map=footprint_map)
    b = matrix.scan("nonpursuit", footprint_map=footprint_map)
    assert [r["verdict"] for r in a] == [r["verdict"] for r in b]


def test_empty_identifier_returns_empty(footprint_map):
    for value in ("", "   ", None):
        assert matrix.scan(value, footprint_map=footprint_map) == []


# --- mock lookup logic ------------------------------------------------

def test_seeded_identifier_matches_its_seed_list_exactly(footprint_map):
    seed = set(footprint_map["seeded_identifiers"]["jane.doe@example.com"]["found"])
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    found = {r["platform_key"] for r in results if r["verdict"] == matrix.FOUND}
    assert found == seed


def test_seeded_empty_list_yields_no_hits(footprint_map):
    results = matrix.scan("demo@example.com", footprint_map=footprint_map)
    assert matrix.hits(results) == []
    assert matrix.summarize(results)[matrix.FOUND] == 0


def test_lookup_is_deterministic_across_calls(footprint_map):
    first = matrix.scan("unseeded-user-42", footprint_map=footprint_map)
    second = matrix.scan("unseeded-user-42", footprint_map=footprint_map)
    assert [r["verdict"] for r in first] == [r["verdict"] for r in second]


def test_bucket_is_stable_and_bounded():
    """Bounded, stable within a run, and platform-sensitive. Not a pinned
    literal -- test_lookup_is_deterministic_across_calls covers run-to-run
    stability, which is the property the demo actually depends on."""
    value = matrix._bucket("nonpursuit", "github")
    assert 0.0 <= value < 1.0
    assert value == matrix._bucket("nonpursuit", "github")
    assert value != matrix._bucket("nonpursuit", "reddit")


def test_email_only_platform_is_unsupported_for_a_handle(footprint_map):
    """Adobe accepts email only. A handle must read N/A, not 'not found'
    -- no lookup was possible."""
    results = matrix.scan("nonpursuit", footprint_map=footprint_map)
    adobe = next(r for r in results if r["platform_key"] == "adobe")
    assert adobe["verdict"] == matrix.UNSUPPORTED
    assert adobe["exists"] is False


def test_handle_only_platform_is_unsupported_for_an_email(footprint_map):
    results = matrix.scan("someone@example.com", footprint_map=footprint_map)
    for key in ("reddit", "mastodon"):
        row = next(r for r in results if r["platform_key"] == key)
        assert row["verdict"] == matrix.UNSUPPORTED


def test_profile_url_only_populated_on_a_hit(footprint_map):
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    for row in results:
        if row["verdict"] == matrix.FOUND:
            assert row["profile_url"] and "{account}" not in row["profile_url"]
        else:
            assert row["profile_url"] == ""


def test_every_row_is_flagged_simulated(footprint_map):
    """The one property that must never regress: nothing from this module
    may present as a real finding."""
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    assert results and all(r["simulated"] is True for r in results)
    assert all(r["source"] == matrix.SOURCE_OFFLINE for r in results)


def test_summarize_counts_cover_every_row(footprint_map):
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    assert sum(matrix.summarize(results).values()) == len(results)


# --- holehe schema compatibility --------------------------------------

def test_results_expose_holehe_result_keys(footprint_map):
    """holehe's per-module dict shape, so both sources deserialise the
    same and can share the footprint_results table."""
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    for row in results:
        for key in ("exists", "emailrecovery", "phoneNumber", "others", "rateLimit"):
            assert key in row
        assert isinstance(row["exists"], bool)
        assert isinstance(row["rateLimit"], bool)


def test_recovery_fields_are_null_on_a_miss(footprint_map):
    """holehe returns None for these on exists=False; a miss that carried
    a recovery hint would be incoherent."""
    results = matrix.scan("demo@example.com", footprint_map=footprint_map)
    for row in results:
        assert row["emailrecovery"] is None
        assert row["phoneNumber"] is None
        assert row["others"] is None


def test_holehe_module_names_map_to_known_platforms(footprint_map):
    mapped = {p["key"]: p["holehe_module"] for p in footprint_map["platforms"]}
    assert mapped["github"] == "github"
    assert mapped["x"] == "twitter"
    assert mapped["reddit"] is None


# --- persistence ------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "tracker.db")


def test_save_persists_only_hits_by_default(db, footprint_map):
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    written = matrix.save_results(db, results)
    assert written == len(matrix.hits(results))
    assert len(matrix.get_results(db)) == written


def test_save_can_include_misses_when_asked(db, footprint_map):
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    assert matrix.save_results(db, results, include_misses=True) == len(results)


def test_rescanning_upserts_rather_than_duplicating(db, footprint_map):
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    matrix.save_results(db, results)
    matrix.save_results(db, results)
    stored = matrix.get_results(db)
    assert len(stored) == len(matrix.hits(results))
    assert len({(r["identifier"], r["platform_key"]) for r in stored}) == len(stored)


def test_stored_row_round_trips_holehe_fields(db, footprint_map):
    results = matrix.scan("jane.doe@example.com", footprint_map=footprint_map)
    matrix.save_results(db, results)
    row = next(r for r in matrix.get_results(db) if r["platform_key"] == "x")
    assert row["exists_flag"] == 1
    assert row["holehe_module"] == "twitter"
    assert row["email_recovery"]
    assert row["rate_limit"] == 1
    assert row["simulated"] == 1
    assert row["source"] == matrix.SOURCE_OFFLINE


def test_get_results_scopes_to_one_identifier(db, footprint_map):
    matrix.save_results(db, matrix.scan("jane.doe@example.com", footprint_map=footprint_map))
    matrix.save_results(db, matrix.scan("nonpursuit", footprint_map=footprint_map))
    scoped = matrix.get_results(db, "@NonPursuit")
    assert scoped and all(r["identifier"] == "nonpursuit" for r in scoped)


def test_delete_removes_one_identifier_only(db, footprint_map):
    matrix.save_results(db, matrix.scan("jane.doe@example.com", footprint_map=footprint_map))
    matrix.save_results(db, matrix.scan("nonpursuit", footprint_map=footprint_map))
    removed = matrix.delete_results(db, "nonpursuit")
    assert removed > 0
    assert matrix.get_results(db, "nonpursuit") == []
    assert matrix.get_results(db, "jane.doe@example.com") != []


def test_saving_nothing_is_a_noop(db):
    assert matrix.save_results(db, []) == 0


def test_init_db_creates_the_table(db):
    matrix.init_db(db)
    assert matrix.get_results(db) == []


def test_table_lives_alongside_the_other_stores(db, footprint_map):
    """footprint_results must be a table in the shared tracker.db, not a
    separate database file -- runtime_mode.db_path() hands every store the
    same path."""
    import sqlite3
    import discovered_accounts

    discovered_accounts.init_db(db)
    matrix.save_results(db, matrix.scan("jane.doe@example.com", footprint_map=footprint_map))
    with sqlite3.connect(db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"footprint_results", "discovered_accounts"} <= names


# --- worklist handoff -------------------------------------------------

def test_discovered_rows_never_claim_confirmed(footprint_map):
    """Simulated hits must not be promotable to CONFIRMED -- that is what
    would let a mock finding reach the audit package as established
    fact."""
    rows = matrix.to_discovered_rows(
        matrix.scan("jane.doe@example.com", footprint_map=footprint_map))
    assert rows
    assert all(row["confidence"] == "POSSIBLE" for row in rows)
    assert all("simulated" in row["reason"] for row in rows)


def test_discovered_rows_fit_the_discovered_accounts_schema(db, footprint_map):
    """The handoff payload has to actually insert, not just look right."""
    import discovered_accounts

    rows = matrix.to_discovered_rows(
        matrix.scan("jane.doe@example.com", footprint_map=footprint_map))
    assert discovered_accounts.save_discoveries(db, rows) == len(rows)
    assert len(discovered_accounts.get_all(db)) == len(rows)


def test_every_seeded_platform_is_reachable_for_its_identifier_type(footprint_map):
    """A seed naming a platform that can't accept that identifier type is
    dead config: the UNSUPPORTED rule outranks the seed, so the platform
    silently never lights up and the demo quietly under-reports. Caught
    this exact mistake once (reddit is handle-only, seeded under an
    email), so it is asserted for every seed rather than one example.
    """
    accepts = {p["key"]: p["accepts"] for p in footprint_map["platforms"]}
    for identifier, seed in footprint_map["seeded_identifiers"].items():
        kind = matrix.classify_identifier(matrix.normalize_identifier(identifier))
        unreachable = [key for key in seed["found"] if kind not in accepts[key]]
        assert unreachable == [], (
            f"{identifier} ({kind}) seeds unreachable platform(s): {unreachable}"
        )
