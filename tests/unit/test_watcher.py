"""The control plane, tested as a predicate and as a behaviour.

The entry's headline claim is that the fleet has no scheduler and no queue: a
query subscription is the trigger. Two things have to be true for that to be
honest, and both are asserted here.

The predicate has to be right, because Firestore maintains the *set* and we
judge it. And waking has to actually do something and be countable, because
"the agent woke" is worthless to a judge who cannot see what changed.
"""

from __future__ import annotations

import pytest

from mitos.chore import escalate_on_wake
from mitos.ledger import Entry, InMemoryLedger
from mitos.watcher import (
    DEFERRAL_KIND,
    InMemoryWatcher,
    expired_deferrals,
)

TODAY = "2026-08-19"


def _deferral(expires: str, entry_id: str = "", **extra):
    payload = {"finding": "personal data with no retention entry", "expires_on": expires}
    payload.update(extra)
    kw = dict(kind=DEFERRAL_KIND, actor="compliance-companion",
              subject="services/customer", payload=payload)
    if entry_id:
        kw["entry_id"] = entry_id
    return Entry(**kw)


# --------------------------------------------------------------------------
# The predicate
# --------------------------------------------------------------------------


def test_an_expired_deferral_matches():
    assert len(expired_deferrals([_deferral("2026-08-12")], TODAY)) == 1


def test_a_deferral_expiring_today_matches():
    """Boundary. "Until the twelfth" means the twelfth is the last day it holds,
    so on the twelfth it is due."""
    assert len(expired_deferrals([_deferral(TODAY)], TODAY)) == 1


def test_a_live_deferral_does_not_match():
    assert expired_deferrals([_deferral("2026-12-01")], TODAY) == []


def test_a_resolved_deferral_does_not_match():
    """Someone dealt with it. Waking about it again is noise, and noise is how
    an autonomous system gets switched off."""
    assert expired_deferrals([_deferral("2026-08-12", resolved=True)], TODAY) == []


def test_a_deferral_with_no_expiry_does_not_match():
    """Fail closed in the quiet direction: never wake on a date we do not have."""
    e = Entry(kind=DEFERRAL_KIND, actor="a", subject="s", payload={"finding": "x"})
    assert expired_deferrals([e], TODAY) == []


def test_other_entry_kinds_are_ignored():
    noise = Entry(kind="write.executed", actor="writer", subject="services/customer",
                  payload={"expires_on": "2026-01-01"})
    assert expired_deferrals([noise], TODAY) == []


# --------------------------------------------------------------------------
# The behaviour
# --------------------------------------------------------------------------


def test_the_fleet_wakes_without_anyone_calling_it():
    """The headline. Nothing here invokes the chore; the world changes and the
    fleet acts."""
    led = InMemoryLedger()
    woken: list[list[Entry]] = []
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(woken.append)

    led.append(_deferral("2026-08-12"))
    watcher.tick()

    assert len(woken) == 1
    assert len(woken[0]) == 1
    assert len(watcher.wakeups) == 1
    assert watcher.wakeups[0].matched == 1


def test_nothing_wakes_while_the_deferral_is_live():
    led = InMemoryLedger()
    led.append(_deferral("2026-12-01"))
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(lambda e: None)

    assert watcher.tick() == []
    assert watcher.wakeups == []


def test_the_same_deferral_does_not_wake_the_fleet_twice():
    """A subscription delivers the whole matching set on every change, so
    without this the fleet would re-escalate everything every time anything
    moved."""
    led = InMemoryLedger()
    led.append(_deferral("2026-08-12"))
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(lambda e: None)

    assert len(watcher.tick()) == 1
    assert watcher.tick() == []
    assert watcher.tick() == []
    assert len(watcher.wakeups) == 1


def test_time_passing_is_what_wakes_it_not_a_new_document():
    """The claim is that the QUERY is the trigger. Nothing is written here
    between the two ticks; only the date moves."""
    led = InMemoryLedger()
    led.append(_deferral("2026-08-12"))
    watcher = InMemoryWatcher(led, today="2026-08-01")
    watcher.start(lambda e: None)

    assert watcher.tick() == [], "woke while the deferral was still live"
    assert len(watcher.tick(today="2026-08-13")) == 1


def test_waking_records_why_so_the_claim_is_countable():
    led = InMemoryLedger()
    led.append(_deferral("2026-08-12"))
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(lambda e: None)
    watcher.tick()

    wake = watcher.wakeups[0]
    assert "expired" in wake.reason
    assert wake.at == TODAY
    assert len(wake.acted_on) == 1


# --------------------------------------------------------------------------
# What it does when it wakes
# --------------------------------------------------------------------------


def test_waking_escalates_into_the_same_thread():
    led = InMemoryLedger()
    deferral = led.append(_deferral("2026-08-12", entry_id="def12345"))
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(lambda expired: escalate_on_wake(led, expired))
    watcher.tick()

    escalations = [e for e in led.all() if e.kind == "finding.escalated"]
    assert len(escalations) == 1
    assert escalations[0].parent_id == deferral.entry_id, (
        "the escalation is not attached to the deferral, so it cannot be retraced"
    )
    assert escalations[0].payload["woken_by"] == "firestore-query-subscription"


def test_an_unattended_wake_never_reaches_the_write_credential():
    """The safety property. Waking is cheap and nobody is watching, so it must
    not be able to reach the one credential that changes something outside the
    ledger."""
    led = InMemoryLedger()
    led.append(_deferral("2026-08-12"))
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(lambda expired: escalate_on_wake(led, expired))
    watcher.tick()

    kinds = {e.kind for e in led.all()}
    assert "write.executed" not in kinds
    assert "plan.proposed" not in kinds


def test_the_escalation_carries_who_deferred_it_and_until_when():
    led = InMemoryLedger()
    led.append(
        _deferral("2026-08-12", deferred_by="dpo@example.test", deferred_on="2026-07-29")
    )
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(lambda expired: escalate_on_wake(led, expired))
    watcher.tick()

    payload = [e for e in led.all() if e.kind == "finding.escalated"][0].payload
    assert payload["deferred_by"] == "dpo@example.test"
    assert payload["expires_on"] == "2026-08-12"


def test_stopping_the_watcher_stops_the_fleet_waking():
    led = InMemoryLedger()
    led.append(_deferral("2026-08-12"))
    watcher = InMemoryWatcher(led, today=TODAY)
    calls: list = []
    watcher.start(calls.append)
    watcher.stop()
    watcher.tick()
    assert calls == []


@pytest.mark.parametrize("n", [1, 3, 7])
def test_several_expiring_at_once_are_one_wakeup_and_n_escalations(n):
    led = InMemoryLedger()
    for i in range(n):
        led.append(_deferral("2026-08-12", entry_id=f"d{i:04d}"))
    watcher = InMemoryWatcher(led, today=TODAY)
    watcher.start(lambda expired: escalate_on_wake(led, expired))
    watcher.tick()

    assert len(watcher.wakeups) == 1
    assert watcher.wakeups[0].matched == n
    assert len([e for e in led.all() if e.kind == "finding.escalated"]) == n


def test_time_passing_on_its_own_wakes_nothing():
    """The claim this pins down was in the README for weeks and was false.

    `FirestoreWatcher` subscribes to `kind == "finding.deferred"`, a filter with
    no date in it. A deferral reaching its expiry writes no document, changes no
    result set and produces no snapshot, so the callback that evaluates the
    expiry is never called. What is true is that the next change to the set, for
    any reason, hands the fleet every open deferral and the expired ones are
    escalated then.

    Asserted by advancing the clock and delivering nothing: a watcher that woke
    here would mean the claim was true after all, and this test should be
    deleted rather than adjusted.
    """
    from mitos.watcher import FirestoreWatcher

    clock = {"today": "2026-08-01"}
    watcher = FirestoreWatcher(client=object(), today=lambda: clock["today"])

    clock["today"] = "2026-12-31"

    assert watcher.wakeups == [], (
        "the watcher woke without a snapshot, so the calendar alone is enough "
        "after all and the README claim can be restored"
    )
