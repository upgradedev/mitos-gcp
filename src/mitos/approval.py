"""The approval, as an artifact the writer checks rather than a boolean it trusts.

`POST /execute` used to take a path, a body and a branch and publish them. The
only thing standing between the internet and the specification repository was a
Cloud Run IAM binding, and for a while that binding was `allUsers`. The README
said the writer re-checked the plan hash. It did not.

So the write is bound to an approval now, and the binding is what makes the
claim true rather than aspirational. An approval names:

    what          the exact bytes, by digest, plus path and branch
    where         the repository those bytes are going to
    which run     the correlation id, so a receipt retraces to a pull request
    who           the actor who approved it
    until when    an expiry, because an approval left lying around is a key
    once          a nonce, consumed transactionally, so a replay is refused

The writer recomputes the digest from the bytes it was actually handed. A
caller that changes one character after approval produces a different digest and
is refused, which is the property "bound to the exact bytes" actually means.

Consumption is a `create` on a document keyed by the nonce, not an update to the
approval. That keeps the ledger append-only, which is ADR-004's shape, and gets
one-time semantics from Firestore's own precondition rather than from a
read-then-write this service could lose a race on.

Two implementations behind one protocol, as everywhere else here: the in-memory
one is what the offline suite runs and needs no credential.
"""

from __future__ import annotations

import hmac
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

from .ledger import content_hash

# Long enough for a person to read an approval card and decide, short enough
# that an approval found in a log tomorrow is worth nothing.
DEFAULT_TTL_SECONDS = 900

CONSUMED_COLLECTION = "approvals_consumed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def body_digest(
    *, repository: str, path: str, branch: str, body: str, commit: str = ""
) -> str:
    """The digest an approval binds, over everything that decides the effect.

    The body alone is not enough. The same bytes written to a different path or
    a different branch are a different change, and an approval that did not
    cover them would still verify.

    `commit` is the head sha of the pull request the approval was granted for.
    It is here because an approval is a statement about a diff somebody read,
    and a branch that moves afterwards makes that diff a different diff. Without
    it, an approval granted against one commit stayed valid after the author
    pushed another, which is the one thing a reviewer would not expect.

    Empty for a run with no commit, the offline demo among them, and empty is
    recorded rather than omitted so the two cases hash differently.
    """
    return content_hash(
        {
            "repository": repository,
            "path": path,
            "branch": branch,
            "commit": commit,
            "body": body,
        }
    )


class Expired(Exception):
    """The approval was real and is no longer."""


class Replayed(Exception):
    """This approval has already been used once."""


class Mismatch(Exception):
    """The bytes presented are not the bytes that were approved."""


@dataclass(frozen=True)
class Approval:
    """What a human approved, recorded before anything is written."""

    repository: str
    path: str
    branch: str
    digest: str
    run_id: str
    actor: str
    # The head sha the reviewer was looking at. Bound into the digest, so an
    # approval does not survive the author pushing again.
    commit: str = ""
    intent: str = "publish_specification"
    intent_origin: str = "human_approval_card"
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex)
    granted_at: str = field(default_factory=lambda: _now().isoformat(timespec="seconds"))
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    def expires_at(self) -> datetime:
        granted = datetime.fromisoformat(self.granted_at)
        if granted.tzinfo is None:
            granted = granted.replace(tzinfo=timezone.utc)
        return granted + timedelta(seconds=self.ttl_seconds)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["expires_at"] = self.expires_at().isoformat(timespec="seconds")
        return out

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> "Approval":
        fields = {k: v for k, v in (doc or {}).items() if k in cls.__annotations__}
        missing = [
            k
            for k in ("repository", "path", "branch", "digest", "run_id", "actor")
            if not fields.get(k)
        ]
        if missing:
            raise Mismatch(f"the approval is missing {', '.join(missing)}")
        return cls(**fields)

    def check(
        self,
        *,
        repository: str,
        path: str,
        branch: str,
        body: str,
        commit: str = "",
        now: Optional[datetime] = None,
    ) -> None:
        """Raise unless this approval covers exactly these bytes, still.

        `compare_digest` rather than `==`: the comparison is against a value the
        caller supplied, and a timing side channel on a digest comparison is the
        kind of detail that is free to get right and awkward to explain later.
        """
        if (now or _now()) >= self.expires_at():
            raise Expired(
                f"the approval expired at {self.expires_at().isoformat(timespec='seconds')}"
            )
        # The commit the CALLER presents, not the one stored on the approval.
        # Recomputing from `self.commit` made the approval verify against
        # itself, so the field was recorded and never checked: a binding that
        # reads correctly and enforces nothing. Caught by the test that meant to
        # prove it worked.
        presented = body_digest(
            repository=repository,
            path=path,
            branch=branch,
            body=body,
            commit=commit,
        )
        if not hmac.compare_digest(presented, self.digest):
            raise Mismatch(
                "the bytes presented are not the bytes that were approved: "
                f"approved {self.digest[:12]}, presented {presented[:12]}"
            )


class ApprovalStore(Protocol):
    """Where approvals live, and where their one use is recorded."""

    def grant(self, approval: Approval) -> Approval: ...

    def find(self, nonce: str) -> Optional[Approval]: ...

    def consume(self, nonce: str, *, by: str) -> None: ...


class InMemoryApprovalStore:
    """The offline one. What the whole test suite and the recorded demo use."""

    def __init__(self) -> None:
        self._granted: dict[str, Approval] = {}
        self._consumed: dict[str, str] = {}

    def grant(self, approval: Approval) -> Approval:
        self._granted[approval.nonce] = approval
        return approval

    def find(self, nonce: str) -> Optional[Approval]:
        return self._granted.get(str(nonce or ""))

    def consume(self, nonce: str, *, by: str) -> None:
        if nonce in self._consumed:
            raise Replayed(
                f"this approval was already used by {self._consumed[nonce]}"
            )
        self._consumed[nonce] = by


class FirestoreApprovalStore:
    """The deployed one.

    `create` rather than `set`: it fails if the document exists, so two writers
    racing the same approval produce one write and one refusal rather than two
    writes. Doing this as read-then-write would be a check that passes under
    test and loses under concurrency, which is the worst kind.
    """

    def __init__(self, project: Optional[str] = None, collection: str = CONSUMED_COLLECTION):
        from google.cloud import firestore  # noqa: PLC0415

        self._db = firestore.Client(project=project)
        self._granted = self._db.collection("approvals")
        self._consumed = self._db.collection(collection)

    def grant(self, approval: Approval) -> Approval:
        self._granted.document(approval.nonce).set(approval.as_dict())
        return approval

    def find(self, nonce: str) -> Optional[Approval]:
        doc = self._granted.document(str(nonce or "")).get()
        return Approval.from_dict(doc.to_dict()) if doc.exists else None

    def consume(self, nonce: str, *, by: str) -> None:
        from google.api_core import exceptions  # noqa: PLC0415

        try:
            self._consumed.document(nonce).create(
                {"consumed_by": by, "consumed_at": _now().isoformat(timespec="seconds")}
            )
        except exceptions.AlreadyExists as exc:
            raise Replayed("this approval has already been used") from exc


def build_approval_store(project: Optional[str] = None) -> ApprovalStore:
    """`MITOS_LEDGER=memory` forces the offline one, as it does for the ledger."""
    if os.environ.get("MITOS_LEDGER", "firestore") == "memory":
        return InMemoryApprovalStore()
    return FirestoreApprovalStore(project)


def verify_and_consume(
    store: ApprovalStore,
    *,
    nonce: str,
    repository: str,
    path: str,
    branch: str,
    body: str,
    by: str,
    commit: str = "",
    now: Optional[datetime] = None,
) -> Approval:
    """The whole check, in the order that matters.

    Existence, then coverage, then consumption. Consuming first would burn an
    approval on a request that was going to be refused anyway, which turns a
    typo into a lost approval.
    """
    approval = store.find(nonce)
    if approval is None:
        raise Mismatch("no approval was granted for that nonce")
    approval.check(
        repository=repository,
        path=path,
        branch=branch,
        body=body,
        commit=commit,
        now=now,
    )
    store.consume(approval.nonce, by=by)
    return approval
