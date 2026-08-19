"""The thread, on its own.

`InMemoryLedger` and `FirestoreLedger` share their read logic through one base
class precisely so these tests constrain both. What is asserted here is the
interface contract the deployed backend has to keep.
"""

from __future__ import annotations

import pytest

from mitos.ledger import Entry, InMemoryLedger, content_hash


def _e(kind="note", subject="svc", parent=None, **payload):
    return Entry(kind=kind, actor="test", subject=subject, payload=payload, parent_id=parent)


def test_content_hash_is_stable_regardless_of_key_order():
    """The approval card shows this digest and the writer refuses anything that
    does not match, so two identical plans must hash identically."""
    a = content_hash({"path": "x", "body": "y", "pr": 1})
    b = content_hash({"pr": 1, "body": "y", "path": "x"})
    assert a == b


def test_content_hash_changes_when_the_plan_changes():
    a = content_hash({"body": "y"})
    b = content_hash({"body": "y "})
    assert a != b


def test_append_returns_the_entry_and_stores_it():
    led = InMemoryLedger()
    e = led.append(_e())
    assert led.all() == [e]


def test_recall_filters_by_subject():
    led = InMemoryLedger()
    led.append(_e(subject="a"))
    led.append(_e(subject="b"))
    assert [e.subject for e in led.recall("a")] == ["a"]


def test_recall_filters_by_kind():
    led = InMemoryLedger()
    led.append(_e(kind="finding.raised"))
    led.append(_e(kind="noise"))
    got = led.recall("svc", kinds={"finding.raised"})
    assert [e.kind for e in got] == ["finding.raised"]


def test_recall_with_no_kinds_returns_every_kind():
    led = InMemoryLedger()
    led.append(_e(kind="a"))
    led.append(_e(kind="b"))
    assert len(led.recall("svc")) == 2


def test_thread_walks_back_to_the_root_in_order():
    led = InMemoryLedger()
    root = led.append(_e(kind="root"))
    mid = led.append(_e(kind="mid", parent=root.entry_id))
    leaf = led.append(_e(kind="leaf", parent=mid.entry_id))

    assert [e.kind for e in led.thread(leaf.entry_id)] == ["root", "mid", "leaf"]


def test_thread_of_an_unknown_id_is_empty():
    assert InMemoryLedger().thread("nope") == []


def test_thread_terminates_on_a_cycle():
    """A malformed parent chain must not hang the service."""
    led = InMemoryLedger()
    a = _e(kind="a")
    b = _e(kind="b", parent=a.entry_id)
    a.parent_id = b.entry_id
    led.append(a)
    led.append(b)
    assert len(led.thread(b.entry_id)) <= 2


def test_entry_digest_covers_the_payload_but_not_the_timestamp():
    """Two runs producing the same decision should be comparable; the clock
    should not make them look different."""
    a = Entry(kind="k", actor="x", subject="s", payload={"v": 1}, recorded_at="t1")
    b = Entry(kind="k", actor="x", subject="s", payload={"v": 1}, recorded_at="t2")
    assert a.digest() == b.digest()

    c = Entry(kind="k", actor="x", subject="s", payload={"v": 2})
    assert a.digest() != c.digest()


def test_to_doc_carries_the_digest():
    doc = _e().to_doc()
    assert doc["digest"] and len(doc["digest"]) == 64


def test_seeded_entries_are_readable():
    seed = [_e(kind="seeded")]
    assert InMemoryLedger(seed=seed).all()[0].kind == "seeded"


@pytest.mark.parametrize("forbidden", ["update", "delete", "set", "overwrite", "remove"])
def test_the_interface_offers_no_way_to_mutate_history(forbidden):
    """Append-only is the claim; the absence of these methods is the mechanism."""
    assert not hasattr(InMemoryLedger, forbidden)
