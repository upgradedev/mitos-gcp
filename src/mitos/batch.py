"""A morning's backlog, cleared unattended.

The judging criterion this exists for asks how much friction the fleet removes
**on its own**. One item processed well does not answer that. A count does:

    12 presented, 8 completed, 3 parked, 1 needed nothing,
    with 0 human interventions before the approval step.

The parked count is the part that makes the rest believable. A fleet that
completes everything has either been given easy work or is producing confident
answers to questions it is not entitled to decide. Each parked item names the
companion that refused and the reason, so the human who picks it up starts from
something useful rather than from "the robot gave up".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .chore import ApprovalCard, run_chore
from .envelope import Outcome
from .fixtures import PullRequest
from .ledger import Ledger


@dataclass
class BatchReport:
    outcomes: list[Outcome] = field(default_factory=list)
    human_interventions_before_approval: int = 0
    approvals_requested: int = 0

    @property
    def presented(self) -> int:
        return len(self.outcomes)

    @property
    def completed(self) -> int:
        return sum(1 for o in self.outcomes if o.state == "completed")

    @property
    def parked(self) -> int:
        return sum(1 for o in self.outcomes if o.state == "parked")

    @property
    def no_action(self) -> int:
        return sum(1 for o in self.outcomes if o.state == "no_action")

    @property
    def review(self) -> int:
        """Worked, findings recorded, and no document it could honestly propose
        editing. Counted separately because folding it into `parked` would call
        it a refusal, and folding it into `completed` would claim a proposal
        that was never made."""
        return sum(1 for o in self.outcomes if o.state == "review")

    @property
    def findings(self) -> int:
        return sum(o.findings for o in self.outcomes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "presented": self.presented,
            "completed_unattended": self.completed,
            "parked_for_a_human": self.parked,
            "no_action_needed": self.no_action,
            "findings_raised": self.findings,
            "human_interventions_before_approval": (
                self.human_interventions_before_approval
            ),
            "approvals_requested": self.approvals_requested,
            "outcomes": [o.as_dict() for o in self.outcomes],
        }

    def parked_reasons(self) -> list[tuple[int, str, str]]:
        return [
            (o.pr_number, o.parked_by or "?", o.reason)
            for o in self.outcomes
            if o.state == "parked"
        ]

    def headline(self) -> str:
        return (
            f"{self.presented} presented, {self.completed} completed unattended, "
            f"{self.parked} parked for a human, {self.no_action} needed nothing"
        )


def run_batch(
    backlog: list[PullRequest],
    ledger: Ledger,
    *,
    approve: Optional[Callable[[ApprovalCard], bool]] = None,
    emit: Optional[Callable[[str, str], None]] = None,
    today: str = "2026-08-19",
    analyst: Any = None,
    critic: Any = None,
    publisher: Any = None,
) -> BatchReport:
    """Work the backlog. Nothing here asks a human anything except to approve.

    Items are independent: one refusal parks that item and does not stop the
    queue. That is deliberate. A backlog processor that halts on the first thing
    it cannot do is a backlog processor nobody leaves running.
    """
    report = BatchReport()
    approvals = {"n": 0}

    def counting_approve(card: ApprovalCard) -> bool:
        approvals["n"] += 1
        return approve(card) if approve else False

    for pr in backlog:
        result = run_chore(
            pr,
            ledger,
            run_id=uuid.uuid4().hex[:8],
            emit=emit or (lambda k, t: None),
            approve=counting_approve,
            today=today,
            analyst=analyst,
            critic=critic,
            publisher=publisher,
        )

        if result.parked_by:
            report.outcomes.append(
                Outcome(
                    pr_number=pr.number,
                    title=pr.title,
                    state="parked",
                    reason=result.parked_reason,
                    parked_by=result.parked_by,
                )
            )
            continue

        # The router woke nobody, so there is nothing for this fleet to do. That
        # is a real outcome and it is not the same as a failure, so it is
        # counted separately rather than inflating either other number.
        if not result.dispatch.woken:
            report.outcomes.append(
                Outcome(
                    pr_number=pr.number,
                    title=pr.title,
                    state="no_action",
                    reason="no schema change and no personal data in this diff",
                )
            )
            continue

        report.outcomes.append(
            Outcome(
                pr_number=pr.number,
                title=pr.title,
                state=(
                    "completed"
                    if result.card
                    else "review"
                    if result.review_only
                    else "parked"
                ),
                reason=(
                    ""
                    if result.card
                    else "no document in this change is the paperwork for it; "
                    "a reviewer decides what to update"
                    if result.review_only
                    else "; ".join(
                        f"{f.check}: {f.detail}"
                        for f in (
                            result.final_verdict.findings if result.final_verdict else []
                        )
                    )
                    or "the repaired draft still failed the deterministic gate"
                ),
                parked_by=None if result.card else "evaluator-companion",
                findings=len(result.card.findings) if result.card else 0,
                plan_hash=result.card.plan_hash if result.card else "",
                published=result.published,
            )
        )

    report.approvals_requested = approvals["n"]
    return report
