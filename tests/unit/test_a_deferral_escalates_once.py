"""A deferral is escalated once, and it used to be once per process.

`FirestoreWatcher._seen` is an instance attribute. Firestore hands a new
subscriber the whole matching set the moment it subscribes, and a new container
starts with that set empty, so every restart and every one of the four instances
`maxScale` allows re-escalated everything currently expired.

Measured on the live ledger before the fix: 1596 escalations over 73 distinct
parents, up to 40 copies of one deferral, payloads byte-identical.

Every dedup test in this suite reused ONE watcher object, which is exactly the
case that worked. This builds a second one over the same ledger, which is the
case that shipped.
"""

from __future__ import annotations

from mitos.chore import escalate_on_wake
from mitos.ledger import Entry, InMemoryLedger


def _deferral(led, subject="a retention column"):
    return led.append(
        Entry(
            kind="finding.deferred",
            actor="compliance-companion",
            subject=subject,
            payload={
                "deferred_on": "2026-07-01",
                "expires_on": "2026-08-12",
                "deferred_by": "dpo@example.test",
                "finding": "personal data kept past its retention window",
            },
            run_id="run-1",
        )
    )


def test_one_wake_escalates_one_deferral_once():
    led = InMemoryLedger()
    deferral = _deferral(led)

    written = escalate_on_wake(led, [deferral])

    assert len(written) == 1
    assert written[0].parent_id == deferral.entry_id


def test_a_second_process_over_the_same_ledger_escalates_nothing():
    """The four-instance repro, and the one no existing test covered.

    Restarting, or a second Cloud Run instance receiving the same snapshot, both
    look like this: a fresh caller with no memory, handed the same expired set,
    over a ledger that already holds the escalation.
    """
    led = InMemoryLedger()
    deferral = _deferral(led)

    first = escalate_on_wake(led, [deferral])
    second = escalate_on_wake(led, [deferral])
    third = escalate_on_wake(led, [deferral])

    assert len(first) == 1
    assert second == []
    assert third == []

    escalations = [e for e in led.all() if e.kind == "finding.escalated"]
    assert len(escalations) == 1, f"{len(escalations)} escalations for one deferral"


def test_two_deferrals_in_one_wake_are_not_confused_for_each_other():
    """The guard is keyed on parent_id. A guard keyed on anything the payloads
    share would suppress the second deferral as a duplicate of the first."""
    led = InMemoryLedger()
    one = _deferral(led, subject="a retention column")
    two = _deferral(led, subject="another retention column")

    written = escalate_on_wake(led, [one, two])

    assert {e.parent_id for e in written} == {one.entry_id, two.entry_id}
    assert len([e for e in led.all() if e.kind == "finding.escalated"]) == 2


def test_a_deferral_escalated_in_an_earlier_run_is_not_escalated_again():
    """The restart case stated directly: the escalation is already in the thread
    when this process first sees the deferral."""
    led = InMemoryLedger()
    deferral = _deferral(led)
    escalate_on_wake(led, [deferral])

    fresh = InMemoryLedger()
    for entry in led.all():
        fresh.append(entry)

    assert escalate_on_wake(fresh, [deferral]) == []


def test_the_guard_reads_the_thread_rather_than_marking_the_deferral():
    """`FirestoreLedger` says of itself "append-only by construction: there is no
    update or delete method here". A provenance product that reaches around its
    own append-only ledger to fix a duplication bug has traded the property it
    sells for the bug it fixes."""
    from mitos import chore

    source = chore.escalate_on_wake.__doc__ or ""
    assert "append-only" in source
    protocol = (
        __import__("inspect").getsource(chore.escalate_on_wake)
    )
    for mutation in (".update(", ".set(", ".delete("):
        assert mutation not in protocol, f"the guard mutates the ledger with {mutation}"
