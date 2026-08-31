"""A content-addressed approval whose content nobody can read.

The interface showed a digest, a findings count and a button. It never showed
the bytes. Addressing a plan by the sha256 of its content is most of this
product's argument, and it is worth very little if the person pressing approve
has no way to see what the hash describes.

There was no endpoint to ask, either. `/api/workspace/suggested-changes/approve`
existed and nothing served the proposal itself, so the browser could send a run
id and could not render what that run id meant.

The harness mirrors `test_approving_a_write.py`: the same fake Firestore, bound
to the package attribute as well as `sys.modules` for the reason recorded there.
"""

from __future__ import annotations

import sys
import types

import pytest


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


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def where(self, **_kwargs):
        return self

    def limit(self, _n):
        return self

    def stream(self):
        return [_Doc(row) for row in self._rows]


class _Collection:
    def __init__(self, world, name):
        self._world = world
        self._name = name

    def document(self, key):
        return _Ref(self._world.get(self._name, {}).get(key))

    def where(self, **_kwargs):
        return _Query(list(self._world.get(self._name, {}).values()))


class _Firestore:
    def __init__(self, world):
        self._world = world

    def collection(self, name):
        return _Collection(self._world, name)


BODY = "# Customer record\n\nThe mobile number column now exists.\n"


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("MITOS_LEDGER", "memory")
    import service.main as main
    from mitos.ledger import content_hash

    data = {
        "suggested_changes": {
            "run-1": {
                "repository": "acme/billing",
                "path": "docs/spec.md",
                "body": BODY,
                "source_pr": 7,
                "source_head_sha": "headsha",
                "installation_id": 42,
                "status": "awaiting_approval",
                "findings": ["personal data field with no retention entry"],
                "advisories": ["the register was last updated in July"],
                "plan_hash": content_hash(
                    {"pr": 7, "path": "docs/spec.md", "body": BODY}
                ),
            },
            "tampered": {
                "repository": "acme/billing",
                "path": "docs/spec.md",
                "body": BODY + "\nand one line nobody approved\n",
                "source_pr": 7,
                "source_head_sha": "headsha",
                "status": "awaiting_approval",
                "plan_hash": content_hash(
                    {"pr": 7, "path": "docs/spec.md", "body": BODY}
                ),
            },
        },
        "repositories": {
            "acme/billing": {
                "full_name": "acme/billing",
                "workspace_id": "w1",
                "active": True,
            }
        },
    }
    fake = types.ModuleType("google.cloud.firestore")
    fake.Client = lambda project=None: _Firestore(data)
    fake.SERVER_TIMESTAMP = "<ts>"
    fake.FieldFilter = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)

    import google.cloud  # noqa: PLC0415

    monkeypatch.setattr(google.cloud, "firestore", fake, raising=False)
    return main, data


def _as(main, monkeypatch, role, workspace="w1"):
    monkeypatch.setattr(
        main,
        "_workspace_context",
        lambda request: (
            {"login": "someone", "github_user_id": 1},
            {"workspace_id": workspace},
        ),
    )
    monkeypatch.setattr(
        main,
        "_require_role",
        lambda request, workspace_id, roles: (
            {"login": "someone"}
            if role in roles
            else (_ for _ in ()).throw(
                main.HTTPException(
                    status_code=403, detail="Workspace role does not permit this action"
                )
            )
        ),
    )


def _get(main, run_id="run-1"):
    from fastapi.testclient import TestClient

    return TestClient(main.app, raise_server_exceptions=False).get(
        f"/api/workspace/suggested-changes/{run_id}"
    )


# ---------------------------------------------------------------------------
# What it shows
# ---------------------------------------------------------------------------


def test_it_serves_the_exact_bytes_and_not_a_summary(world, monkeypatch):
    main, _ = world
    _as(main, monkeypatch, "owner")

    body = _get(main).json()

    assert body["body"] == BODY, "the proposal was summarised rather than served"
    assert body["bytes"] == len(BODY.encode("utf-8"))
    assert body["path"] == "docs/spec.md"
    assert body["repository"] == "acme/billing"
    assert body["source_pr"] == 7
    assert body["source_head_sha"] == "headsha"


def test_the_digest_is_recomputed_rather_than_echoed(world, monkeypatch):
    """A reader told to trust a hash is entitled to be told whether it still
    describes what they are being shown."""
    main, _ = world
    _as(main, monkeypatch, "owner")

    assert _get(main).json()["digest_matches"] is True


def test_bytes_that_no_longer_match_their_digest_are_flagged(world, monkeypatch):
    """The counterweight, and the case that matters: stored content edited
    after the plan was hashed must not be presented as the approved plan."""
    main, _ = world
    _as(main, monkeypatch, "owner")

    body = _get(main, "tampered").json()

    assert body["digest_matches"] is False
    assert body["body"].endswith("nobody approved\n")


def test_it_says_what_pressing_approve_will_do(world, monkeypatch):
    main, _ = world
    _as(main, monkeypatch, "owner")

    plan = _get(main).json()["on_approval"]

    assert plan["creates_branch"] == "mitos/suggestion-7-run-1"
    assert plan["writes_path"] == "docs/spec.md"
    assert plan["opens_pull_request"] is True
    assert "writer service" in plan["identity"]
    assert "sha256" in plan["bound_to"]


def test_the_findings_and_advisories_travel_with_the_proposal(world, monkeypatch):
    main, _ = world
    _as(main, monkeypatch, "owner")

    body = _get(main).json()

    assert body["findings"] == ["personal data field with no retention entry"]
    assert body["advisories"] == ["the register was last updated in July"]


# ---------------------------------------------------------------------------
# Who may see it, and who may act on it
# ---------------------------------------------------------------------------


def test_a_reviewer_may_read_the_proposal(world, monkeypatch):
    """Reading is how a reviewer reviews. Only pressing approve is restricted,
    and that asymmetry is deliberate."""
    main, _ = world
    _as(main, monkeypatch, "reviewer")

    response = _get(main)

    assert response.status_code == 200
    assert response.json()["may_approve"] is False


def test_an_owner_is_told_they_may_approve(world, monkeypatch):
    main, _ = world
    _as(main, monkeypatch, "owner")

    assert _get(main).json()["may_approve"] is True


def test_a_viewer_is_refused_the_proposal_entirely(world, monkeypatch):
    """Reading is restricted to the two roles that act on a proposal.

    Written first as an assertion that a viewer sees `may_approve: false`, which
    was wrong about this endpoint: a viewer does not get to read the bytes at
    all. Correcting the test rather than widening the endpoint, because the
    smaller answer is the one the boundary already gives.
    """
    main, _ = world
    _as(main, monkeypatch, "viewer")

    assert _get(main).status_code == 403


def test_another_workspace_cannot_read_the_proposal(world, monkeypatch):
    main, _ = world
    _as(main, monkeypatch, "owner", workspace="somebody-else")

    response = _get(main)

    assert response.status_code == 403
    assert "outside this workspace" in response.json()["detail"]


def test_a_missing_change_is_a_404_not_an_empty_proposal(world, monkeypatch):
    main, _ = world
    _as(main, monkeypatch, "owner")

    assert _get(main, "no-such-run").status_code == 404
