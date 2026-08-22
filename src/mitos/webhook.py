"""The trigger, for real.

Until now "nobody opened Mitos, a webhook did this" was true of the design and
not of the deployment: the trigger was a fixture. This is the endpoint that
makes it true, and it is the last thing in the product that was simulated.

Everything here is the verification half, kept free of web-framework types so it
can be tested exhaustively without a server. A webhook is a public endpoint that
causes an autonomous system to act, which is the most attackable shape a service
can have, so the checks are worth more than the plumbing:

    signature   HMAC-SHA256 over the raw body, compared in constant time
    size        a body larger than the cap is refused before it is parsed
    event       only pull_request, and only actions that mean new code
    repository  an allowlist, because a signature proves who sent it and not
                that we asked for it

The payload itself is `Tainted`. A pull request title and diff are written by
whoever opened the pull request, and the fixture in this repository contains an
instruction addressed to the review agent precisely because that is a real thing
that happens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Optional

from .envelope import Tainted
from .fixtures import PullRequest

# GitHub caps a delivery at 25 MB. Nothing legitimate here approaches that, and
# parsing an arbitrary body before checking its size is how a public endpoint
# becomes a memory exhaustion primitive.
MAX_BODY_BYTES = 2_000_000

# Actions that mean there is new code to look at. `closed`, `labeled`,
# `assigned` and the rest are noise, and a fleet that wakes on noise is a fleet
# somebody turns off.
ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review"})

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"


class Rejected(Exception):
    """The delivery is not one we will act on.

    Carries a `status` because the distinction matters to the sender: 401 means
    the signature is wrong and GitHub should stop, 202 means we understood and
    chose not to act, and 400 means the body is not what it claims to be.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def verify_signature(body: bytes, header: Optional[str], secret: str) -> None:
    """Constant-time HMAC check over the RAW body.

    Over the raw bytes, not over a re-serialised object: any difference in key
    order or whitespace changes the digest, so verifying a round-tripped body
    verifies something the sender never signed.
    """
    if not secret:
        # Refusing is the only safe behaviour. An endpoint that accepts
        # everything when misconfigured is worse than one that accepts nothing,
        # because the failure is invisible.
        raise Rejected("no webhook secret is configured; refusing every delivery", 503)
    if not header:
        raise Rejected("unsigned delivery", 401)
    if not header.startswith("sha256="):
        raise Rejected("unsupported signature algorithm", 401)

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header.removeprefix("sha256=")):
        raise Rejected("signature does not match", 401)


@dataclass
class Delivery:
    """A verified, understood delivery. Nothing here is trusted, only checked."""

    delivery_id: str
    repository: str
    number: int
    title: Tainted
    author: str
    action: str
    # The commit the pull request is at. Reading the repository at HEAD would
    # read whatever main happens to be, which is not what was proposed.
    head_sha: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "repository": self.repository,
            "pr": self.number,
            # The title is attacker-controlled text. It goes in the thread
            # because provenance needs it, and it is marked so nothing
            # downstream mistakes it for an instruction.
            "title": self.title.value,
            "title_trust": self.title.trust.value,
            "author": self.author,
            "action": self.action,
            "head_sha": self.head_sha,
        }


def parse(
    body: bytes,
    headers: dict[str, str],
    *,
    secret: str,
    allowed_repositories: frozenset[str],
) -> Delivery:
    """Verify and understand a delivery, or raise `Rejected` saying why.

    Order matters and is deliberate: size, then signature, then meaning. Parsing
    before verifying would run a JSON decoder on unauthenticated input, and
    checking the event before the signature would let an unauthenticated caller
    learn which events we care about.
    """
    if len(body) > MAX_BODY_BYTES:
        raise Rejected(f"body of {len(body)} bytes exceeds the cap", 413)

    lower = {k.lower(): v for k, v in headers.items()}
    verify_signature(body, lower.get(SIGNATURE_HEADER.lower()), secret)

    event = lower.get(EVENT_HEADER.lower(), "")
    if event == "ping":
        raise Rejected("ping acknowledged", 202)
    if event != "pull_request":
        raise Rejected(f"not a pull_request event: {event!r}", 202)

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise Rejected(f"body is not JSON: {exc}", 400) from exc
    if not isinstance(payload, dict):
        raise Rejected("body is not a JSON object", 400)

    action = str(payload.get("action", ""))
    if action not in ACTIONS:
        raise Rejected(f"nothing new to read in action {action!r}", 202)

    repository = str((payload.get("repository") or {}).get("full_name", ""))
    if repository not in allowed_repositories:
        # A valid signature proves who sent it. It does not prove we asked for
        # it, and a shared secret reused across repositories is a real thing.
        raise Rejected(f"repository {repository!r} is not on the allowlist", 403)

    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    if not isinstance(number, int):
        raise Rejected("pull_request.number missing or not an integer", 400)

    return Delivery(
        delivery_id=str(lower.get(DELIVERY_HEADER.lower(), "") or "unknown"),
        repository=repository,
        number=number,
        title=Tainted(value=str(pr.get("title", ""))[:300]),
        author=str((pr.get("user") or {}).get("login", ""))[:100],
        action=action,
        head_sha=str((pr.get("head") or {}).get("sha", ""))[:40],
    )


def to_pull_request(delivery: Delivery, files: list[dict[str, Any]]) -> PullRequest:
    """Turn a verified delivery plus its fetched diff into the fleet's input.

    The fleet already treats a `PullRequest` as untrusted data, so nothing
    special happens here beyond keeping the real number and title rather than a
    fixture's.
    """
    return PullRequest(
        number=delivery.number,
        title=delivery.title.value,
        author=delivery.author,
        files=files,
    )
