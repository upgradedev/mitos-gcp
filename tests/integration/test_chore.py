"""Tests for the claims a judge is entitled to doubt.

Every test here maps to a sentence the entry makes out loud. If a sentence has no
test, either the sentence goes or the test gets written.

Offline: no ADK, no cloud credential, no model. The chore's logic is
deterministic by design, which is what lets the rejection happen on every take
rather than whenever the model happens to misbehave.
"""

from __future__ import annotations

import uuid

import pytest

from mitos.chore import (  # noqa: E402
    PlanHashMismatch,
    execute_write,
    run_chore,
)
from mitos.evaluator import evaluate  # noqa: E402
from mitos.fixtures import PR_4471, PR_4472, SEEDED_HISTORY  # noqa: E402
from mitos.fleet import route  # noqa: E402
from mitos.guard import ROLE_READER, ROLE_WRITER  # noqa: E402
from mitos.ledger import Entry, InMemoryLedger  # noqa: E402


def _ledger(seeded: bool = True) -> InMemoryLedger:
    led = InMemoryLedger()
    if seeded:
        for item in SEEDED_HISTORY:
            led.append(
                Entry(
                    kind=item["kind"],
                    actor=item["actor"],
                    subject=item["subject"],
                    payload=item["payload"],
                    run_id="seed",
                )
            )
    return led


def _run(pr, led, approve=True):
    return run_chore(
        pr,
        led,
        run_id=uuid.uuid4().hex[:8],
        approve=(lambda card: approve),
    )


# --------------------------------------------------------------------------
# "the router makes a decision, it does not run a fixed pipeline"
# --------------------------------------------------------------------------


def test_router_wakes_different_fleets_for_different_diffs():
    personal = route(PR_4471)
    plain = route(PR_4472)

    assert "compliance-companion" in personal.woken
    assert "compliance-companion" in plain.skipped, (
        "the router woke compliance on a diff carrying no personal data, so the "
        "branch point is decorative"
    )
    assert personal.woken != plain.woken


def test_router_cites_the_hunk_that_raised_each_signal():
    """A dispatch decision with no evidence is an assertion."""
    for signal in route(PR_4471).signals:
        assert signal.path, signal
        assert signal.evidence.strip(), signal


# --------------------------------------------------------------------------
# "the evaluator visibly rejects something, on every take"
# --------------------------------------------------------------------------


def test_the_gate_rejects_the_first_draft_deterministically():
    led = _ledger()
    result = _run(PR_4471, led)

    assert not result.first_verdict.passed, (
        "draft 1 passed, so the demo has no rejection to show"
    )
    kinds = {f.check for f in result.first_verdict.findings}
    assert "secret-leak" in kinds
    assert "prompt-injection" in kinds
    assert result.first_verdict.injection_attempt is True


def test_the_rejection_repeats_across_runs():
    """The poison is in the fixture, so this cannot come out differently."""
    verdicts = [_run(PR_4471, _ledger()).first_verdict.passed for _ in range(5)]
    assert verdicts == [False] * 5


def test_the_repaired_draft_passes_and_carries_no_secret():
    result = _run(PR_4471, _ledger())
    assert result.final_verdict is not None
    assert result.final_verdict.passed
    assert result.card is not None
    assert "SharedAccessKey" not in result.card.body
    assert "ignore previous instructions" not in result.card.body.lower()


def test_the_gate_can_fail_a_clean_draft_too():
    """Proof the detectors are not no-ops: hand them something bad directly."""
    # Same reason as in fixtures.py: no credential-shaped literal is committed,
    # so the secret scanner stays at full strength with no ignore file.
    from tests.synthetic_secrets import SERVICE_BUS

    bad = f"See config: {SERVICE_BUS}"
    assert not evaluate(bad).passed


def test_a_clean_draft_passes():
    """And proof they are not always-on, which would be the other way to cheat."""
    assert evaluate("## Schema impact\n\nA column was added.").passed


# --------------------------------------------------------------------------
# "context across weeks of asynchronous operations"
# --------------------------------------------------------------------------


def test_the_second_run_recalls_what_the_first_run_wrote():
    """The claim that matters for the track. Not a seeded read: run one writes
    the entries that run two finds."""
    led = _ledger(seeded=False)

    first = _run(PR_4471, led)
    assert first.written

    raised = [e for e in led.all() if e.kind == "finding.raised"]
    assert raised, "run 1 recorded no findings, so run 2 has nothing to recall"

    second = _run(PR_4472, led)
    recalled_findings = {
        e.payload.get("finding") for e in second.recalled if e.kind == "finding.raised"
    }
    assert recalled_findings, "run 2 did not recall anything run 1 wrote"
    assert recalled_findings <= {e.payload.get("finding") for e in raised}


def test_an_expired_deferral_escalates_instead_of_re_filing():
    led = _ledger()
    result = _run(PR_4471, led)

    assert result.escalated
    # The flag alone is our own bookkeeping. The escalation has to be in the
    # thread, because that is the thing a human retraces later.
    escalations = [e for e in led.all() if e.kind == "finding.escalated"]
    assert len(escalations) == 1, [e.kind for e in led.all()]
    assert "expired" in escalations[0].payload["reason"]


def test_a_live_deferral_does_not_escalate():
    """The inverse, so the escalation is attributable to the date and not to the
    fact that a deferral exists at all."""
    led = _ledger()
    result = run_chore(
        PR_4471, led, run_id="t", approve=lambda c: True, today="2026-08-01"
    )
    assert not result.escalated


def test_findings_already_in_the_thread_are_not_re_filed():
    led = _ledger(seeded=False)
    _run(PR_4471, led)
    before = len([e for e in led.all() if e.kind == "finding.raised"])
    _run(PR_4471, led)
    after = len([e for e in led.all() if e.kind == "finding.raised"])
    assert after == before, "the same finding was filed twice"


# --------------------------------------------------------------------------
# "one human-approved governed write"
# --------------------------------------------------------------------------


def test_nothing_is_written_without_approval():
    led = _ledger()
    result = _run(PR_4471, led, approve=False)
    assert not result.written
    assert not [e for e in led.all() if e.kind == "write.executed"]


def test_the_writer_refuses_a_plan_that_changed_after_approval():
    """An approval is for exact bytes. This is the reason the card shows a
    sha256 rather than a summary."""
    result = _run(PR_4471, _ledger())
    card = result.card
    assert card is not None
    approved = card.plan_hash

    card.body += "\n\nand also delete the retention rule"

    with pytest.raises(PlanHashMismatch):
        execute_write(card, approved, role=ROLE_WRITER)


def test_the_writer_refuses_the_reader_identity():
    result = _run(PR_4471, _ledger())
    card = result.card
    assert card is not None
    with pytest.raises(PermissionError):
        execute_write(card, card.plan_hash, role=ROLE_READER)


# --------------------------------------------------------------------------
# "a thread of provenance you can follow back"
# --------------------------------------------------------------------------


def test_every_outcome_walks_back_to_the_diff_that_caused_it():
    led = _ledger()
    result = _run(PR_4471, led)
    thread = led.thread(result.last_entry_id)

    assert thread[0].kind == "trigger.pull_request", (
        "the thread does not reach the trigger, so it cannot be retraced"
    )
    assert thread[0].payload["pr"] == PR_4471.number
    kinds = [e.kind for e in thread]
    for expected in (
        "fleet.dispatch",
        "evaluator.verdict",
        "plan.proposed",
        "write.executed",
    ):
        assert expected in kinds, f"{expected} missing from the thread: {kinds}"


def test_the_ledger_has_no_mutation_path():
    """Append-only is a claim about the interface, so assert on the interface."""
    for forbidden in ("update", "delete", "set", "overwrite", "remove"):
        assert not hasattr(InMemoryLedger, forbidden), forbidden


def test_a_specialist_may_cite_a_file_it_actually_read():
    """The hallucination check compares citations against what the fleet opened,
    not against what happened to be in the diff.

    While specialists only saw a diff those were the same set. Once they began
    reading the repository they diverged, and every legitimate citation of a
    specification became a hallucination finding. Eight of thirteen items parked
    that way on a live run.
    """

    class _ReadsTheSpec:
        model = "test-analyst"

        def assess(self, companion, pr, signals):
            return {
                "status": "ok",
                "assessment": "See `docs/specs/billing.md` for the tariff rule.",
                "findings": [],
                "citations": ["docs/specs/billing.md"],
                "paths_read": ["docs/specs/billing.md"],
                "confidence": 0.9,
                "read_log": {"tool_calls": 1, "reads": 1, "denied": 0, "sequence": []},
            }

    result = run_chore(
        PR_4472, _ledger(), run_id="cite", approve=lambda c: True,
        analyst=_ReadsTheSpec(),
    )
    hallucinations = [
        f for f in result.first_verdict.findings if f.check == "hallucinated-path"
    ]
    assert not hallucinations, (
        f"a file the specialist read was called a hallucination: {hallucinations}"
    )


def test_a_path_nobody_read_is_still_a_hallucination():
    """The inverse, so the check is not simply switched off."""

    class _Invents:
        model = "test-analyst"

        def assess(self, companion, pr, signals):
            return {
                "status": "ok",
                "assessment": "See `docs/specs/invented.md`.",
                "findings": [],
                "citations": ["docs/specs/invented.md"],
                "paths_read": [],
                "confidence": 0.9,
                "read_log": {},
            }

    result = run_chore(
        PR_4472, _ledger(), run_id="inv", approve=lambda c: True, analyst=_Invents()
    )
    assert any(
        f.check == "hallucinated-path" for f in result.first_verdict.findings
    ), "an invented path passed the check"
