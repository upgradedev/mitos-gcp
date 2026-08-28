"""The thread. Every decision the fleet makes is appended here, and every entry
names the entry it came from, so you can walk any outcome back to the diff that
caused it.

That is the whole reason the product is called Mitos. Ariadne's thread is only
useful if it is unbroken, so entries are append-only and content-hashed: nothing
in the fleet has an operation that edits or deletes one.

Two implementations behind one interface. `InMemoryLedger` is what the offline
test suite and the CI run use, so the chore is exercisable with no cloud
credential. `FirestoreLedger` is what runs in production, and it is the same
interface, which is why the deployment is a swap rather than a rewrite.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Protocol

COLLECTION = "provenance"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(payload: Any) -> str:
    """Stable sha256 over a payload.

    `sort_keys` matters: the approval card shows this hash and the writer refuses
    any plan whose hash differs, so two runs that produce the same plan have to
    produce the same digest regardless of dict ordering.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class Entry:
    """One link in the thread."""

    kind: str
    actor: str
    subject: str
    payload: dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None
    run_id: str = ""
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    recorded_at: str = field(default_factory=_now)

    def digest(self) -> str:
        return content_hash(
            {
                "kind": self.kind,
                "actor": self.actor,
                "subject": self.subject,
                "payload": self.payload,
                "parent_id": self.parent_id,
            }
        )

    def to_doc(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["digest"] = self.digest()
        return doc


class Ledger(Protocol):
    def append(self, entry: Entry) -> Entry: ...

    def recall(
        self, subject: str, kinds: Optional[Iterable[str]] = None
    ) -> list[Entry]: ...

    def thread(self, entry_id: str) -> list[Entry]: ...

    def all(self) -> list[Entry]: ...


class _BaseLedger:
    """Shared read logic so the two backends cannot drift on `thread`."""

    def _entries(self) -> list[Entry]:
        raise NotImplementedError

    def recall(
        self, subject: str, kinds: Optional[Iterable[str]] = None
    ) -> list[Entry]:
        wanted = set(kinds) if kinds else None
        return [
            e
            for e in self._entries()
            if e.subject == subject and (wanted is None or e.kind in wanted)
        ]

    def thread(self, entry_id: str) -> list[Entry]:
        """Walk one entry back to the root. This is the retrace-your-way-out."""
        by_id = {e.entry_id: e for e in self._entries()}
        out: list[Entry] = []
        seen: set[str] = set()
        cursor: Optional[str] = entry_id
        while cursor and cursor in by_id and cursor not in seen:
            seen.add(cursor)
            entry = by_id[cursor]
            out.append(entry)
            cursor = entry.parent_id
        return list(reversed(out))

    def all(self) -> list[Entry]:
        return list(self._entries())


class InMemoryLedger(_BaseLedger):
    """Offline backend. Same interface, no credential, used by the test suite."""

    def __init__(self, seed: Optional[list[Entry]] = None) -> None:
        self._store: list[Entry] = list(seed or [])

    def append(self, entry: Entry) -> Entry:
        self._store.append(entry)
        return entry

    def _entries(self) -> list[Entry]:
        return self._store


class FirestoreLedger(_BaseLedger):
    """Production backend. Append-only by INTERFACE: there is no update or
    delete method here, so nothing in this codebase can rewrite or erase an
    entry.

    This docstring used to go on to claim a second lock in IAM, saying the
    reader and evaluator held only a create grant on this collection. Not quoted
    here on purpose: a check that reads source text cannot tell a claim from a
    quotation of one, and this file is checked. It was false either way.
    `infra/main.tf` grants all three fleet identities
    `roles/datastore.user`, which includes `entities.update` and
    `entities.delete`, project-wide. Firestore IAM has no per-collection or
    per-operation scope in its predefined roles, so the second lock a reader
    would infer from that sentence does not exist and could not be built the way
    it implied.

    One lock, in code, stated as one. A provenance ledger that claims two and
    has one is worse than one that claims one, because the claim is the product.

    Imported lazily so that the offline suite never needs the Firestore client.
    """

    def __init__(self, project: Optional[str] = None, collection: str = COLLECTION):
        from google.cloud import firestore  # noqa: PLC0415

        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self._client = firestore.Client(project=self.project)
        self._collection = collection

    def append(self, entry: Entry) -> Entry:
        self._client.collection(self._collection).document(entry.entry_id).create(
            entry.to_doc()
        )
        return entry

    def _entries(self) -> list[Entry]:
        docs = self._client.collection(self._collection).stream()
        out = []
        for doc in docs:
            data = doc.to_dict() or {}
            data.pop("digest", None)
            out.append(Entry(**data))
        return sorted(out, key=lambda e: e.recorded_at)


def build_ledger(kind: Optional[str] = None) -> Ledger:
    """Pick a backend. `MITOS_LEDGER=memory` forces the offline one."""
    kind = kind or os.environ.get("MITOS_LEDGER", "firestore")
    if kind == "memory":
        return InMemoryLedger()
    return FirestoreLedger()
