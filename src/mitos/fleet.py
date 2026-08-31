"""The fleet, and the catalog that makes it a fleet rather than a pipeline.

The track asks for agents "cataloged for cross-department use". So the catalog is
a queryable record, not a table in a README: the router reads it to decide who to
wake, which means adding a companion to the catalog changes behaviour and
deleting one is immediately visible in what the fleet does.

Five companions are productised here, out of the 58 ARKON definitions. The rest
are deliberately out of scope, because a fleet that wakes everything is not
making a decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from .envelope import Response, Status
from .fixtures import PullRequest


@dataclass(frozen=True)
class Companion:
    """One catalogued agent. `department` is what makes it cross-department."""

    name: str
    department: str
    role: str
    wakes_on: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "department": self.department,
            "role": self.role,
            "wakes_on": list(self.wakes_on),
            "reads": list(self.reads),
            "writes": list(self.writes),
        }


CATALOG: tuple[Companion, ...] = (
    Companion(
        name="architect-leader",
        department="Architecture",
        role="router",
        wakes_on=("*",),
        reads=("pull-request",),
    ),
    Companion(
        name="db-architect-leader",
        department="Data",
        role="schema impact",
        wakes_on=("schema-change",),
        reads=("migration", "model"),
    ),
    Companion(
        name="documentation-companion",
        department="Documentation",
        role="spec drift",
        wakes_on=("schema-change", "spec-touched"),
        reads=("spec", "model"),
        writes=("spec-repo",),
    ),
    Companion(
        name="compliance-companion",
        department="Data Protection",
        role="personal-data finding",
        wakes_on=("personal-data",),
        reads=("model", "migration", "spec"),
    ),
    Companion(
        name="evaluator-companion",
        department="Quality Gate",
        role="critic",
        wakes_on=("draft-produced",),
        reads=("draft",),
    ),
)

BY_NAME = {c.name: c for c in CATALOG}

# What the router looks for in a diff. Each signal is evidence-bearing: the
# router records which hunk raised it, so the dispatch decision is auditable
# rather than asserted.
PERSONAL_DATA_TERMS = (
    "mobile",
    "phone",
    "email",
    "address",
    "birth",
    "national_id",
    "tax_id",
) + tuple(
    # Article 9 terms are personal data too. Without them here the router never
    # wakes compliance for a special-category field, so the refusal below could
    # never fire. The backlog is what exposed that.
    t for t in (
        "health",
        "biometric",
        "genetic",
        "ethnic",
        "religio",
        "political",
        "union_member",
        "sexual",
    )
)

DESTRUCTIVE = re.compile(
    r"^\+\s*(?:ALTER\s+TABLE\s+\w+\s+DROP\s+COLUMN|DROP\s+TABLE|TRUNCATE)\b",
    re.M | re.I,
)

# GDPR Article 9. The fleet may report ordinary personal data; it must not
# decide anything about these.
SPECIAL_CATEGORY = (
    "health",
    "biometric",
    "genetic",
    "ethnic",
    "religio",
    "political",
    "union_member",
    "sexual",
)

SCHEMA_SIGNALS = (
    re.compile(r"^\+\s*(?:private|public|protected)\s+\w+\s+\w+;", re.M),
    re.compile(r"^\+\s*ALTER\s+TABLE\b", re.M | re.I),
    re.compile(r"^\+\s*CREATE\s+(?:TABLE|INDEX)\b", re.M | re.I),
    # A bare DROP raised no signal at all, so a destructive migration woke
    # nobody and the item was counted as "nothing to do". A destructive change
    # being invisible is worse than one that parks. Found by running the
    # backlog, which is the entire reason for running a backlog.
    re.compile(r"^\+\s*DROP\s+(?:TABLE|COLUMN)\b", re.M | re.I),
)


@dataclass
class Signal:
    name: str
    evidence: str
    path: str


@dataclass
class Dispatch:
    signals: list[Signal] = field(default_factory=list)
    woken: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signals": [
                {"name": s.name, "path": s.path, "evidence": s.evidence}
                for s in self.signals
            ],
            "woken": self.woken,
            "skipped": self.skipped,
        }


def detect_signals(pr: PullRequest) -> list[Signal]:
    """Read the diff and say what it is. This is the branch point."""
    signals: list[Signal] = []
    for f in pr.files:
        patch, path = f["patch"], f["path"]

        for rx in SCHEMA_SIGNALS:
            m = rx.search(patch)
            if m:
                signals.append(
                    Signal("schema-change", m.group(0).strip(), path)
                )
                break

        added = "\n".join(
            ln for ln in patch.splitlines() if ln.startswith("+")
        ).lower()
        for term in PERSONAL_DATA_TERMS:
            if term in added:
                line = next(
                    (
                        ln.strip()
                        for ln in patch.splitlines()
                        if ln.startswith("+") and term in ln.lower()
                    ),
                    term,
                )
                signals.append(Signal("personal-data", line, path))
                break

        if path.endswith(".md") and "/specs/" in path:
            signals.append(Signal("spec-touched", f"spec edited in {path}", path))

    # Deduplicate by (name, path) while keeping order, so a file that trips the
    # same signal twice does not inflate the dispatch.
    seen = set()
    out = []
    for s in signals:
        key = (s.name, s.path)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def route(pr: PullRequest) -> Dispatch:
    """Decide who wakes. Different diffs wake different fleets."""
    signals = detect_signals(pr)
    names = {s.name for s in signals}

    woken, skipped = [], []
    for companion in CATALOG:
        if companion.role in ("router", "critic"):
            continue
        if names.intersection(companion.wakes_on):
            woken.append(companion.name)
        else:
            skipped.append(companion.name)

    return Dispatch(signals=signals, woken=woken, skipped=skipped)


# --------------------------------------------------------------------------
# Specialists. Each returns a draft fragment plus the paths it actually read,
# which is what the evaluator checks citations against.
# --------------------------------------------------------------------------


Specialist = Callable[[PullRequest, list[Signal]], Response]


def _schema_specialist(pr: PullRequest, signals: list[Signal]) -> Response:
    hits = [s for s in signals if s.name == "schema-change"]

    # An irreversible migration is not something to approve on the fleet's own
    # authority. Dropping a column cannot be undone by merging a revert.
    for f in pr.files:
        m = DESTRUCTIVE.search(f["patch"])
        if m:
            return Response(
                companion="db-architect-leader",
                status=Status.BLOCKED,
                reason=(
                    f"irreversible migration in {f['path']}: "
                    f"`{m.group(0).strip()}`. Data removed by this cannot be "
                    f"restored by reverting the pull request, so a human owner "
                    f"has to decide, not the fleet"
                ),
                citations=[f["path"]],
                paths_read=[f["path"]],
                confidence=1.0,
            )
    lines = [f"## Schema impact, PR {pr.number}", ""]
    for s in hits:
        lines.append(f"- `{s.path}` changes the shape of the record: `{s.evidence}`")
    lines.append("")
    lines.append(
        "The customer document and its table move together in this diff, so any "
        "consumer reading the old shape needs the spec updated before merge."
    )
    return Response(
        companion="db-architect-leader",
        status=Status.OK,
        assessment="\n".join(lines),
        # An empty string is not a citation, and a model-raised signal has no
        # path. Dropping it leaves an honest list rather than one that looks
        # populated and points nowhere.
        paths_read=[s.path for s in hits if s.path],
        citations=[s.path for s in hits if s.path] or pr.paths(),
        confidence=0.9,
    )


def _doc_specialist(pr: PullRequest, signals: list[Signal]) -> Response:
    spec_files = [f for f in pr.files if f["path"].endswith(".md")]
    paths = [f["path"] for f in pr.files]
    lines = [f"## Specification drift, PR {pr.number}", ""]
    if spec_files:
        # The generator faithfully carries the spec prose forward. The planted
        # instruction rides along with it, which is exactly what the gate is for.
        for f in spec_files:
            body = "\n".join(
                ln[1:] for ln in f["patch"].splitlines() if ln.startswith("+")
            )
            lines.append(f"Existing spec text from `{f['path']}`:")
            lines.append("")
            lines.append(body.strip())
            lines.append("")
    else:
        lines.append(
            "No spec was touched in this diff, so the spec is now behind the code."
        )
        lines.append("")
    # And the config hunk gets quoted for context, which carries the planted
    # credential forward.
    for f in pr.files:
        if f["path"].endswith((".yml", ".yaml")):
            body = "\n".join(
                ln[1:] for ln in f["patch"].splitlines() if ln.startswith("+")
            )
            lines.append(f"New configuration in `{f['path']}`:")
            lines.append("")
            lines.append("```yaml")
            lines.append(body.strip())
            lines.append("```")
            lines.append("")
    return Response(
        companion="documentation-companion",
        status=Status.OK,
        assessment="\n".join(lines),
        paths_read=paths,
        citations=paths,
        # Lower confidence when no specification was touched: the companion is
        # reporting drift it inferred rather than drift it can point at.
        confidence=0.8 if spec_files else 0.6,
    )


def _compliance_specialist(pr: PullRequest, signals: list[Signal]) -> Response:
    hits = [s for s in signals if s.name == "personal-data"]

    # Ordinary personal data the fleet can assess and report. Special-category
    # data under GDPR Article 9 is different in kind: it needs a Data Protection
    # Impact Assessment and a named owner's decision, and no amount of reading
    # the diff produces one. So the fleet refuses rather than producing a
    # confident answer about something it is not entitled to decide.
    special = [
        (h, term)
        for h in hits
        for term in SPECIAL_CATEGORY
        if term in h.evidence.lower() or term in h.path.lower()
    ]
    if special:
        hit, term = special[0]
        # A signal the model raised carries no path: it classified the change,
        # not a file. Rendered naively that produced the most serious refusal
        # this product makes, the one that requires a Data Protection Impact
        # Assessment, with empty backticks where the filename should be. Seen in
        # a live run as:
        #
        #     `` introduces what looks like special-category data under GDPR
        #
        # A refusal a human cannot act on is barely better than no refusal, so
        # it names the change when it cannot name a file, and cites the files
        # that changed so there is something to open either way.
        where = f"`{hit.path}`" if hit.path else "this change"
        cites = [hit.path] if hit.path else pr.paths()
        return Response(
            companion="compliance-companion",
            status=Status.BLOCKED,
            reason=(
                f"{where} introduces what looks like special-category data "
                f"under GDPR Article 9 (matched on {term!r}). That requires a "
                f"Data Protection Impact Assessment and a named owner's "
                f"decision, which cannot be derived from a diff"
            ),
            findings=[f"special-category data added in {where}"],
            citations=cites,
            paths_read=[h.path for h in hits],
            confidence=0.7,
        )
    lines = [f"## Data protection, PR {pr.number}", ""]
    findings = []
    for s in hits:
        # Same empty-path problem as the Article 9 branch above: a signal the
        # model raised classifies the change rather than a file, and rendering
        # it naively produced findings reading "added in `` with no retention
        # entry" and a citation list containing one empty string.
        finding = (
            f"personal data field added in "
            f"{f'`{s.path}`' if s.path else 'this change'} with no retention "
            f"entry in the register"
        )
        findings.append(finding)
        lines.append(f"- {finding}")
    lines.append("")
    lines.append(
        "A contact field used for outbound notification needs a lawful basis and "
        "a retention period recorded before the column ships."
    )
    return Response(
        companion="compliance-companion",
        status=Status.OK,
        assessment="\n".join(lines),
        # An empty string is not a citation, and a model-raised signal has no
        # path. Dropping it leaves an honest list rather than one that looks
        # populated and points nowhere.
        paths_read=[s.path for s in hits if s.path],
        citations=[s.path for s in hits if s.path] or pr.paths(),
        findings=findings,
        confidence=0.9,
    )


SPECIALISTS: dict[str, Specialist] = {
    "db-architect-leader": _schema_specialist,
    "documentation-companion": _doc_specialist,
    "compliance-companion": _compliance_specialist,
}


class Analyst(Protocol):
    """Produces a specialist's assessment. Implemented by the deterministic
    templates below and by `gemini.GeminiAnalyst`."""

    def assess(self, companion: str, pr: PullRequest, signals: list[Signal]) -> dict: ...


def run_specialist(
    name: str,
    pr: PullRequest,
    signals: list[Signal],
    analyst: Optional[Analyst] = None,
) -> Optional[Response]:
    """Run one companion.

    With no analyst this is the deterministic template path: no credential, no
    network, identical output every time. That is what CI runs and what the
    recorded demo replays, and it is why the video's rejection happens on every
    take rather than whenever the model misbehaves.

    With an analyst, the same companion is a Gemini agent reading the same diff.
    The engine used is recorded in the ledger either way, so a reader can always
    tell which produced a given assessment.
    """
    if name not in SPECIALISTS:
        return None

    # The deterministic specialist runs first, always, and its verdict is the
    # floor.
    #
    # It used to run only when there was no analyst: the model path returned
    # `SPECIALISTS[name]` never having been called. So in production, where an
    # analyst is always configured, the deterministic rules did not execute at
    # all and the model's answer was the whole answer. A pull request dropping
    # a table was refused offline and accepted live, which is the opposite of
    # ADR-002 and was asserted to be impossible by a comment three lines above
    # the code that made it possible.
    #
    # Reproduced before it was changed:
    #
    #     deterministic          -> blocked | irreversible migration in ...
    #     with a model saying ok -> ok
    #
    # The two refusals this lost are the two that most need a human: an
    # irreversible migration, and special-category data under GDPR Article 9.
    deterministic = SPECIALISTS[name](pr, signals)
    if analyst is None:
        return deterministic

    if deterministic.parks_the_item:
        # The model is not consulted for a verdict it is not allowed to change.
        # Asking and discarding the answer costs a request and invites a later
        # edit that "uses" what came back.
        return deterministic

    try:
        data = analyst.assess(name, pr, signals)
    except Exception as exc:  # noqa: BLE001 - degradation is a recorded outcome
        # ADR-002: an unreachable model contributes nothing. The deterministic
        # verdict stands rather than being replaced by a guess, and the failure
        # is recorded as a finding instead of vanishing, because a run that
        # silently skipped half its analysis should not read as a clean one.
        return _with_model_contribution(
            deterministic,
            findings=[
                f"the model was unreachable for {name} "
                f"({type(exc).__name__}); this verdict is the deterministic "
                f"rules alone"
            ],
        )

    if not isinstance(data, dict):
        return _with_model_contribution(
            deterministic,
            findings=[
                f"the model returned {type(data).__name__} rather than a "
                f"result for {name}; this verdict is the deterministic rules "
                f"alone"
            ],
        )

    # Union, not replacement. `blocked` from either side blocks; anything the
    # model says that is not a recognised refusal adds findings and cannot
    # subtract a verdict. An absent or unrecognised status contributes no
    # verdict at all, which is the only reading that cannot quietly clear one:
    # the previous code defaulted a missing status to `ok`.
    reported = str(data.get("status", "")).strip().lower()
    model_blocks = reported == "blocked"
    reason = str(data.get("reason", "")).strip()
    if model_blocks and not reason:
        reason = f"{name} blocked without giving a reason"

    findings = list(deterministic.findings)
    for item in data.get("findings", []) or []:
        text = str(item)
        if text not in findings:
            findings.append(text)

    return Response(
        companion=name,
        status=Status.BLOCKED if model_blocks else deterministic.status,
        assessment=data.get("assessment", "") or deterministic.assessment,
        paths_read=_union(deterministic.paths_read, data.get("paths_read"), pr.paths()),
        citations=_union(
            deterministic.citations,
            data.get("citations") or data.get("paths_read"),
            pr.paths(),
        ),
        findings=findings,
        reason=reason or deterministic.reason,
        confidence=float(data.get("confidence", 0.8) or 0.8),
        read_log=data.get("read_log") or {},
    )


def _union(*groups: Optional[list[str]]) -> list[str]:
    """Order-preserving union, so a citation from either side is never dropped."""
    out: list[str] = []
    for group in groups:
        for item in group or []:
            if item not in out:
                out.append(item)
    return out


def _with_model_contribution(base: Response, *, findings: list[str]) -> Response:
    """The deterministic verdict, plus a note that the model added nothing.

    ADR-014's rule applied here: a step that could not be evaluated is recorded
    as such rather than counted as clean.
    """
    return Response(
        companion=base.companion,
        status=base.status,
        assessment=base.assessment,
        findings=list(base.findings) + [f for f in findings if f not in base.findings],
        paths_read=list(base.paths_read),
        citations=list(base.citations),
        confidence=base.confidence,
        reason=base.reason,
        read_log=dict(base.read_log),
    )


def route_with_model(pr: PullRequest, classifier=None) -> tuple[Dispatch, dict]:
    """Deterministic routing, optionally widened by a model. Never narrowed.

    THE INVARIANT, and it is the same one the evaluator's critic obeys:

        the model can only TIGHTEN.

    Its signals are unioned with the deterministic ones and it cannot remove
    any, so a wrong or compromised classifier can make the fleet wake more
    companions and be more cautious. It can never make it wake fewer.

    Disagreement is returned rather than resolved, so a reader months later can
    see that the model spotted something the patterns missed, or that it
    invented something they correctly ignored.
    """
    base = route(pr)
    if classifier is None:
        return base, {}

    try:
        proposal = classifier.classify(pr)
    except Exception as exc:  # noqa: BLE001 - degradation is the behaviour
        # Union-only means an absent model contributes nothing, so the
        # deterministic dispatch stands untouched. Recorded rather than
        # swallowed: a run where the model was unreachable and a run where
        # it agreed are different runs, and the thread should say which.
        return base, {
            "model_reached": False,
            "why": f"{type(exc).__name__}: {str(exc)[:160]}",
            "effect": "none; the deterministic dispatch is unchanged",
        }
    deterministic = {s.name for s in base.signals}
    proposed = set(proposal.get("signals", []))

    # Union only. There is deliberately no branch that discards a deterministic
    # signal because the model did not report it.
    added = sorted(proposed - deterministic)
    missed = sorted(deterministic - proposed)

    signals = list(base.signals)
    for name in added:
        signals.append(
            Signal(name, f"raised by the model: {proposal.get('rationale', '')}", "")
        )

    # A model saying "this is Article 9" is enough to wake compliance. A model
    # saying it is NOT cannot stop the deterministic rule from firing.
    if proposal.get("special_category") and "personal-data" not in {
        s.name for s in signals
    }:
        signals.append(
            Signal("personal-data", "the model classified this as Article 9 data", "")
        )
        added.append("personal-data")

    names = {s.name for s in signals}
    woken, skipped = [], []
    for companion in CATALOG:
        if companion.role in ("router", "critic"):
            continue
        (woken if names.intersection(companion.wakes_on) else skipped).append(
            companion.name
        )

    divergence = {
        "model_added": added,
        "model_missed": missed,
        "special_category": proposal.get("special_category", False),
        "rationale": proposal.get("rationale", ""),
        "agreed": not added and not missed,
    }
    return Dispatch(signals=signals, woken=woken, skipped=skipped), divergence
