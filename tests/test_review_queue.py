"""
Coverage for the human-review queue.

The queue exists because automation here deliberately stops short, so the
failure that matters most is an item being marked done when it wasn't. The
scaffold this was ported from had exactly that bug -- it resolved items by
their position in the pending list while indexing the full list -- so the
id-vs-position behaviour is pinned first and hardest.
"""
import sqlite3
import threading

import pytest

import review_queue as rq


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "review_queue.db")


def _seed(db, *targets, reason=rq.CAPTCHA):
    return [rq.add_item(db, target, "optout", reason) for target in targets]


# --- the ported bug --------------------------------------------------------

def test_resolving_one_item_does_not_shift_the_others(db):
    """The scaffold resolved by list position and closed the wrong task."""
    first, second, third = _seed(db, "spokeo", "radaris", "intelius")

    rq.resolve_item(db, first)
    # A caller holding the *pending* list now sees radaris at position 0.
    # Resolving by its id must close radaris, not intelius.
    still_pending = rq.pending_items(db)
    assert [item["target"] for item in still_pending] == ["radaris", "intelius"]

    rq.resolve_item(db, still_pending[0]["id"])
    assert [item["target"] for item in rq.pending_items(db)] == ["intelius"]
    assert second != third


def test_every_pending_row_exposes_a_stable_id(db):
    ids = _seed(db, "spokeo", "radaris")
    assert [item["id"] for item in rq.pending_items(db)] == ids


def test_resolving_an_unknown_id_raises(db):
    with pytest.raises(ValueError, match="No review item"):
        rq.resolve_item(db, 9999)


def test_resolving_twice_is_a_no_op_and_keeps_the_first_timestamp(db):
    item_id = rq.add_item(db, "spokeo", "optout", rq.CAPTCHA)
    assert rq.resolve_item(db, item_id) is True
    first_stamp = rq.all_items(db)[0]["resolved_at"]
    assert rq.resolve_item(db, item_id) is False
    assert rq.all_items(db)[0]["resolved_at"] == first_stamp


# --- the closed vocabulary -------------------------------------------------

def test_an_unknown_reason_is_rejected(db):
    with pytest.raises(ValueError, match="Unknown reason"):
        rq.add_item(db, "spokeo", "optout", "because I said so")


def test_an_unknown_outcome_is_rejected(db):
    item_id = rq.add_item(db, "spokeo", "optout", rq.CAPTCHA)
    with pytest.raises(ValueError, match="Unknown outcome"):
        rq.resolve_item(db, item_id, outcome="mostly done")


def test_a_targetless_item_is_rejected(db):
    with pytest.raises(ValueError, match="target is required"):
        rq.add_item(db, "   ", "optout", rq.CAPTCHA)


def test_every_reason_carries_guidance():
    assert set(rq.REASONS) == set(rq.PRIORITY)
    assert all(text.strip() for text in rq.REASONS.values())


def test_a_long_note_is_truncated_rather_than_stored_whole(db):
    rq.add_item(db, "spokeo", "optout", rq.CAPTCHA, note="x" * 5000)
    assert len(rq.all_items(db)[0]["note"]) == rq.MAX_NOTE_CHARS


# --- ordering --------------------------------------------------------------

def test_filled_forms_awaiting_signoff_sort_above_everything(db):
    rq.add_item(db, "radaris", "optout", rq.NO_AUTOMATOR)
    rq.add_item(db, "spokeo", "optout", rq.SUBMIT_REQUIRES_SIGNOFF)
    assert rq.pending_items(db)[0]["target"] == "spokeo"


def test_equal_priority_keeps_oldest_first(db):
    _seed(db, "a", "b", "c")
    assert [item["target"] for item in rq.pending_items(db)] == ["a", "b", "c"]


# --- stats -----------------------------------------------------------------

def test_stats_count_by_status_and_name_the_blockers(db):
    first, second = _seed(db, "spokeo", "radaris")
    rq.add_item(db, "intelius", "optout", rq.NO_AUTOMATOR)
    rq.resolve_item(db, first)
    rq.resolve_item(db, second, outcome=rq.SKIPPED)

    result = rq.stats(db)
    assert result["total"] == 3
    assert result[rq.PENDING] == 1
    assert result[rq.RESOLVED] == 1
    assert result[rq.SKIPPED] == 1
    assert result["blocking"] == {rq.NO_AUTOMATOR: 1}


def test_stats_on_an_empty_queue_are_zeroed_not_missing(db):
    assert rq.stats(db)["total"] == 0
    assert rq.stats(db)["blocking"] == {}


def test_clear_closed_keeps_pending_work(db):
    first, _ = _seed(db, "spokeo", "radaris")
    rq.resolve_item(db, first)
    assert rq.clear_closed(db) == 1
    assert [item["target"] for item in rq.pending_items(db)] == ["radaris"]


# --- persistence and threads ----------------------------------------------

def test_the_queue_survives_a_restart(db):
    rq.add_item(db, "spokeo", "optout", rq.SUBMIT_REQUIRES_SIGNOFF)
    # A fresh call opens the file again -- nothing is held in memory.
    assert [item["target"] for item in rq.pending_items(db)] == ["spokeo"]


def test_the_queue_is_readable_from_a_worker_thread(db):
    """The scaffold shared one connection and sqlite3 refused cross-thread use."""
    rq.add_item(db, "spokeo", "optout", rq.CAPTCHA)
    box = {}

    def worker():
        try:
            box["items"] = rq.pending_items(db)
        except sqlite3.ProgrammingError as exc:  # what the scaffold hit
            box["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert "error" not in box
    assert [item["target"] for item in box["items"]] == ["spokeo"]
