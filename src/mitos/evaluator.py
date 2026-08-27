"""The gate.

Productised from the ARKON `evaluator-companion`, which is a critic that sits
between every generator and anything that reaches a real system. Two of its
checks are auto-FAIL there and they are auto-FAIL here: a leaked credential and a
guardrail bypass. Its anti-manipulation rule is here too, because a generator's
output can carry an injection that was planted upstream in the diff.

Two things are scanned and they are not the same thing. `evaluate` reads a
generator's draft. `scan_pull_request_for_injection` reads the untrusted input
directly, because a draft only ever contains the parts of the input some
specialist chose to quote.

Everything in this module is deterministic. No model decides whether a draft
passes, which is the point: a gate whose verdict is produced by the same class of
thing it is gating can be argued out of its verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # the gate must not depend at runtime on where its input came from
    from .fixtures import PullRequest

# Credential shapes. Deliberately narrow: a detector that fires on any long
# string produces noise, and a gate that cries wolf gets widened by the next
# person, which is how gates die.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("azure-service-bus-connection-string", re.compile(r"Endpoint=sb://", re.I)),
    ("shared-access-key", re.compile(r"SharedAccessKey\s*=\s*\S+", re.I)),
    ("account-key", re.compile(r"AccountKey\s*=\s*\S+", re.I)),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # `[A-Z ]*` rather than `(?:RSA |EC )?`. The narrow spelling missed
    # `-----BEGIN OPENSSH PRIVATE KEY-----`, which is what `ssh-keygen` writes by
    # default since OpenSSH 7.8, and the one credential this architecture exists
    # to protect is an SSH deploy key. `standards.py` already shipped the wide
    # form, so the repository disagreed with itself about a shape it had already
    # decided was safe to match.
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    # A separate entry rather than a widening of the one above: `gh[pousr]_`
    # cannot match `github_pat_`, so a fine-grained token, which is the kind
    # somebody pastes today, would have walked straight through.
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[0-9A-Za-z_]{22,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
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
    try:
        extra_raw = critic.review(draft, [f.detail for f in verdict.findings])
    except Exception as exc:  # noqa: BLE001 - degradation is the behaviour
        # Not silence. A card that was never reviewed by the critic and a
        # card the critic had nothing to add to look identical unless one
        # of them says so, and the person approving cannot tell.
        return Verdict(
            passed=verdict.passed,
            findings=verdict.findings,
            advisories=verdict.advisories
            + [
                Finding(
                    severity="ADVISORY",
                    check="model-critic",
                    detail=(
                        "the second opinion did not run, so this card carries "
                        "the deterministic findings only"
                    ),
                    evidence=f"{type(exc).__name__}: {str(exc)[:160]}",
                )
            ],
            injection_attempt=verdict.injection_attempt,
            checked=[*verdict.checked, "model-critic-unavailable"],
        )
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


def _hunks(patch: str) -> list[tuple[str, str]]:
    """Split a patch into `(header, text)` pairs, `text` including the header.

    A patch with no `@@` header is one hunk with an empty header rather than
    nothing to scan, because `webhook.to_pull_request` builds `files` from
    whatever the GitHub API returned and the fixtures are not the only shape
    that arrives. A patch with no text at all yields no hunks, which is the
    limit named in `scan_pull_request_for_injection`.
    """
    out: list[tuple[str, str]] = []
    header, lines = "", []
    for line in patch.splitlines():
        if line.startswith("@@"):
            if lines:
                out.append((header, "\n".join(lines)))
            header, lines = line.strip(), [line]
        else:
            # The diff marker comes off. Measured on the demo fixture, whose
            # planted paragraph wraps mid-phrase: `already` then `+approved`.
            # Three of its four instructions matched the raw hunk and
            # `pre-approved-claim` did not, because the `+` sits inside the
            # phrase. Leaving the marker in means any multi-word pattern is
            # evaded by pressing return, and the reader of the diff, human or
            # model, never sees the marker anyway.
            lines.append(line[1:] if line.startswith(("+", "-", " ")) else line)
    if lines:
        out.append((header, "\n".join(lines)))
    return out


def _hunk_label(header: str) -> str:
    """Keep the line range and drop the rest.

    What follows the second `@@` is the enclosing context line, which is prose
    written by whoever opened the pull request. It makes the location longer
    without making it more locatable.
    """
    end = header.find("@@", 2)
    return header[: end + 2] if end != -1 else header


def _located(text: str, where: str) -> list[Finding]:
    """Scan one piece of the input and say where the piece was.

    The location goes in `detail` rather than in a new field on `Finding`
    because `detail` is what the callers already print. The hallucinated-path
    check does the same thing with the path it objected to.
    """
    found = _scan(text, INJECTION_PATTERNS, "CRITICAL", "prompt-injection")
    for f in found:
        f.detail = f"{f.detail} in {where}"
    return found


def scan_pull_request_for_injection(pr: PullRequest) -> list[Finding]:
    """Scan the pull request itself: every hunk, the title and the author.

    `evaluate` judges a draft, so it sees only what a specialist chose to quote.
    In the demo the planted instruction sits in a specification hunk and the
    documentation companion copies added specification lines verbatim, so it
    reaches the draft and the gate catches it. That is a property of that path
    and not of the system. The same instruction in a migration hunk is quoted by
    nobody: run against a diff carrying one, the draft scan returned
    `PASS, 5 checks, no findings` with `injection_attempt` false.

    The title and the author are read for the same reason. Neither passes
    through a specialist, and both reach the provenance thread and the
    dashboard.

    Returns `list[Finding]`, which is what `_scan` returns, so a caller can
    merge these with a draft verdict's findings. It returns them and stops.
    Scanning is not blocking: what a finding costs the item stays with the
    caller, which is where that decision already lives.

    The limit, stated rather than left to be discovered. GitHub omits `patch`
    for a binary file and for one whose diff exceeds its size cap, and a file
    that arrives with no patch text contributes nothing here. That is silence,
    not a clean bill, and this function does not tell the two apart. Saying so
    would mean returning a finding that is not an injection, which is a
    decision about what a caller is handed and does not belong in a function
    named for one check.
    """
    findings = _located(pr.title, "the pull request title")
    findings += _located(pr.author, "the pull request author")
    for f in pr.files:
        path = f.get("path", "an unnamed file")
        for header, text in _hunks(f.get("patch", "")):
            label = _hunk_label(header)
            findings += _located(text, f"{path} {label}".strip())
    return findings


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
