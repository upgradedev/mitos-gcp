"""The gate.

Productised from the ARKON `evaluator-companion`, which is a critic that sits
between every generator and anything that reaches a real system. Two of its
checks are auto-FAIL there and they are auto-FAIL here: a leaked credential and a
guardrail bypass. Its anti-manipulation rule is here too, because a generator's
output can carry an injection that was planted upstream in the diff.

Everything in this module is deterministic. No model decides whether a draft
passes, which is the point: a gate whose verdict is produced by the same class of
thing it is gating can be argued out of its verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

# Credential shapes. Deliberately narrow: a detector that fires on any long
# string produces noise, and a gate that cries wolf gets widened by the next
# person, which is how gates die.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("azure-service-bus-connection-string", re.compile(r"Endpoint=sb://", re.I)),
    ("shared-access-key", re.compile(r"SharedAccessKey\s*=\s*\S+", re.I)),
    ("account-key", re.compile(r"AccountKey\s*=\s*\S+", re.I)),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}")),
    (
        "password-in-url",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]+@", re.I),
    ),
]

# Instructions aimed at the reader rather than statements about the system.
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("override-previous-instructions", re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I)),
    ("role-reassignment", re.compile(r"\byour\s+new\s+task\s+is\b", re.I)),
    ("verdict-injection", re.compile(r"\b(?:output|reply\s+with|respond\s+with)\s+APPROVED\b", re.I)),
    ("gate-skip", re.compile(r"\bskip\s+the\s+(?:compliance|security|evaluation)\s+check\b", re.I)),
    ("self-approval", re.compile(r"\b(?:rate|mark|score)\s+this\s+as\s+(?:PASS|APPROVED)\b", re.I)),
    ("pre-approved-claim", re.compile(r"\bthis\s+spec\s+is\s+already\s+approved\b", re.I)),
]

# Guardrail bypasses, verbatim from the ARKON evaluator's auto-FAIL list.
BYPASS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("force-deploy", re.compile(r"\bforce[- ]deploy\b", re.I)),
    ("bypass-approval", re.compile(r"\bbypass[- ]approval\b", re.I)),
    ("skip-tests", re.compile(r"\bskip[- ]tests\b", re.I)),
    ("no-verify", re.compile(r"--no-verify\b")),
    ("destructive-sql", re.compile(r"\b(?:drop\s+database|truncate\s+table)\b", re.I)),
]


@dataclass
class Finding:
    severity: str
    check: str
    detail: str
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "check": self.check,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class Verdict:
    passed: bool
    findings: list[Finding] = field(default_factory=list)
    # What the model critic raised. Deliberately separate from `findings`, and
    # deliberately not part of `passed`. See `_with_critic`.
    advisories: list[Finding] = field(default_factory=list)
    injection_attempt: bool = False
    checked: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "injection_attempt": self.injection_attempt,
            "checks_run": self.checked,
            "findings": [f.as_dict() for f in self.findings],
            "advisories": [f.as_dict() for f in self.advisories],
        }

    def summary(self) -> str:
        if self.passed:
            extra = (
                f", {len(self.advisories)} advisory for the human"
                if self.advisories
                else ", no findings"
            )
            return f"PASS, {len(self.checked)} checks{extra}"
        worst = ", ".join(sorted({f.check for f in self.findings}))
        return f"FAIL on {worst}"


def _redact(text: str, match: re.Match[str]) -> str:
    """Quote enough of the hit to be actionable without reprinting the secret."""
    start = max(0, match.start() - 24)
    head = text[start : match.start()]
    hit = match.group(0)
    shown = hit[:12] + "…redacted…" if len(hit) > 12 else hit
    return (head + shown).replace("\n", " ").strip()


def _scan(text: str, patterns, severity: str, kind: str) -> list[Finding]:
    out = []
    for name, rx in patterns:
        m = rx.search(text)
        if m:
            out.append(
                Finding(
                    severity=severity,
                    check=kind,
                    detail=name,
                    evidence=_redact(text, m),
                )
            )
    return out


class Critic(Protocol):
    """A second opinion that can only ever make the gate stricter."""

    def review(self, draft: str, already_found: list[str]) -> list[dict[str, str]]: ...


def evaluate(
    draft: str,
    *,
    known_paths: list[str] | None = None,
    critic: Critic | None = None,
) -> Verdict:
    """Run every check on a generator draft and return one verdict.

    `known_paths` is the set of files the fleet actually read. A draft citing
    anything outside it is a hallucinated reference, which is the ARKON
    evaluator's check 3.

    `critic` is an optional model-backed reviewer. Read `_with_critic` before
    changing anything about how its result is combined: the invariant that it
    can only add findings is the reason a model is allowed near this gate at
    all.
    """
    checks = [
        "secret-leak",
        "prompt-injection",
        "guardrail-bypass",
        "hallucinated-path",
        "non-empty",
    ]
    findings: list[Finding] = []

    findings += _scan(draft, SECRET_PATTERNS, "CRITICAL", "secret-leak")
    injection = _scan(draft, INJECTION_PATTERNS, "CRITICAL", "prompt-injection")
    findings += injection
    findings += _scan(draft, BYPASS_PATTERNS, "CRITICAL", "guardrail-bypass")

    if known_paths is not None:
        cited = set(re.findall(r"[A-Za-z0-9_./-]+\.(?:java|sql|ya?ml|md|py|ts|json)", draft))
        for path in sorted(cited):
            if not any(known.endswith(path) or path in known for known in known_paths):
                findings.append(
                    Finding(
                        severity="HIGH",
                        check="hallucinated-path",
                        detail=f"cites {path}, which the fleet never read",
                        evidence=path,
                    )
                )

    if not draft.strip():
        findings.append(
            Finding("HIGH", "non-empty", "the draft is empty", "")
        )

    verdict = Verdict(
        passed=not findings,
        findings=findings,
        injection_attempt=bool(injection),
        checked=checks,
    )
    return verdict if critic is None else _with_critic(verdict, draft, critic)


def _with_critic(verdict: Verdict, draft: str, critic: Critic) -> Verdict:
    """Attach the critic's findings to the verdict without letting them gate it.

    THE INVARIANT, unchanged: **the model can only tighten.** It may add, never
    subtract. There is no branch here that removes a deterministic finding,
    clears the injection flag, or turns a failure into a pass.

    What changed, and why, is where its findings land.

    They used to count towards `passed`. That looked stricter and was worse. The
    repair step is mechanical, a regular expression substitution that removes
    leaked credentials and injected instructions, and it cannot act on a
    sentence of judgement. So any critic opinion survived the repair, failed the
    draft a second time, and parked the item permanently with the reason "the
    gate could not be satisfied", which tells the human nothing they can act on.
    Measured on a live backlog: twelve of thirteen items parked, eight of them
    this way.

    A refusal with no actionable reason is the failure this project banned
    elsewhere and reintroduced here through a different door.

    So the deterministic checks gate, because they describe a draft that is
    unsafe to write and they can be repaired. The critic's findings are
    judgements, and judgements belong in front of the human who is already
    standing at the approval step. They ride on the approval card, and the human
    weighs them before approving.

    The model still cannot approve anything, cannot clear a finding, and cannot
    reduce what a human is shown. It can only add to it.
    """
    extra_raw = critic.review(draft, [f.detail for f in verdict.findings])
    advisories = [
        Finding(
            severity="ADVISORY",
            check="model-critic",
            detail=item.get("detail", ""),
            evidence=item.get("evidence", ""),
        )
        for item in extra_raw
        if item.get("detail")
    ]
    return Verdict(
        passed=verdict.passed,
        findings=verdict.findings,
        advisories=verdict.advisories + advisories,
        injection_attempt=verdict.injection_attempt,
        checked=[*verdict.checked, "model-critic"],
    )


def redact_for_repair(draft: str) -> str:
    """Strip exactly what the gate objected to, so the second pass is a repair
    and not a re-roll.

    Kept mechanical on purpose. If repair were a second model call, a demo could
    pass on one take and fail on the next, and the whole point of putting the
    poison in the fixture was to stop that.
    """
    out = draft
    for _, rx in SECRET_PATTERNS:
        out = rx.sub("[secret removed by mitos-evaluator]", out)
    for _, rx in INJECTION_PATTERNS:
        out = rx.sub("[instruction addressed to the agent, discarded]", out)
    for _, rx in BYPASS_PATTERNS:
        out = rx.sub("[guardrail bypass removed]", out)
    return out
