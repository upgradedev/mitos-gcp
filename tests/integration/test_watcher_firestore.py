"""The subscription itself, against the Firestore emulator.

The entry's headline is that a query subscription is the trigger. The unit tests
prove the predicate and the behaviour; this proves the mechanism, because
`on_snapshot` is the one part that cannot be reasoned about and has to be run.

If this suite is skipped, the claim is unproven. CI starts an emulator and fails
the build if it skips.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="no Firestore emulator; set FIRESTORE_EMULATOR_HOST",
)


@pytest.fixture
def collection() -> str:
    return f"w{uuid.uuid4().hex[:10]}"


def _deferral(expires: str):
    from mitos.ledger import Entry
    from mitos.watcher import DEFERRAL_KIND

    return Entry(
        kind=DEFERRAL_KIND,
        actor="compliance-companion",
        subject="services/customer",
        payload={"finding": "personal data, no retention entry", "expires_on": expires},
    )


def test_firestore_delivers_a_snapshot_and_the_fleet_wakes(collection):
    """Write a document from one place; a subscription somewhere else fires.

    Nothing polls and nothing is scheduled. This is the whole claim.
    """
    from mitos.ledger import FirestoreLedger
    from mitos.watcher import FirestoreWatcher

    ledger = FirestoreLedger(project="mitos-test", collection=collection)
    woken = threading.Event()
    seen: list = []

    watcher = FirestoreWatcher(
        project="mitos-test", collection=collection, today=lambda: "2026-08-19"
    )

    def on_wake(expired):
        seen.extend(expired)
        woken.set()

    watcher.start(on_wake)
    try:
        # Give the initial (empty) snapshot time to arrive before writing, so
        # the wake is attributable to the write and not to the subscription
        # opening.
        time.sleep(2.0)
        assert not woken.is_set(), "woke before anything expired"

        ledger.append(_deferral("2026-08-12"))

        assert woken.wait(timeout=30), (
            "Firestore never delivered a snapshot; the control plane does not work"
        )
        assert len(seen) == 1
        assert seen[0].payload["expires_on"] == "2026-08-12"
        assert len(watcher.wakeups) == 1
    finally:
        watcher.stop()


def test_a_live_deferral_does_not_wake_the_fleet(collection):
    """The negative. A subscription that fires on everything is a poller."""
    from mitos.ledger import FirestoreLedger
    from mitos.watcher import FirestoreWatcher

    ledger = FirestoreLedger(project="mitos-test", collection=collection)
    woken = threading.Event()

    watcher = FirestoreWatcher(
        project="mitos-test", collection=collection, today=lambda: "2026-08-19"
    )
    watcher.start(lambda expired: woken.set())
    try:
        ledger.append(_deferral("2026-12-01"))
        assert not woken.wait(timeout=8), "woke for a deferral that has not expired"
        assert watcher.wakeups == []
    finally:
        watcher.stop()


def test_waking_escalates_into_the_thread_and_is_retraceable(collection):
    from mitos.chore import escalate_on_wake
    from mitos.ledger import FirestoreLedger
    from mitos.watcher import FirestoreWatcher

    ledger = FirestoreLedger(project="mitos-test", collection=collection)
    done = threading.Event()

    watcher = FirestoreWatcher(
        project="mitos-test", collection=collection, today=lambda: "2026-08-19"
    )

    def on_wake(expired):
        escalate_on_wake(ledger, expired)
        done.set()

    watcher.start(on_wake)
    try:
        deferral = ledger.append(_deferral("2026-08-12"))
        assert done.wait(timeout=30), "the fleet never woke"
        time.sleep(1.0)

        escalations = [e for e in ledger.all() if e.kind == "finding.escalated"]
        assert len(escalations) == 1
        assert escalations[0].parent_id == deferral.entry_id
        assert ledger.thread(escalations[0].entry_id)[0].entry_id == deferral.entry_id
    finally:
        watcher.stop()
