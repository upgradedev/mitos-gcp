"""The live adapter, against the Firestore emulator.

Coverage that skips the backend the deployed services actually use is not
coverage. `InMemoryLedger` passing every contract test proves the contract is
satisfiable, not that Firestore satisfies it, so the same contract is run again
here against a real Firestore wire protocol.

Skipped when the emulator is not running, and CI starts one, so the skip cannot
quietly become permanent.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="no Firestore emulator; set FIRESTORE_EMULATOR_HOST",
)


@pytest.fixture
def ledger():
    from mitos.ledger import FirestoreLedger

    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "mitos-test")
    # A fresh collection per test, so runs cannot contaminate each other.
    return FirestoreLedger(project="mitos-test", collection=f"t{uuid.uuid4().hex[:10]}")


def _entry(**kw):
    from mitos.ledger import Entry

    kw.setdefault("kind", "note")
    kw.setdefault("actor", "test")
    kw.setdefault("subject", "svc")
    return Entry(**kw)


def test_append_then_read_back(ledger):
    e = ledger.append(_entry(payload={"v": 1}))
    stored = ledger.all()
    assert [s.entry_id for s in stored] == [e.entry_id]
    assert stored[0].payload == {"v": 1}


def test_recall_filters_the_same_way_the_offline_backend_does(ledger):
    ledger.append(_entry(kind="finding.raised", subject="a"))
    ledger.append(_entry(kind="noise", subject="a"))
    ledger.append(_entry(kind="finding.raised", subject="b"))

    got = ledger.recall("a", kinds={"finding.raised"})
    assert len(got) == 1
    assert got[0].subject == "a"


def test_thread_walks_back_through_firestore(ledger):
    root = ledger.append(_entry(kind="root"))
    mid = ledger.append(_entry(kind="mid", parent_id=root.entry_id))
    leaf = ledger.append(_entry(kind="leaf", parent_id=mid.entry_id))

    assert [e.kind for e in ledger.thread(leaf.entry_id)] == ["root", "mid", "leaf"]


def test_appending_the_same_id_twice_is_refused(ledger):
    """`create` rather than `set`. An append-only ledger must not let a second
    write silently replace the first."""
    from google.api_core import exceptions

    e = _entry()
    ledger.append(e)
    with pytest.raises(exceptions.AlreadyExists):
        ledger.append(e)


def test_the_whole_chore_runs_against_firestore(ledger):
    """The deployed path, end to end, on the real backend."""
    from mitos.chore import run_chore
    from mitos.fixtures import PR_4471

    result = run_chore(PR_4471, ledger, run_id="emul", approve=lambda card: True)
    assert result.written
    assert not result.first_verdict.passed
    assert ledger.thread(result.last_entry_id)[0].kind == "trigger.pull_request"
