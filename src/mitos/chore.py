"""The chore, end to end.

A pull request lands carrying a schema change to a field that holds personal
data. The fleet works out what downstream has drifted, remembers what it already
decided about that subject, and returns one content-addressed diff for a human to
approve. Everything before the approval is unattended.

The write is the only step that is not autonomous, and it is last on purpose: a
demo that stalls for a human in the middle is not showing autonomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import fleet
from .envelope import Outcome, Response, Status
from .evaluator import Verdict, evaluate, redact_for_repair
from .fixtures import PullRequest
from .guard import ROLE_READER, ROLE_WRITER, is_allowed
from .ledger import Entry, Ledger, content_hash
from .spec_repo import NullSpecRepo, SpecRepo

Emit = Callable[[str, str], None]


def _noop(kind: str, text: str) -> None:  # pragma: no cover - default sink
    pass


@dataclass
class ApprovalCard:
    """What a human sees, and the only thing the writer will act on."""

    run_id: str
    pr_number: int
    target_path: str
    body: str
    findings: list[str] = field(default_factory=list)
    # What the model critic raised. Not gating, but the human approving this
    # plan is the right person to weigh it, so it is on the card.
    advisories: list[str] = field(default_factory=list)
    plan_hash: str = ""

    def compute_hash(self) -> str:
        self.plan_hash = content_hash(
            {"pr": self.pr_number, "path": self.target_path, "body": self.body}
        )
        return self.plan_hash


@dataclass
class ChoreResult:
    run_id: str
    pr_number: int
    dispatch: fleet.Dispatch
    recalled: list[Entry]
    first_verdict: Verdict
    final_verdict: Optional[Verdict]
    card: Optional[ApprovalCard]
    # Two different claims, deliberately not one flag.
    # `written`   the governed write passed all three checks and executed.
    # `published` bytes actually landed in the specification repository.
    # Offline the first is True and the second is False, and saying "written"
    # for both would be an overclaim in exactly the place a judge looks.
    written: bool
    published: bool = False
    receipt: dict[str, Any] = field(default_factory=dict)
    # Set when a specialist refused. The whole point of letting an agent refuse
    # is that the refusal reaches the reader, so it is a first-class result.
    parked_by: Optional[str] = None
    parked_reason: str = ""
    responses: list[Response] = field(default_factory=list)
    root_entry_id: str = ""
    last_entry_id: str = ""
    escalated: bool = False


# The thread is keyed on the service, not on the field, so two different
# changes to the same service land on the same thread. Keying it per-field would
# give a tidy demo and a useless memory: the whole value is noticing that this
# service has been here before.
SUBJECT = "services/customer"


def run_chore(
    pr: PullRequest,
    ledger: Ledger,
    *,
    run_id: str,
    emit: Emit = _noop,
    approve: Optional[Callable[[ApprovalCard], bool]] = None,
    today: str = "2026-08-19",
    analyst: Any = None,
    critic: Any = None,
    publisher: Optional[SpecRepo] = None,
    classifier: Any = None,
    doc_agent: Any = None,
) -> ChoreResult:
    """Run the whole chore. `emit` is how the demo narrates it; the logic does
    not depend on anything being watched."""

    def record(kind: str, actor: str, payload: dict[str, Any], parent: Optional[str]) -> Entry:
        return ledger.append(
            Entry(
                kind=kind,
                actor=actor,
                subject=SUBJECT,
                payload=payload,
                parent_id=parent,
                run_id=run_id,
            )
        )

    # 1. The trigger. Nobody opened Mitos; a webhook did this.
    root = record(
        "trigger.pull_request",
        "webhook",
        {"pr": pr.number, "title": pr.title, "files": pr.paths()},
        None,
    )
    emit("trigger", f"PR {pr.number} — {pr.title}\n  {len(pr.files)} files from {pr.author}")

    # 2. The branch point. A model may widen it and can never narrow it.
    dispatch, divergence = fleet.route_with_model(pr, classifier)
    cursor = record(
        "fleet.dispatch",
        "architect-leader",
        {**dispatch.as_dict(), "model_divergence": divergence},
        root.entry_id,
    ).entry_id
    if divergence and not divergence.get("agreed", True):
        emit(
            "divergence",
            "the model and the rules disagreed, recorded rather than resolved\n"
            f"  model added   {divergence.get('model_added') or 'nothing'}\n"
            f"  model missed  {divergence.get('model_missed') or 'nothing'}\n"
            f"  the union is what ran; a model can widen, never narrow",
        )
    for s in dispatch.signals:
        emit("signal", f"{s.name:<15} {s.path}\n                  {s.evidence}")
    emit(
        "dispatch",
        f"waking {len(dispatch.woken)} of {len(dispatch.woken) + len(dispatch.skipped)}: "
        + ", ".join(dispatch.woken)
        + (f"\n  skipped: {', '.join(dispatch.skipped)}" if dispatch.skipped else ""),
    )

    # 3. The thread. What do we already know about this subject?
    recalled = ledger.recall(SUBJECT, kinds={"finding.deferred", "finding.raised"})
    escalated = False
    for entry in recalled:
        p = entry.payload
        if entry.kind == "finding.deferred":
            expired = str(p.get("expires_on", "")) < today
            emit(
                "recall",
                f"already seen on {p.get('deferred_on', '?')}: {p.get('finding', '')}\n"
                f"  deferred by {p.get('deferred_by', '?')} until {p.get('expires_on', '?')}"
                + ("  EXPIRED" if expired else ""),
            )
            if expired:
                escalated = True
        else:
            emit(
                "recall",
                f"raised in run {entry.run_id} on {entry.recorded_at[:10]}: "
                f"{p.get('finding', '')}\n  not re-filing it",
            )
    if not recalled:
        emit("recall", "nothing in the thread for this subject yet")
    if escalated:
        cursor = record(
            "finding.escalated",
            "compliance-companion",
            {"reason": "the deferral expired and the same subject changed again"},
            cursor,
        ).entry_id
        emit("escalate", "the deferral has expired, escalating instead of re-filing")

    # 4. The specialists. Which engine produced each assessment goes in the
    # thread, so nobody has to guess later whether a finding came from a model
    # or from a template.
    engine = getattr(analyst, "model", None) or "template"
    emit("engine", f"specialists running on {engine}")
    fragments, paths_read, findings = [], [], []
    responses: list[Response] = []
    for name in dispatch.woken:
        out = fleet.run_specialist(name, pr, dispatch.signals, analyst=analyst)
        if out is None:
            continue
        responses.append(out)
        # The read log is the evidence of agency. A fixed pipeline produces the
        # same sequence on every item; an agent that decides what to open
        # produces a different one, and the difference is inspectable here
        # rather than claimed in a README.
        cursor = record(
            "specialist.response",
            name,
            {**out.as_dict(), "engine": engine, "reads": out.read_log},
            cursor,
        ).entry_id
        if out.read_log.get("tool_calls"):
            emit(
                "reads",
                f"{name} chose to open {out.read_log.get('reads', 0)} file(s), "
                f"{out.read_log.get('denied', 0)} refused by the bound",
            )

        if out.parks_the_item:
            # A specialist refused. Nothing downstream runs, because producing
            # a plan on top of a refusal is exactly how an autonomous system
            # ends up confidently wrong.
            emit(
                "parked",
                f"{name} returned {out.status.value}\n  {out.reason}",
            )
            cursor = record(
                "item.parked",
                name,
                {"status": out.status.value, "reason": out.reason},
                cursor,
            ).entry_id
            return ChoreResult(
                run_id, pr.number, dispatch, recalled, Verdict(passed=False),
                None, None, False, False, {}, name, out.reason, responses,
                root.entry_id, cursor, escalated,
            )

        fragments.append(out.assessment)
        paths_read.extend(out.paths_read)
        findings.extend(out.findings)
        emit(
            "specialist",
            f"{name} {out.status.value}, {len(out.assessment)} chars, "
            f"confidence {out.confidence:.2f}",
        )

    already_known = {
        e.payload.get("finding") for e in recalled if e.payload.get("finding")
    }
    fresh = [f for f in findings if f not in already_known]

    draft = "\n\n".join(fragments)
    target = "docs/specs/customer-record.md"

    # What the fleet is entitled to cite: the files in the pull request, plus
    # everything the specialists actually opened from the repository.
    #
    # This used to be the pull request alone, which was correct while
    # specialists only ever saw a diff. Once they began reading the repository
    # the two sets diverged, and every legitimate citation became a
    # hallucination finding: a specialist that read the billing specification
    # and cited it was accused of inventing it. Eight of thirteen items parked
    # that way on a live run.
    #
    # The read log is the ground truth here, which makes the check stronger than
    # it was rather than weaker. A citation is now compared against what was
    # genuinely opened, instead of against whatever happened to be in the diff.
    cited_allowed = sorted(set(pr.paths()) | set(paths_read))

    # 5. The gate. The draft carries whatever was planted in the diff.
    verdict = evaluate(draft, known_paths=cited_allowed, critic=critic)
    cursor = record(
        "evaluator.verdict", "evaluator-companion", verdict.as_dict(), cursor
    ).entry_id
    emit("evaluate", f"draft 1: {verdict.summary()}")
    for f in verdict.findings:
        emit("finding", f"{f.severity:<9} {f.check:<18} {f.detail}\n                  {f.evidence}")

    final_verdict: Optional[Verdict] = None
    card: Optional[ApprovalCard] = None
    written = False

    if not verdict.passed:
        emit("repair", "stripping what the gate objected to and re-submitting")
        draft = redact_for_repair(draft)
        final_verdict = evaluate(draft, known_paths=cited_allowed, critic=critic)
        cursor = record(
            "evaluator.verdict",
            "evaluator-companion",
            final_verdict.as_dict(),
            cursor,
        ).entry_id
        emit("evaluate", f"draft 2: {final_verdict.summary()}")
    else:
        final_verdict = verdict

    if not final_verdict.passed:
        # Name what failed. "The gate could not be satisfied" parks an item and
        # tells the human nothing, which is the failure this project bans
        # everywhere else.
        why = "; ".join(
            f"{f.check}: {f.detail}" for f in final_verdict.findings
        ) or "the repaired draft still fails"
        emit("halt", f"nothing is written. {why}")
        return ChoreResult(
            run_id, pr.number, dispatch, recalled, verdict, final_verdict,
            None, False, False, {}, None, "", responses,
            root.entry_id, cursor, escalated,
        )

    # 5b. The interceptor, exercised in the product path rather than in a test.
    # The documentation companion is handed the write tool and told to use it.
    # ADK consults the guard before dispatch and the tool is never invoked.
    if doc_agent is not None:
        probe = doc_agent.attempt_write(target, draft)
        cursor = record("guard.exercised", "documentation-companion", probe, cursor).entry_id
        if probe.get("denied"):
            emit(
                "guard",
                "the documentation companion asked to write and ADK refused it\n"
                f"  tool actually executed: {probe.get('tool_executed')}\n"
                f"  {probe.get('detail', '')[:110]}",
            )
        elif probe.get("error"):
            emit("guard", f"the guard probe could not run: {probe['error']}")
        else:
            emit("guard", "WARNING: the write tool was reachable from the reader role")

    # 6. The approval card, content-addressed.
    card = ApprovalCard(
        run_id=run_id,
        pr_number=pr.number,
        target_path=target,
        body=draft,
        findings=fresh,
        advisories=[
            f"{f.detail} ({f.evidence})" if f.evidence else f.detail
            for f in (final_verdict.advisories if final_verdict else [])
        ],
    )
    plan_hash = card.compute_hash()
    cursor = record(
        "plan.proposed",
        "documentation-companion",
        {"path": target, "plan_hash": plan_hash, "findings": fresh},
        cursor,
    ).entry_id
    emit(
        "approval",
        f"one write proposed\n  target   {target}\n  sha256   {plan_hash}\n"
        f"  findings {len(fresh)} new"
        + (f", {len(findings) - len(fresh)} already in the thread" if len(findings) != len(fresh) else ""),
    )

    # The reader identity provably cannot do this next part.
    allowed_as_reader, why = is_allowed("write_spec_repo", ROLE_READER)
    emit(
        "identity",
        f"reader may call write_spec_repo: {allowed_as_reader}\n  {why}",
    )

    if approve is None or not approve(card):
        emit("halt", "not approved; nothing is written")
        return ChoreResult(
            run_id, pr.number, dispatch, recalled, verdict, final_verdict,
            card, False, False, {}, None, "", responses,
            root.entry_id, cursor, escalated,
        )

    # 7. The governed write, in the writer identity, against the exact hash.
    receipt = execute_write(
        card, plan_hash, role=ROLE_WRITER, publisher=publisher
    )
    written = True  # execute_write raises rather than returning on refusal
    published = bool(receipt.get("published"))
    cursor = record(
        "write.executed",
        "writer",
        {"path": target, "plan_hash": plan_hash, "approved": True, **receipt},
        cursor,
    ).entry_id
    for finding in fresh:
        cursor = record(
            "finding.raised", "compliance-companion", {"finding": finding}, cursor
        ).entry_id
    if receipt.get("published"):
        emit(
            "write",
            f"pushed to the spec repository as the writer identity\n"
            f"  branch  {receipt.get('branch')}\n"
            f"  commit  {str(receipt.get('commit', ''))[:12]}\n"
            f"  {receipt.get('compare', '')}",
        )
    else:
        emit(
            "write",
            f"plan {plan_hash[:12]} approved, {receipt.get('reason', 'not published')}",
        )

    return ChoreResult(
        run_id, pr.number, dispatch, recalled, verdict, final_verdict,
        card, written, published, receipt, None, "", responses,
        root.entry_id, cursor, escalated,
    )


class PlanHashMismatch(Exception):
    """The writer refuses anything that is not the approved bytes."""


def execute_write(
    card: ApprovalCard,
    approved_hash: str,
    *,
    role: str,
    publisher: Optional[SpecRepo] = None,
) -> dict[str, Any]:
    """The governed write.

    Three independent conditions and all of them have to hold before a byte
    leaves this process.

    The role check is the same policy the ADK interceptor enforces at tool
    dispatch, applied again here so a call arriving by any other path still
    fails closed.

    The hash check means an approval is for exact bytes. Change one character of
    the plan after a human approved it and this refuses, which is the reason the
    approval card shows a sha256 rather than a summary.

    And the credential itself is the third condition, enforced by Google IAM
    rather than by this function: only `mitos-writer` can read the deploy key,
    so a process running as the reader fails here even with the first two checks
    somehow satisfied.
    """
    allowed, why = is_allowed("write_spec_repo", role)
    if not allowed:
        raise PermissionError(why)
    recomputed = content_hash(
        {"pr": card.pr_number, "path": card.target_path, "body": card.body}
    )
    if recomputed != approved_hash:
        raise PlanHashMismatch(
            f"approved {approved_hash[:12]}, got {recomputed[:12]}; refusing"
        )
    repo = publisher or NullSpecRepo()
    return repo.publish(
        path=card.target_path,
        body=card.body,
        message=(
            f"docs(spec): reconcile customer record with PR {card.pr_number}\n\n"
            f"Written by the Mitos fleet after a human approved plan "
            f"{approved_hash[:16]}.\nRun {card.run_id}."
        ),
        branch=f"mitos/pr-{card.pr_number}-{approved_hash[:8]}",
    )


def escalate_on_wake(ledger: Ledger, expired: list[Entry]) -> list[Entry]:
    """What the fleet does when the subscription fires.

    Nobody called an endpoint and nothing was scheduled. A deferral reached its
    expiry date, Firestore handed the reader service a snapshot in which the
    watched set had changed, and the fleet acted.

    Deliberately narrow. Waking is cheap and unattended, so the action taken
    unattended is the smallest useful one: record the escalation against the
    same thread, so the next run of the chore sees it and a human can retrace
    why it happened. It does not write to the specification repository, because
    an unattended wake must never reach the one credential that changes
    something outside the ledger.
    """
    written: list[Entry] = []
    for deferral in expired:
        written.append(
            ledger.append(
                Entry(
                    kind="finding.escalated",
                    actor="compliance-companion",
                    subject=deferral.subject,
                    payload={
                        "reason": "the deferral expired and nobody re-opened it",
                        "deferred_on": deferral.payload.get("deferred_on"),
                        "expires_on": deferral.payload.get("expires_on"),
                        "deferred_by": deferral.payload.get("deferred_by"),
                        "finding": deferral.payload.get("finding"),
                        "woken_by": "firestore-query-subscription",
                    },
                    parent_id=deferral.entry_id,
                    run_id="watch",
                )
            )
        )
    return written
