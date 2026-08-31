"""`run_chore` compared every deferral against a date that stopped moving.

The signature read `today: str = "2026-08-19"`, and the production webhook path
never passed a value. The demo and the batch runner did, which is why this was
invisible from every command anyone actually ran by hand.

Measured on 2026-08-31, twelve days after the literal:

    deferral expiring 2026-08-20: expired under frozen clock = False | truly expired = True
    deferral expiring 2026-08-25: expired under frozen clock = False | truly expired = True

So a finding a human deferred until the 20th was still "not expired" on the
31st, and would be on any later date. The window of deferrals that silently
never escalate grew by one day per day. That is the escalation half of "safely
maintain context across weeks of asynchronous operations", which is the track's
own wording, and it was dead in production while the demo showed it working.

Standard library only, per `test_offline_suite_stays_offline.py`.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from mitos.chore import run_chore
from mitos.fixtures import PullRequest
from mitos.ledger import Entry, InMemoryLedger

PR = PullRequest(
    number=7001,
    title="Add mobile contact",
    author="someone",
    files=[
        {
            "path": "services/customer/migrations/V400__mobile.sql",
            "patch": (
                "@@ +1 @@\n"
                "+ALTER TABLE customer ADD COLUMN mobile_number VARCHAR(32);\n"
            ),
            "status": "added",
        }
    ],
)


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days_from_now(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _ledger_with_deferral(expires_on: str) -> InMemoryLedger:
    led = InMemoryLedger()
    led.append(
        Entry(
            kind="finding.deferred",
            actor="compliance-companion",
            subject="services/customer",
            payload={
                "finding": "personal data field with no retention entry",
                "deferred_by": "data-protection-lead@example-utility.test",
                "deferred_on": "2026-07-29",
                "expires_on": expires_on,
            },
            run_id="seed",
        )
    )
    return led


def _escalated(led: InMemoryLedger) -> bool:
    return any(e.kind == "finding.escalated" for e in led.all())


def test_the_default_is_not_a_literal_date():
    """The defect, as a signature check. A hard-coded default here cannot be
    seen from any command a person runs, because every one of them passes a
    value."""
    default = inspect.signature(run_chore).parameters["today"].default

    assert default is None, (
        f"run_chore defaults `today` to {default!r}; production never passes one, "
        f"so that literal is the production clock"
    )


def test_a_deferral_that_expired_yesterday_escalates_with_no_clock_passed():
    """The production path, which passes no `today`. This did not escalate
    before the fix, and would not have on any later date either."""
    led = _ledger_with_deferral(_days_from_now(-1))

    run_chore(PR, led, run_id="r1", approve=lambda card: False)

    assert _escalated(led), (
        "a deferral that expired yesterday was not escalated by a run that "
        "passed no clock"
    )


def test_a_deferral_that_expires_tomorrow_does_not_escalate():
    """The counterweight. A change that escalated everything would satisfy the
    test above and make every deferral pointless."""
    led = _ledger_with_deferral(_days_from_now(+1))

    run_chore(PR, led, run_id="r2", approve=lambda card: False)

    assert not _escalated(led), "a deferral with time left was escalated"


def test_an_injected_clock_still_wins():
    """The demo and the recorded video pin the date on purpose, so the
    parameter has to keep working. What changed is what absence means."""
    led = _ledger_with_deferral("2026-08-12")

    run_chore(PR, led, run_id="r3", today="2026-08-01", approve=lambda card: False)

    assert not _escalated(led), "an injected clock was ignored"


def test_the_clock_is_utc_rather_than_local():
    """A local clock puts the boundary in a different place for every deployment
    and every developer, and Cloud Run is not in the same zone as anyone here."""
    led = _ledger_with_deferral(_utc_today())

    run_chore(PR, led, run_id="r4", approve=lambda card: False)

    # Expiry is `expires_on < today`, so a deferral expiring today has not
    # expired yet. Under a local clock east of UTC this flips a day early.
    assert not _escalated(led)


def test_the_boundary_is_the_day_it_claims_to_be():
    """Off by one here is a finding that escalates a day early or a day late,
    for every deferral, forever."""
    yesterday = _ledger_with_deferral(_days_from_now(-1))
    today = _ledger_with_deferral(_utc_today())

    run_chore(PR, yesterday, run_id="y", approve=lambda card: False)
    run_chore(PR, today, run_id="t", approve=lambda card: False)

    assert _escalated(yesterday)
    assert not _escalated(today)
