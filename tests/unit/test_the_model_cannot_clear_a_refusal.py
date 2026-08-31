"""ADR-002 says the model can only tighten. On the specialist path it could clear.

`run_specialist` ran the deterministic companion only when there was no
analyst. The model branch returned before `SPECIALISTS[name]` was ever called,
so in production, where an analyst is always configured, the deterministic rules
did not execute and the model's answer was the whole answer.

Three lines above the code that made it possible, a comment asserted the
opposite: that deterministic rules "run first and return before reaching here".
They did not.

Reproduced before the fix, on `DROP TABLE customers;`:

    deterministic          -> blocked | irreversible migration in ...
    with a model saying ok -> ok

The two refusals this lost are the two that most need a human: an irreversible
migration, and special-category data under GDPR Article 9. Both are the cases
where the deterministic gate exists precisely because a model's opinion is not
good enough.

Standard library only, per `test_offline_suite_stays_offline.py`.
"""

from __future__ import annotations

from mitos import fleet
from mitos.chore import run_chore
from mitos.envelope import Status
from mitos.fixtures import PullRequest
from mitos.ledger import InMemoryLedger

DESTRUCTIVE_PR = PullRequest(
    number=9001,
    title="Drop the legacy customer table",
    author="someone",
    files=[
        {
            "path": "services/customer/migrations/V300__drop.sql",
            "patch": "@@ +1 @@\n+DROP TABLE customers;\n",
            "status": "added",
        }
    ],
)

ARTICLE_9_PR = PullRequest(
    number=9002,
    title="Add health vulnerability flag",
    author="someone",
    files=[
        {
            "path": "services/customer/migrations/V301__health.sql",
            "patch": (
                "@@ +1 @@\n"
                "+ALTER TABLE customer ADD COLUMN health_condition VARCHAR(64);\n"
            ),
            "status": "added",
        }
    ],
)

CLEAN_PR = PullRequest(
    number=9003,
    title="Add a nickname column",
    author="someone",
    files=[
        {
            "path": "services/customer/migrations/V302__nickname.sql",
            "patch": "@@ +1 @@\n+ALTER TABLE customer ADD COLUMN nickname VARCHAR(8);\n",
            "status": "added",
        }
    ],
)


class _Says:
    """An analyst with a fixed verdict. The point is that it is believed or not."""

    model = "stub-with-an-opinion"

    def __init__(self, status: str) -> None:
        self.status = status

    def assess(self, name, pr, signals):
        return {
            "status": self.status,
            "assessment": f"{name} says {self.status}",
            "reason": "the model's own reason" if self.status == "blocked" else "",
            "confidence": 0.95,
        }


class _Raises:
    model = "stub-that-times-out"

    def assess(self, name, pr, signals):
        raise TimeoutError("deadline exceeded")


class _Junk:
    model = "stub-that-returns-nonsense"

    def assess(self, name, pr, signals):
        return "not a dict at all"


def _verdict(companion: str, pr: PullRequest, analyst) -> Status:
    out = fleet.run_specialist(companion, pr, fleet.detect_signals(pr), analyst=analyst)
    return out.status


# ---------------------------------------------------------------------------
# The floor: a deterministic refusal survives anything the model does
# ---------------------------------------------------------------------------


def test_a_destructive_migration_is_still_blocked_when_the_model_says_ok():
    """The reproduction, as a test. This returned OK before the fix."""
    assert _verdict("db-architect-leader", DESTRUCTIVE_PR, None) is Status.BLOCKED
    assert (
        _verdict("db-architect-leader", DESTRUCTIVE_PR, _Says("ok")) is Status.BLOCKED
    )


def test_special_category_data_is_still_blocked_when_the_model_says_ok():
    """GDPR Article 9 needs an assessment and a named owner, which cannot be
    derived from a diff and certainly not from a model saying it is fine."""
    assert _verdict("compliance-companion", ARTICLE_9_PR, None) is Status.BLOCKED
    assert (
        _verdict("compliance-companion", ARTICLE_9_PR, _Says("ok")) is Status.BLOCKED
    )


def test_a_model_that_times_out_cannot_clear_a_refusal():
    assert (
        _verdict("db-architect-leader", DESTRUCTIVE_PR, _Raises()) is Status.BLOCKED
    )


def test_a_model_that_returns_nonsense_cannot_clear_a_refusal():
    assert _verdict("db-architect-leader", DESTRUCTIVE_PR, _Junk()) is Status.BLOCKED


def test_the_refusal_keeps_its_own_reason_and_citation():
    """A refusal the human cannot act on is barely better than no refusal, so
    the deterministic reason must not be replaced by the model's silence."""
    out = fleet.run_specialist(
        "db-architect-leader",
        DESTRUCTIVE_PR,
        fleet.detect_signals(DESTRUCTIVE_PR),
        analyst=_Says("ok"),
    )

    assert "irreversible migration" in out.reason
    assert "DROP TABLE" in out.reason.upper()
    assert "services/customer/migrations/V300__drop.sql" in out.citations


# ---------------------------------------------------------------------------
# The ceiling: the model may still tighten, and must not be ignored
# ---------------------------------------------------------------------------


def test_the_model_can_still_block_something_the_rules_allow():
    """The counterweight. A change that pinned the deterministic verdict in
    place would pass every assertion above and destroy half of ADR-002."""
    assert _verdict("db-architect-leader", CLEAN_PR, None) is Status.OK
    assert _verdict("db-architect-leader", CLEAN_PR, _Says("blocked")) is Status.BLOCKED


def test_a_clean_change_with_a_content_model_stays_ok():
    assert _verdict("db-architect-leader", CLEAN_PR, _Says("ok")) is Status.OK


def test_an_unreachable_model_leaves_a_finding_rather_than_silence():
    """ADR-014: a step that could not be evaluated is recorded as such. A run
    that skipped half its analysis must not read as a clean one."""
    out = fleet.run_specialist(
        "db-architect-leader",
        CLEAN_PR,
        fleet.detect_signals(CLEAN_PR),
        analyst=_Raises(),
    )

    assert out.status is Status.OK
    assert any("unreachable" in f for f in out.findings), (
        f"the model failed and the run says nothing about it: {out.findings}"
    )


# ---------------------------------------------------------------------------
# Through run_chore, because the defect was invisible from the helper alone
# ---------------------------------------------------------------------------


def test_the_whole_chore_parks_a_destructive_migration_a_model_approved():
    """The brief's requirement, and the only version of this that would have
    caught the defect in production: the helper was fine in isolation, and the
    chore is what the webhook runs."""
    led = InMemoryLedger()
    result = run_chore(
        DESTRUCTIVE_PR, led, run_id="drop", analyst=_Says("ok"), approve=lambda c: True
    )
    kinds = [e.kind for e in led.all()]

    assert result.parked_by == "db-architect-leader", (
        f"the chore did not park a dropped table; it returned "
        f"parked_by={result.parked_by!r}"
    )
    assert "item.parked" in kinds
    assert "plan.proposed" not in kinds, (
        "a plan was proposed on top of an irreversible migration"
    )
    assert result.written is False
    assert result.card is None, "an approval card was minted for a dropped table"


def test_the_whole_chore_still_completes_a_clean_change():
    """Same counterweight at the chore level: a fix that parked everything
    would satisfy the test above."""
    led = InMemoryLedger()
    result = run_chore(
        CLEAN_PR, led, run_id="clean", analyst=_Says("ok"), approve=lambda c: False
    )

    assert result.parked_by is None, f"a clean change was parked: {result.parked_reason}"
    assert "specialist.response" in {e.kind for e in led.all()}
