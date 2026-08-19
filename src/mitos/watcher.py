"""The control plane.

This is the module the entry leads with, so the claim it makes is worth stating
precisely: **Mitos has no scheduler and no queue.** An agent holds open a
subscription to a Firestore *query*, and the query itself is the trigger. When a
document matching it appears or changes, the fleet wakes.

Why that is not a stylistic choice. A change feed like DynamoDB Streams is
shard-ordered, consumed server-side, and delivers events about a *table*.
`on_snapshot` subscribes to a **query**: "every finding whose deferral is still
open". A process holding that subscription is handed the current result set and
then every subsequent change to it, so "wake me when this set changes" needs no
poller, no queue, and no second store to remember what was already seen.

The consequence is that the provenance thread stops being a log and becomes the
thing that dispatches work. One store is the memory, the audit trail and the
control plane at once, which is the reason a run can be retraced as a single
thread rather than reconciled across three systems.

Two implementations behind one protocol, as everywhere else in this codebase.
`InMemoryWatcher` is driven by explicit ticks so tests and the recorded demo stay
deterministic; `FirestoreWatcher` is the real subscription.

**Cloud Run note.** A subscription is a background thread, and Cloud Run throttles
CPU to zero between requests by default, which would silently suspend it. The
service is deployed with `--no-cpu-throttling` for exactly this reason. A
listener that only runs while someone happens to be calling you is a poller with
extra steps.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional, Protocol

from .ledger import Entry

# What the fleet stands watch for. Expressed as a query rather than a schedule:
# there is no "every night at 2am", there is "this set, whenever it changes".
DEFERRAL_KIND = "finding.deferred"

OnWake = Callable[[list[Entry]], None]


@dataclass
class Wakeup:
    """One time the fleet woke because the world changed, not because it was
    asked. Recorded so the claim is countable rather than asserted."""

    reason: str
    matched: int
    acted_on: list[str] = field(default_factory=list)
    at: str = ""


class Watcher(Protocol):
    def start(self, on_wake: OnWake) -> None: ...

    def stop(self) -> None: ...

    @property
    def wakeups(self) -> list[Wakeup]: ...


def expired_deferrals(entries: list[Entry], today: str) -> list[Entry]:
    """The predicate the subscription exists to serve.

    Firestore evaluates the *query* server-side and hands us the matching set;
    the expiry comparison happens here because "today" moves and the stored
    document does not. That split is the whole point: the set membership is
    maintained by Firestore, the judgement about it is ours.
    """
    out = []
    for e in entries:
        if e.kind != DEFERRAL_KIND:
            continue
        if e.payload.get("resolved"):
            continue
        expires = str(e.payload.get("expires_on", ""))
        if expires and expires <= today:
            out.append(e)
    return out


class InMemoryWatcher:
    """Offline equivalent, driven by explicit ticks.

    Not a fake: it runs the same `expired_deferrals` predicate over the same
    entries. What it does not do is subscribe, which is precisely the thing that
    does not port and therefore the thing the entry claims.
    """

    def __init__(self, ledger, today: str = "2026-08-19") -> None:
        self._ledger = ledger
        self._today = today
        self._on_wake: Optional[OnWake] = None
        self._seen: set[str] = set()
        self._wakeups: list[Wakeup] = []

    def start(self, on_wake: OnWake) -> None:
        self._on_wake = on_wake

    def stop(self) -> None:
        self._on_wake = None

    @property
    def wakeups(self) -> list[Wakeup]:
        return list(self._wakeups)

    def tick(self, today: Optional[str] = None) -> list[Entry]:
        """Advance the world. In production this is Firestore calling us."""
        if today:
            self._today = today
        matched = [
            e
            for e in expired_deferrals(self._ledger.all(), self._today)
            if e.entry_id not in self._seen
        ]
        if not matched:
            return []
        for e in matched:
            self._seen.add(e.entry_id)
        self._wakeups.append(
            Wakeup(
                reason=f"{len(matched)} deferral(s) expired on or before {self._today}",
                matched=len(matched),
                acted_on=[e.entry_id for e in matched],
                at=self._today,
            )
        )
        if self._on_wake:
            self._on_wake(matched)
        return matched


class FirestoreWatcher:
    """The real thing: a standing subscription to a query.

    `on_snapshot` delivers the current result set immediately and then every
    change to it. Nothing here polls, and nothing schedules.
    """

    def __init__(
        self,
        project: Optional[str] = None,
        collection: str = "provenance",
        today: Optional[Callable[[], str]] = None,
        client: Any = None,
    ) -> None:
        # The client is injectable so the snapshot handler, which is the part
        # with the real logic, can be exercised without a Firestore. The
        # subscription itself still has to be run for real, and is, against the
        # emulator in tests/integration/test_watcher_firestore.py.
        if client is None:
            from google.cloud import firestore  # noqa: PLC0415

            client = firestore.Client(project=project)
        self._client = client
        self._collection = collection
        self._today = today or (lambda: date.today().isoformat())
        self._watch: Any = None
        self._on_wake: Optional[OnWake] = None
        self._seen: set[str] = set()
        self._wakeups: list[Wakeup] = []
        self._lock = threading.Lock()

    def start(self, on_wake: OnWake) -> None:
        from google.cloud import firestore  # noqa: PLC0415

        self._on_wake = on_wake
        query = self._client.collection(self._collection).where(
            filter=firestore.FieldFilter("kind", "==", DEFERRAL_KIND)
        )
        self._watch = query.on_snapshot(self._on_snapshot)

    def stop(self) -> None:
        if self._watch is not None:
            self._watch.unsubscribe()
            self._watch = None

    @property
    def wakeups(self) -> list[Wakeup]:
        with self._lock:
            return list(self._wakeups)

    def _on_snapshot(self, docs, changes, read_time) -> None:
        """Firestore calls this. Nobody in our code does."""
        today = self._today()
        entries = []
        for doc in docs:
            data = doc.to_dict() or {}
            data.pop("digest", None)
            try:
                entries.append(Entry(**data))
            except TypeError:
                continue

        matched = [
            e for e in expired_deferrals(entries, today) if e.entry_id not in self._seen
        ]
        if not matched:
            return
        with self._lock:
            for e in matched:
                self._seen.add(e.entry_id)
            self._wakeups.append(
                Wakeup(
                    reason=(
                        f"Firestore delivered a snapshot in which {len(matched)} "
                        f"deferral(s) had expired on or before {today}"
                    ),
                    matched=len(matched),
                    acted_on=[e.entry_id for e in matched],
                    at=today,
                )
            )
        if self._on_wake:
            self._on_wake(matched)


def build_watcher(ledger=None, project: Optional[str] = None) -> Watcher:
    import os  # noqa: PLC0415

    if os.environ.get("MITOS_LEDGER", "memory") == "memory":
        return InMemoryWatcher(ledger)
    return FirestoreWatcher(project=project)
