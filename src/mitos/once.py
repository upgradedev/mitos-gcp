"""Claim an identifier exactly once, across instances.

GitHub retries a delivery it did not get a timely answer for, and Cloud Run runs
up to four readers, so the same pull request can arrive twice within seconds on
two different instances. Nothing keyed on the delivery id, so both would run the
whole chore: four model calls each, two sets of specialist responses in the
provenance thread, and two of everything a reader afterwards has to reconcile.

That matters more here than in most places, because the thread is the audit
trail. A duplicate run does not just cost money, it puts a second account of the
same event into the record that is supposed to be the account.

`create` rather than read-then-write. A read followed by a write is a check that
passes under test and loses under concurrency, which is the worst kind to put on
a path whose whole purpose is handling a race.

Two implementations behind one protocol, as everywhere else here. The in-memory
one is what the offline suite runs and needs no credential; it is honest about
its scope in the docstring rather than pretending to be distributed.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

COLLECTION = "deliveries"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AlreadySeen(Exception):
    """This identifier has been claimed before.

    Not an error in the sense that anything is wrong. A retried delivery is
    GitHub behaving correctly, and the right answer is to acknowledge it and do
    nothing, which is what the caller does.
    """


class Claims(Protocol):
    def claim(self, key: str, *, note: str = "") -> None: ...

    def seen(self, key: str) -> Optional[dict[str, Any]]: ...


class InMemoryClaims:
    """One process only, which is the whole caveat.

    Correct for the offline suite and for a single instance. It cannot stop a
    duplicate that lands on a different Cloud Run instance, and saying so here
    is cheaper than somebody assuming otherwise from the interface.
    """

    def __init__(self) -> None:
        self._seen: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, *, note: str = "") -> None:
        with self._lock:
            if key in self._seen:
                raise AlreadySeen(f"{key} was claimed at {self._seen[key]['at']}")
            self._seen[key] = {"at": _now(), "note": note}

    def seen(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._seen.get(key)


class FirestoreClaims:
    """Across instances, using Firestore's own precondition.

    `create` fails if the document exists. Two readers handed the same delivery
    at the same moment produce one claim and one `AlreadySeen`, decided by
    Firestore rather than by whichever of them read first.
    """

    def __init__(self, project: Optional[str] = None, collection: str = COLLECTION):
        from google.cloud import firestore  # noqa: PLC0415

        self._docs = firestore.Client(project=project).collection(collection)

    def claim(self, key: str, *, note: str = "") -> None:
        from google.api_core import exceptions  # noqa: PLC0415

        try:
            self._docs.document(key).create({"at": _now(), "note": note})
        except exceptions.AlreadyExists as exc:
            raise AlreadySeen(f"{key} has been delivered before") from exc

    def seen(self, key: str) -> Optional[dict[str, Any]]:
        doc = self._docs.document(key).get()
        return doc.to_dict() if doc.exists else None


def build_claims(project: Optional[str] = None) -> Claims:
    """`MITOS_LEDGER=memory` forces the offline one, as it does everywhere."""
    if os.environ.get("MITOS_LEDGER", "firestore") == "memory":
        return InMemoryClaims()
    return FirestoreClaims(project)
