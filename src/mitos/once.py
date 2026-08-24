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

**A claim is a lease, not a tombstone, and the first version got this wrong.**
It marked the delivery permanently on receipt, before the work started. An
instance that died in between left a claim with nothing behind it, so GitHub's
retry was answered "duplicate" and the chore never ran. That traded duplicate
work for lost work, which is strictly worse here: a duplicate is visible in the
thread and can be reconciled, and a silent loss cannot. The claim expires unless
`complete` is called, so a retry after a crash takes it over.

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

# How long a claim holds before a retry may take it over. Longer than the
# slowest chore observed against live Gemini, 303 seconds, with room for the
# tail: taking a lease from a run that is still going produces exactly the
# duplicate this exists to prevent.
LEASE_SECONDS = 900


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _expired(held: dict[str, Any], lease: int = LEASE_SECONDS) -> bool:
    """Has an unfinished claim been abandoned long enough to take over.

    An unreadable timestamp counts as expired. A claim nobody can date is a
    claim nobody can rely on, and refusing the retry would lose the delivery
    for the sake of a field we cannot parse.
    """
    if held.get("done"):
        return False
    try:
        at = datetime.fromisoformat(str(held.get("at")))
    except (TypeError, ValueError):
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds() > lease


class AlreadySeen(Exception):
    """This identifier has been claimed before.

    Not an error in the sense that anything is wrong. A retried delivery is
    GitHub behaving correctly, and the right answer is to acknowledge it and do
    nothing, which is what the caller does.
    """


class Claims(Protocol):
    def claim(self, key: str, *, note: str = "") -> None: ...

    def complete(self, key: str, *, outcome: str = "") -> None: ...

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
            held = self._seen.get(key)
            if held is not None and not _expired(held):
                raise AlreadySeen(
                    f"{key} is {'done' if held.get('done') else 'in flight'} "
                    f"since {held['at']}"
                )
            self._seen[key] = {"at": _now(), "note": note, "done": False}

    def complete(self, key: str, *, outcome: str = "") -> None:
        with self._lock:
            held = self._seen.get(key) or {"at": _now(), "note": ""}
            self._seen[key] = {**held, "done": True, "outcome": outcome}

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

        record = {"at": _now(), "note": note, "done": False}
        try:
            self._docs.document(key).create(record)
            return
        except exceptions.AlreadyExists:
            pass

        # Somebody holds it. Finished work stays refused forever; an abandoned
        # lease is taken over, which is what makes a crash recoverable rather
        # than a delivery lost.
        held = self.seen(key) or {}
        if held.get("done") or not _expired(held):
            raise AlreadySeen(
                f"{key} is {'done' if held.get('done') else 'in flight'} "
                f"since {held.get('at')}"
            )
        self._docs.document(key).set(record)

    def complete(self, key: str, *, outcome: str = "") -> None:
        self._docs.document(key).set(
            {"done": True, "outcome": outcome, "finished_at": _now()}, merge=True
        )

    def seen(self, key: str) -> Optional[dict[str, Any]]:
        doc = self._docs.document(key).get()
        return doc.to_dict() if doc.exists else None


def build_claims(project: Optional[str] = None) -> Claims:
    """`MITOS_LEDGER=memory` forces the offline one, as it does everywhere."""
    if os.environ.get("MITOS_LEDGER", "firestore") == "memory":
        return InMemoryClaims()
    return FirestoreClaims(project)
