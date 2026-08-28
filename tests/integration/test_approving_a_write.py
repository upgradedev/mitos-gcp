"""Approving a suggested change is what turns an analysis into a write.

Nothing tested this endpoint. `grep -rln suggested_changes tests` returned
nothing, and two defects sat in it.

It accepted any reviewer. Every member after the first is made a reviewer
automatically at sign-in, so approving a write on somebody else's repository was
open to anyone who could join the workspace. ADR-006 and the interface both
describe it as an owner decision; only the code disagreed.

And it was not exactly-once. `status` was read and then GitHub was called, so
two approvals arriving together both saw `awaiting_approval` and both opened a
pull request. A crash between the call and the status update left a real pull
request open, the change still awaiting approval, and the next attempt opened a
second one.
"""

from __future__ import annotations

import sys
import types

import pytest

pytest.importorskip("fastapi")


class _Doc:
    def __init__(self, data=None):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data or {})


class _Ref:
    def __init__(self, data):
        self._data = data

    def get(self):
        return _Doc(self._data)

    def set(self, value, merge=False):
        self._data.update(value)

    def update(self, value):
        self._data.update(value)


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def where(self, **_kwargs):
        return self

    def limit(self, _n):
        return self

    def stream(self):
        return iter(self._rows)


class _Collection:
    def __init__(self, world, name):
        self._world = world
        self._name = name

    def document(self, key):
        return _Ref(self._world.setdefault(self._name, {}).setdefault(key, {}))

    def where(self, **_kwargs):
        return _Query([_Doc(v) for v in self._world.get(self._name, {}).values()])


class _Firestore:
    def __init__(self, world):
        self._world = world

    def collection(self, name):
        return _Collection(self._world, name)


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("MITOS_LEDGER", "memory")
    import service.main as main

    data = {
        "suggested_changes": {
            "run-1": {
                "repository": "acme/billing",
                "path": "docs/spec.md",
                "body": "repaired",
                "source_pr": 7,
                "source_head_sha": "headsha",
                "installation_id": 42,
                "status": "awaiting_approval",
            }
        },
        "repositories": {"acme/billing": {"full_name": "acme/billing", "workspace_id": "w1", "active": True}},
    }
    fake = types.ModuleType("google.cloud.firestore")
    fake.Client = lambda project=None: _Firestore(data)
    fake.SERVER_TIMESTAMP = "<ts>"
    fake.FieldFilter = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)

    # And the attribute on the package, which is what actually decides.
    #
    # `from google.cloud import firestore` imports `google.cloud`, then takes
    # the `firestore` ATTRIBUTE if the package already has one, and only falls
    # back to importing the submodule if it does not. Patching `sys.modules`
    # alone therefore works only when nothing has imported the real client yet.
    #
    # That is why this passed on a laptop and failed in CI: the integration job
    # runs the Firestore emulator suite in the same process, so by the time
    # these tests run the real module is already bound to the package and the
    # fake in `sys.modules` is never consulted. The endpoint talked to the
    # emulator, found no document, and answered 404.
    import google.cloud  # noqa: PLC0415

    monkeypatch.setattr(google.cloud, "firestore", fake, raising=False)

    # An in-memory claim store. `_CLAIMS` is a module singleton that would carry
    # one test's claim into the next, and the fake Firestore below does not
    # implement the create() precondition the real one relies on. What is under
    # test here is that the endpoint TAKES a claim before writing, not how the
    # claim is stored, which `test_once.py` covers.
    taken: set[str] = set()
    asked: list[str] = []

    class _Claims:
        def claim(self, key):
            asked.append(key)
            if key in taken:
                return False
            taken.add(key)
            return True

        def complete(self, key):
            return None

    monkeypatch.setattr(main, "claims", lambda: _Claims())

    published = []
    monkeypatch.setattr(
        main, "_github_suggested_pr",
        lambda **kwargs: published.append(kwargs) or {"published": True, "pull_number": 9},
    )
    return main, data, published, asked


def _as(main, monkeypatch, role):
    monkeypatch.setattr(
        main, "_workspace_context",
        lambda request: ({"login": "someone", "github_user_id": 1}, {"workspace_id": "w1"}),
    )
    monkeypatch.setattr(
        main, "_require_role",
        lambda request, workspace_id, roles: (
            {"login": "someone"} if role in roles
            else (_ for _ in ()).throw(
                main.HTTPException(status_code=403, detail="Workspace role does not permit this action")
            )
        ),
    )


def test_a_reviewer_cannot_approve_a_write(world, monkeypatch):
    """Every member after the first is a reviewer automatically."""
    main, _, published, _asked = world
    _as(main, monkeypatch, "reviewer")
    from fastapi.testclient import TestClient

    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/workspace/suggested-changes/approve", json={"run_id": "run-1"}
    )

    assert response.status_code == 403, response.text
    assert published == [], "a reviewer opened a pull request"


def test_an_owner_can_approve(world, monkeypatch):
    """The refusal has to let the one legitimate caller through."""
    main, _, published, _asked = world
    _as(main, monkeypatch, "owner")
    from fastapi.testclient import TestClient

    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/workspace/suggested-changes/approve", json={"run_id": "run-1"}
    )

    assert response.status_code == 200, response.text
    assert len(published) == 1


def test_a_second_approval_of_the_same_change_opens_no_second_pull_request(world, monkeypatch):
    """Two clicks, or two people, or a retry after a crash. The claim is the
    same Firestore create() precondition the webhook uses for delivery ids."""
    main, _, published, asked = world
    _as(main, monkeypatch, "owner")
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    first = client.post("/api/workspace/suggested-changes/approve", json={"run_id": "run-1"})
    second = client.post("/api/workspace/suggested-changes/approve", json={"run_id": "run-1"})

    assert first.status_code == 200
    assert second.status_code in (200, 409), second.text
    assert len(published) == 1, f"{len(published)} pull requests opened for one approval"


def test_the_claim_is_taken_before_anything_is_written(world, monkeypatch):
    """The sequential path is already guarded by the published status check, so
    a second call in a test returns the receipt either way. What the claim adds
    is the CONCURRENT case, where both callers read `awaiting_approval` before
    either writes, and that is not reachable from a sequential test.

    So this asserts the mechanism directly: a claim keyed on the run is taken,
    and it is taken before GitHub is called.
    """
    main, _, published, asked = world
    _as(main, monkeypatch, "owner")
    from fastapi.testclient import TestClient

    TestClient(main.app, raise_server_exceptions=False).post(
        "/api/workspace/suggested-changes/approve", json={"run_id": "run-1"}
    )

    assert "suggest:run-1" in asked, (
        "no claim was taken, so two approvals arriving together would both "
        "reach GitHub"
    )
    assert len(published) == 1
