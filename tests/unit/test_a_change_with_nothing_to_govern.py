"""A governance tool has to be able to say "this needed no governance".

The first real pull request this fleet judged was its own, and it changed one
file: `README.md`. The router skipped all three specialists and Gemini agreed
with it, giving as its rationale that the change only updated documentation and
introduced no schema modification, personal data or specification drift. Both
were right.

The run then continued, collected no fragments, and handed an empty string to
the gate, where the `non-empty` check refused it as a HIGH finding. The check run
posted to GitHub said `action_required`. So the product reached the correct
judgement and reported the opposite of it, on the pull request a judge meets
first.

The gate is not the thing that was wrong and is not what changed. `non-empty`
still refuses an empty draft. What changed is that a run with nothing to assess
no longer manufactures one, which is fixing the reality rather than the measure.

Standard library only, per `test_offline_suite_stays_offline.py`.
"""

from __future__ import annotations

from mitos.chore import run_chore
from mitos.fixtures import PullRequest
from mitos.ledger import InMemoryLedger

DOCS_ONLY = PullRequest(
    number=102,
    title="docs: Mitos now watches this repository",
    author="upgradedev",
    files=[{"path": "README.md", "patch": "@@ -1 +1 @@", "status": "modified"}],
)


def _run():
    led = InMemoryLedger()
    result = run_chore(
        DOCS_ONLY, led, run_id="docs", repository="upgradedev/mitos-gcp"
    )
    return result, led


def test_no_specialist_is_woken_by_a_documentation_change():
    """The premise. If this ever changes the rest of the file is about nothing."""
    result, _ = _run()

    assert result.dispatch.woken == [], (
        f"a documentation-only change woke {result.dispatch.woken}"
    )
    assert result.dispatch.skipped, "the router recorded no decision at all"


def test_it_is_recorded_as_nothing_to_govern_rather_than_as_a_finding():
    """The thread is the product, so the answer has to be in it, named."""
    _, led = _run()
    kinds = [e.kind for e in led.all()]

    assert "run.nothing_to_govern" in kinds, (
        f"the run did not record why it stopped; it recorded {kinds}"
    )
    assert not any(k.startswith("finding.") for k in kinds), (
        f"a change with nothing to govern produced findings: {kinds}"
    )
    assert "evaluator.verdict" not in kinds, (
        "an empty draft was still sent to the gate, which is the defect this "
        "file is about"
    )

    recorded = next(e for e in led.all() if e.kind == "run.nothing_to_govern")
    assert recorded.payload.get("skipped"), (
        "the entry does not say which specialists were skipped, so a reader "
        "cannot check the decision"
    )


def test_the_verdict_is_not_a_failure():
    """`passed=False` here is what GitHub turned into `action_required`."""
    result, _ = _run()

    assert result.first_verdict.passed is True, (
        "a change nobody needed to assess is reported as having failed a gate"
    )
    assert result.card is None, "an approval card was minted for nothing"
    assert result.written is False
    assert result.parked_by is None, (
        "recorded as a specialist refusal, which it is not: no specialist ran"
    )


def test_nothing_is_written_and_no_plan_is_proposed():
    """ADR-007's boundary, asserted on this path too rather than assumed."""
    _, led = _run()
    kinds = {e.kind for e in led.all()}

    assert "plan.proposed" not in kinds
    assert "write.executed" not in kinds


def test_a_real_schema_change_still_runs_the_whole_chore():
    """The counterweight. A shortcut that swallowed real work would pass every
    assertion above, so this asserts the path is still reached."""
    schema = PullRequest(
        number=4471,
        title="feat: add mobile contact",
        author="someone",
        files=[
            {
                "path": "services/customer/migrations/V211__add_mobile.sql",
                "patch": "@@ +1 @@\n+ALTER TABLE customer ADD COLUMN mobile_number VARCHAR(20);",
                "status": "added",
            }
        ],
    )
    led = InMemoryLedger()
    result = run_chore(
        schema, led, run_id="real", repository="upgradedev/mitos-gcp"
    )
    kinds = {e.kind for e in led.all()}

    assert result.dispatch.woken, "a schema change woke no specialist"
    assert "run.nothing_to_govern" not in kinds, (
        "a real schema change was dismissed as having nothing to govern"
    )
    assert "evaluator.verdict" in kinds, "the gate did not run on a real change"

