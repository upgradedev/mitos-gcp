"""The Firestore snapshot handler, without a Firestore.

`_on_snapshot` is where the real decisions happen: turning documents back into
entries, ignoring what does not parse, judging expiry against today, and never
waking twice for the same deferral. The subscription itself cannot be reasoned
about and is run for real against the emulator; this covers everything else.
"""

from __future__ import annotations

from mitos.ledger import Entry
from mitos.watcher import DEFERRAL_KIND, FirestoreWatcher

TODAY = "2026-08-19"


class _Doc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


def _watcher(today=TODAY):
    return FirestoreWatcher(collection="c", today=lambda: today, client=object())


def _doc(expires="2026-08-12", entry_id="d1", **extra):
    e = Entry(
        kind=DEFERRAL_KIND,
        actor="compliance-companion",
        subject="services/customer",
        payload={"finding": "f", "expires_on": expires, **extra},
        entry_id=entry_id,
    )
    return _Doc(e.to_doc())


def test_an_expired_deferral_in_a_snapshot_wakes_the_fleet():
    w = _watcher()
    got = []
    w.start = lambda *a, **k: None  # never open a real subscription here
    w._on_wake = got.append
    w._on_snapshot([_doc()], [], None)

    assert len(got) == 1 and len(got[0]) == 1
    assert len(w.wakeups) == 1
    assert "Firestore delivered a snapshot" in w.wakeups[0].reason


def test_a_live_deferral_in_a_snapshot_does_not():
    w = _watcher()
    got = []
    w._on_wake = got.append
    w._on_snapshot([_doc(expires="2026-12-01")], [], None)
    assert got == [] and w.wakeups == []


def test_the_digest_field_does_not_break_reconstruction():
    """`to_doc` adds `digest`, which is not a constructor argument. If this
    regressed, every snapshot would silently contain zero entries and the fleet
    would never wake."""
    w = _watcher()
    got = []
    w._on_wake = got.append
    doc = _doc()
    assert "digest" in doc.to_dict()
    w._on_snapshot([doc], [], None)
    assert len(got) == 1


def test_a_document_that_is_not_an_entry_is_skipped_not_fatal():
    """A subscription delivers whatever is in the collection. One malformed
    document must not stop the fleet waking for the others."""
    w = _watcher()
    got = []
    w._on_wake = got.append
    w._on_snapshot([_Doc({"unexpected": "shape"}), _doc()], [], None)
    assert len(got) == 1


def test_an_empty_document_is_skipped():
    w = _watcher()
    got = []
    w._on_wake = got.append
    w._on_snapshot([_Doc(None), _doc()], [], None)
    assert len(got) == 1


def test_repeated_snapshots_do_not_re_wake_for_the_same_deferral():
    """Firestore re-delivers the whole matching set on every change, so without
    dedup the fleet would re-escalate everything whenever anything moved."""
    w = _watcher()
    got = []
    w._on_wake = got.append
    for _ in range(4):
        w._on_snapshot([_doc()], [], None)
    assert len(got) == 1
    assert len(w.wakeups) == 1


def test_a_second_distinct_deferral_does_wake_it_again():
    w = _watcher()
    got = []
    w._on_wake = got.append
    w._on_snapshot([_doc(entry_id="d1")], [], None)
    w._on_snapshot([_doc(entry_id="d1"), _doc(entry_id="d2")], [], None)
    assert len(got) == 2
    assert len(w.wakeups) == 2


def test_the_wakeup_counts_how_many_matched():
    w = _watcher()
    w._on_wake = lambda e: None
    w._on_snapshot([_doc(entry_id=f"d{i}") for i in range(5)], [], None)
    assert w.wakeups[0].matched == 5
    assert len(w.wakeups[0].acted_on) == 5


def test_today_is_read_per_snapshot_not_frozen_at_construction():
    """The claim is that time passing wakes it. If `today` were captured once,
    a long-lived service would never notice a deferral expiring."""
    now = {"d": "2026-08-01"}
    w = FirestoreWatcher(collection="c", today=lambda: now["d"], client=object())
    got = []
    w._on_wake = got.append

    w._on_snapshot([_doc()], [], None)
    assert got == []

    now["d"] = "2026-08-13"
    w._on_snapshot([_doc()], [], None)
    assert len(got) == 1


def test_wakeups_is_a_copy_so_callers_cannot_mutate_the_record():
    w = _watcher()
    w._on_wake = lambda e: None
    w._on_snapshot([_doc()], [], None)
    w.wakeups.clear()
    assert len(w.wakeups) == 1
