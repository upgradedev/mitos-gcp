"""The typed handoff envelope.

Carried across from ADR-015 in the author's earlier platform design work, named
in the README under "Pre-existing components". The design is reused; no code is.

The one idea worth having is this: **a specialist must be able to refuse.** A
fleet whose agents can only succeed will always produce an answer, including for
the items where the honest answer is "a human has to look at this". That is the
failure mode that makes autonomous systems untrustworthy, and it is why the
count that matters to a reader is not "how many completed" on its own but
"how many completed, how many parked, and why".

So every specialist returns a status:

    ok             it did the work and stands behind it
    needs_changes  it can proceed, but something upstream has to change first
    blocked        it will not proceed; a human decision is required
    error          it failed for a reason that is ours, not the input's

`blocked` is the load-bearing one. It is the difference between a fleet that
looks confident and a fleet you would let run unattended.

`Trust` is the other idea worth carrying: an input is *declared* untrusted
rather than assumed to be handled carefully. The pull request diff is untrusted
data, and saying so in the type is what lets the gate treat it as such
uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    OK = "ok"
    NEEDS_CHANGES = "needs_changes"
    BLOCKED = "blocked"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        """Whether this status stops the item rather than advancing it."""
        return self in (Status.BLOCKED, Status.ERROR)


class Trust(str, Enum):
    """Taint annotation. Applied to the value, not remembered by convention."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True)
class Tainted:
    """A value that came from outside and must never be treated as instruction.

    The pull request diff is the canonical case: it is authored by whoever
    opened the pull request, and the fixture in this repository contains an
    instruction addressed to the review agent precisely because that is a real
    thing that happens.
    """

    value: Any
    trust: Trust = Trust.UNTRUSTED

    def __post_init__(self) -> None:
        if self.trust is not Trust.UNTRUSTED:
            raise ValueError("Tainted exists to mark untrusted values")


@dataclass
class Response:
    """What one specialist hands back.

    `confidence` and `citations` come from ADR-015 and earn their place: a
    finding with no citation cannot be checked by the human who has to act on
    it, and confidence is what a future autonomy tier would gate on.
    """

    companion: str
    status: Status
    assessment: str = ""
    findings: list[str] = field(default_factory=list)
    paths_read: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    confidence: float = 1.0
    reason: str = ""
    # What this specialist chose to open, and what the bound refused. Empty for
    # the deterministic templates, which read nothing, and that difference is
    # exactly the point: a fixed pipeline produces the same sequence every time.
    read_log: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A refusal with no reason is worse than no refusal: it parks an item
        # and tells the human nothing about what to do next.
        if self.status is not Status.OK and not self.reason.strip():
            raise ValueError(
                f"{self.companion} returned {self.status.value} with no reason"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def parks_the_item(self) -> bool:
        return self.status.is_terminal

    def as_dict(self) -> dict[str, Any]:
        return {
            "companion": self.companion,
            "status": self.status.value,
            "findings": self.findings,
            "paths_read": self.paths_read,
            "citations": self.citations,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass
class Outcome:
    """How one item of the backlog ended.

    The vocabulary a reader is given, and it is deliberately small.
    """

    pr_number: int
    title: str
    state: str  # completed | parked | no_action
    reason: str = ""
    parked_by: Optional[str] = None
    findings: int = 0
    plan_hash: str = ""
    published: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "pr": self.pr_number,
            "title": self.title,
            "state": self.state,
            "reason": self.reason,
            "parked_by": self.parked_by,
            "findings": self.findings,
            "plan_hash": self.plan_hash[:16],
            "published": self.published,
        }
