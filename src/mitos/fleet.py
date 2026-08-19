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
from typing import Any, Callable, Optional

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
)

SCHEMA_SIGNALS = (
    re.compile(r"^\+\s*(?:private|public|protected)\s+\w+\s+\w+;", re.M),
    re.compile(r"^\+\s*ALTER\s+TABLE\b", re.M | re.I),
    re.compile(r"^\+\s*CREATE\s+(?:TABLE|INDEX)\b", re.M | re.I),
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


@dataclass
class SpecialistOutput:
    companion: str
    fragment: str
    paths_read: list[str]
    findings: list[str] = field(default_factory=list)


Specialist = Callable[[PullRequest, list[Signal]], SpecialistOutput]


def _schema_specialist(pr: PullRequest, signals: list[Signal]) -> SpecialistOutput:
    hits = [s for s in signals if s.name == "schema-change"]
    lines = [f"## Schema impact, PR {pr.number}", ""]
    for s in hits:
        lines.append(f"- `{s.path}` changes the shape of the record: `{s.evidence}`")
    lines.append("")
    lines.append(
        "The customer document and its table move together in this diff, so any "
        "consumer reading the old shape needs the spec updated before merge."
    )
    return SpecialistOutput(
        companion="db-architect-leader",
        fragment="\n".join(lines),
        paths_read=[s.path for s in hits],
    )


def _doc_specialist(pr: PullRequest, signals: list[Signal]) -> SpecialistOutput:
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
    return SpecialistOutput(
        companion="documentation-companion",
        fragment="\n".join(lines),
        paths_read=paths,
    )


def _compliance_specialist(
    pr: PullRequest, signals: list[Signal]
) -> SpecialistOutput:
    hits = [s for s in signals if s.name == "personal-data"]
    lines = [f"## Data protection, PR {pr.number}", ""]
    findings = []
    for s in hits:
        finding = (
            f"personal data field added in `{s.path}` with no retention entry "
            f"in the register"
        )
        findings.append(finding)
        lines.append(f"- {finding}")
    lines.append("")
    lines.append(
        "A contact field used for outbound notification needs a lawful basis and "
        "a retention period recorded before the column ships."
    )
    return SpecialistOutput(
        companion="compliance-companion",
        fragment="\n".join(lines),
        paths_read=[s.path for s in hits],
        findings=findings,
    )


SPECIALISTS: dict[str, Specialist] = {
    "db-architect-leader": _schema_specialist,
    "documentation-companion": _doc_specialist,
    "compliance-companion": _compliance_specialist,
}


def run_specialist(
    name: str, pr: PullRequest, signals: list[Signal]
) -> Optional[SpecialistOutput]:
    fn = SPECIALISTS.get(name)
    if fn is None:
        return None
    return fn(pr, signals)
