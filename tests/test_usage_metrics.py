"""
Coverage for the usage counter.

Most of these assert on what the module refuses to do. The reason this
exists instead of an off-the-shelf tracker is that the off-the-shelf ones
retain typed values, so the tests that matter most are the ones proving no
user-supplied string can reach the table.
"""
import sqlite3

import pytest

import usage_metrics as metrics


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "usage.db")


def test_empty_store_reports_no_counts(db_path):
    assert metrics.get_counts(db_path) == {}


def test_record_and_read_back(db_path):
    metrics.record_event(db_path, metrics.LETTER_GENERATED)
    assert metrics.get_counts(db_path)[metrics.LETTER_GENERATED] == 1


def test_repeated_events_accumulate(db_path):
    for _ in range(3):
        metrics.record_event(db_path, metrics.FOOTPRINT_SCAN_RUN)
    assert metrics.get_counts(db_path)[metrics.FOOTPRINT_SCAN_RUN] == 3


def test_events_are_counted_independently(db_path):
    metrics.record_event(db_path, metrics.LETTER_GENERATED)
    metrics.record_event(db_path, metrics.EMAIL_SCAN_RUN, count=4)
    counts = metrics.get_counts(db_path)
    assert counts[metrics.LETTER_GENERATED] == 1
    assert counts[metrics.EMAIL_SCAN_RUN] == 4


def test_unknown_event_is_rejected(db_path):
    """The allowlist is the whole safety mechanism -- if an arbitrary
    string is accepted here, PII can reach the table."""
    with pytest.raises(ValueError):
        metrics.record_event(db_path, "jane@example.com")


def test_rejected_event_writes_nothing(db_path):
    with pytest.raises(ValueError):
        metrics.record_event(db_path, "123 Elm St, Sacramento CA")
    assert metrics.get_counts(db_path) == {}


@pytest.mark.parametrize("pii", [
    "Jordan Vale",
    "jane@example.com",
    "555-0142",
    "CR-2019-004821",
    "",
])
def test_no_pii_shaped_string_is_ever_accepted(db_path, pii):
    with pytest.raises(ValueError):
        metrics.record_event(db_path, pii)


def test_schema_has_no_free_text_column(db_path):
    """Even a future careless caller has nowhere to put a typed value:
    the table holds an event name, an integer, and two dates."""
    metrics.record_event(db_path, metrics.SESSION_STARTED)
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(usage_events)")}
    conn.close()
    assert columns == {"event", "count", "first_seen", "last_seen"}


def test_every_event_has_a_label():
    """summary() would KeyError on an event added to EVENTS but not LABELS."""
    assert set(metrics.EVENTS) == set(metrics.LABELS)


def test_summary_includes_never_fired_events_at_zero(db_path):
    metrics.record_event(db_path, metrics.LETTER_GENERATED)
    rows = dict(metrics.summary(db_path))
    assert rows["Letters generated"] == 1
    assert rows["Email scans"] == 0
    assert len(rows) == len(metrics.EVENTS)


def test_summary_order_follows_events_declaration(db_path):
    labels = [label for label, _ in metrics.summary(db_path)]
    assert labels == [metrics.LABELS[e] for e in metrics.EVENTS]


def test_counts_persist_across_connections(db_path):
    """Sequential connections must see the same file -- the trap that
    :memory: falls into."""
    metrics.record_event(db_path, metrics.SESSION_STARTED)
    metrics.record_event(db_path, metrics.SESSION_STARTED)
    assert metrics.get_counts(db_path)[metrics.SESSION_STARTED] == 2


def test_reset_clears_counters(db_path):
    metrics.record_event(db_path, metrics.LETTER_GENERATED)
    metrics.reset(db_path)
    assert metrics.get_counts(db_path) == {}


def test_first_seen_is_preserved_across_increments(db_path):
    """last_seen moves, first_seen doesn't -- otherwise there's no way to
    say how long a figure has been accumulating."""
    metrics.record_event(db_path, metrics.LETTER_GENERATED)
    conn = sqlite3.connect(db_path)
    first = conn.execute("SELECT first_seen FROM usage_events").fetchone()[0]
    conn.close()

    metrics.record_event(db_path, metrics.LETTER_GENERATED)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT first_seen, count FROM usage_events").fetchone()
    conn.close()
    assert row[0] == first
    assert row[1] == 2


def test_every_defined_event_is_actually_wired_somewhere():
    """A defined-but-unfired event renders as a permanent zero in the
    Usage panel, which reads as "nobody used this feature" rather than
    "this was never instrumented". That distinction is invisible in the
    UI, so it gets caught here instead."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sources = [root / "app.py"] + sorted((root / "components").glob("*.py"))
    wired = "\n".join(p.read_text() for p in sources)

    unwired = [e for e in metrics.EVENTS
               if f"usage_metrics.{e.upper()}" not in wired]
    assert not unwired, f"defined but never recorded: {', '.join(unwired)}"
