"""A morning's backlog, and the count a judge is owed.

The criterion asks how much friction the fleet removes on its own. These assert
the shape of the answer: that items complete unattended, that some are refused
with a reason, and that one refusal does not stop the queue.
"""

from __future__ import annotations

from mitos.batch import run_batch
from mitos.envelope import Status
from mitos.fixtures import BACKLOG, SEEDED_HISTORY
from mitos.fleet import route, run_specialist
from mitos.ledger import Entry, InMemoryLedger


def _ledger():
    led = InMemoryLedger()
    for i in SEEDED_HISTORY:
        led.append(Entry(kind=i["kind"], actor=i["actor"], subject=i["subject"],
                         payload=i["payload"], run_id="seed"))
    return led


def _report():
    return run_batch(BACKLOG, _ledger(), approve=lambda card: True)


def test_the_backlog_produces_a_countable_result():
    """The count the autonomy criterion asks for.

    Thirteen rather than twelve since PR 4483 was added: the item the rules
    cannot get right. It is not parked here, deliberately, because this suite
    runs without a model. `test_rules_alone_are_not_enough.py` asserts that
    failure directly and the live suite asserts the model fixes it.

    Every state is in the sum. `review` was added and left out of it, and this
    test caught that immediately, which is the whole reason it counts rather
    than sampling: a state missing from the total is work the report loses.
    """
    r = _report()
    assert r.presented == len(BACKLOG) == 13
    assert (
        r.completed + r.parked + r.no_action + r.review == r.presented
    ), "items went missing between the queue and the report"


def test_some_items_are_parked_and_some_complete():
    """A fleet that completes everything has either been given easy work or is
    answering questions it is not entitled to decide."""
    r = _report()
    assert r.completed > 0
    assert r.parked >= 3, f"only {r.parked} parked; the mix is not being exercised"
    assert r.no_action >= 1


def test_every_parked_item_names_who_refused_and_why():
    r = _report()
    for pr, who, why in r.parked_reasons():
        assert who and who != "?", f"PR {pr} parked with no owner"
        assert len(why) > 40, f"PR {pr} parked with a reason too thin to act on: {why}"


def test_the_parked_reasons_are_distinct_kinds_of_refusal():
    """Three items park for three different causes, not one rule firing thrice."""
    r = _report()
    who = {w for _, w, _ in r.parked_reasons()}
    assert len(who) >= 2, f"every refusal came from {who}"


def test_nothing_needs_a_human_before_the_approval_step():
    r = _report()
    assert r.human_interventions_before_approval == 0


def test_only_completed_items_ask_for_an_approval():
    r = _report()
    assert r.approvals_requested == r.completed


def test_a_refusal_does_not_stop_the_queue():
    """The item after a parked one still gets worked.

    This asserted specifically that something later `completed`, which held only
    while every run proposed a write to the same file whatever the change was.
    The property under test is that the queue keeps going, so it asks for a
    worked outcome rather than for one particular kind of worked outcome.
    """
    r = _report()
    states = [o.state for o in r.outcomes]
    parked_at = states.index("parked")
    after = states[parked_at + 1 :]

    assert after, "the parked item was the last one, so this proves nothing"
    assert any(s in ("completed", "review") for s in after), (
        f"the queue stopped at the first refusal: {after}"
    )


def test_an_irreversible_migration_is_refused_by_the_schema_specialist():
    pr = [p for p in BACKLOG if p.number == 4475][0]
    out = run_specialist("db-architect-leader", pr, route(pr).signals)
    assert out.status is Status.BLOCKED
    assert "irreversible" in out.reason


def test_a_destructive_migration_is_visible_to_the_router_at_all():
    """It used to raise no signal, so it woke nobody and was counted as
    'nothing to do'. A destructive change being invisible is worse than one
    that parks."""
    pr = [p for p in BACKLOG if p.number == 4481][0]
    assert route(pr).woken, "DROP TABLE woke nobody"


def test_special_category_data_is_refused_not_assessed():
    pr = [p for p in BACKLOG if p.number == 4477][0]
    assert "compliance-companion" in route(pr).woken, (
        "the router never woke compliance, so it could not refuse"
    )
    out = run_specialist("compliance-companion", pr, route(pr).signals)
    assert out.status is Status.BLOCKED
    assert "Article 9" in out.reason


def test_ordinary_personal_data_is_assessed_not_refused():
    """The inverse, so the refusal is attributable to the category and not to
    compliance refusing everything."""
    pr = [p for p in BACKLOG if p.number == 4471][0]
    out = run_specialist("compliance-companion", pr, route(pr).signals)
    assert out.status is Status.OK
    assert out.findings


def test_a_parked_item_never_produces_a_plan_or_a_write():
    r = _report()
    for o in r.outcomes:
        if o.state == "parked":
            assert o.plan_hash == ""
            assert o.published is False
