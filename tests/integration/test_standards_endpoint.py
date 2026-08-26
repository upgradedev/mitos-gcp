"""An unreachable repository is not a clean one, and this used to say it was.

`/standards.json?repository=owner/name` answered `200` with zero findings and an
empty summary for EVERY repository anyone named. Not a rate limit, not one bad
repository: the same answer for `octocat/Hello-World` and for this repository,
which audits correctly from a laptop in 12.6 seconds and reports 24 rules with a
real failure in them.

The cause was one `except Exception` in `GitHubCorpus.paths()` that turned any
transport failure into an empty file list, discarding the reason. A caller could
not tell that from a repository with nothing in scope, the logs said `200 OK`,
and `deployed.yml` only ever asked for the demo corpus, so the one gate that
watches production never touched the parameter that was broken.

That is the shape this project keeps finding: a check passing over the thing it
was named after. So the endpoint refuses now, and layer 3 of the deployed
pyramid asks for a real repository.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITOS_LEDGER", "memory")
    monkeypatch.setenv("MITOS_DEMO_MODE", "true")
    import service.main as main

    return TestClient(main.app, raise_server_exceptions=False), main


class _Corpus:
    """A corpus that failed to list, the way the real one now reports it."""

    def __init__(self, failure=None, status=None):
        self.failure = failure
        self.status = status

    def paths(self):
        return []


def test_a_repository_that_could_not_be_read_is_not_reported_as_audited(client, monkeypatch):
    api, main = client
    monkeypatch.setattr(
        main, "build_corpus",
        lambda *a, **k: _Corpus(failure="URLError: [Errno -3] Temporary failure"),
    )

    response = api.get("/standards.json?repository=owner/name")

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["repository"] == "owner/name"
    assert "Temporary failure" in body["detail"]
    assert "60 requests an hour" in body["fix"]


def test_a_repository_that_does_not_exist_is_the_callers_mistake(client, monkeypatch):
    """404 from GitHub is not an outage. Answering 502 for it would send an
    operator looking for a problem on our side."""
    api, main = client
    monkeypatch.setattr(
        main, "build_corpus", lambda *a, **k: _Corpus(failure="HTTPError: 404", status=404)
    )

    response = api.get("/standards.json?repository=owner/nope")

    assert response.status_code == 400
    assert "not a repository this can read" in response.json()["detail"]


def test_a_readable_repository_still_returns_its_rules(client, monkeypatch):
    """The refusal must not swallow the working path."""
    api, main = client

    response = api.get("/standards.json")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["rules"] > 0
    # The load-bearing property of this module: silence is never compliance.
    assert body["summary"]["could_not_be_determined"] > 0
    assert body["findings"]


def test_the_demo_corpus_says_it_is_the_demo_corpus(client):
    """`repository: null` told a reader nothing about what had been audited,
    while the findings read as statements about a real repository."""
    api, _ = client

    body = api.get("/standards.json").json()

    assert body["repository"] is None
    assert "demo corpus" in (body.get("note") or "").lower(), (
        "the payload does not say what it audited, so a critical FAIL in it "
        "reads as a claim about the caller's own repository"
    )


def test_looking_twice_costs_once(client, monkeypatch):
    """Measured, not assumed: one audit of this repository makes 31 requests to
    api.github.com, and an unauthenticated caller gets 60 an hour per address.
    Two audits exhausts the quota, and Cloud Run egresses from a shared address,
    so the quota is not even ours alone.

    That is exactly how the silent failure was found: the endpoint returned an
    empty result for every repository for about an hour, then started working
    again on its own when the window rolled over.
    """
    api, main = client
    main._AUDIT_CACHE.clear()

    built = []

    class _Real:
        failure = None
        status = None

        def paths(self):
            return ["README.md"]

    monkeypatch.setattr(
        main, "build_corpus", lambda *a, **k: (built.append(1), _Real())[1]
    )
    monkeypatch.setattr(
        main, "check_repository",
        lambda corpus: type("R", (), {
            "results": [],
            "summary": type("S", (), {"as_dict": lambda self: {"rules": 24}})(),
        })(),
    )

    first = api.get("/standards.json?repository=owner/name")
    second = api.get("/standards.json?repository=owner/name")

    assert first.status_code == 200 and second.status_code == 200
    assert len(built) == 1, "the second look went back to GitHub"
    assert first.json()["summary"] == second.json()["summary"]
    assert "ago and kept" in second.json()["note"], (
        "the second answer does not say it is a kept one, so a reader cannot "
        "tell a fresh verdict from a held one"
    )


def test_a_different_repository_is_not_served_the_first_ones_audit(client, monkeypatch):
    api, main = client
    main._AUDIT_CACHE.clear()
    main._AUDIT_CACHE["owner/one"] = (main.time.time(), [], {"rules": 1})

    class _Real:
        failure = None
        status = None

        def paths(self):
            return ["README.md"]

    monkeypatch.setattr(main, "build_corpus", lambda *a, **k: _Real())
    monkeypatch.setattr(
        main, "check_repository",
        lambda corpus: type("R", (), {
            "results": [],
            "summary": type("S", (), {"as_dict": lambda self: {"rules": 24}})(),
        })(),
    )

    body = api.get("/standards.json?repository=owner/two").json()

    assert body["summary"]["rules"] == 24


def test_a_failure_is_never_kept(client, monkeypatch):
    """Caching a 502 would turn a one minute outage into a ten minute one."""
    api, main = client
    main._AUDIT_CACHE.clear()
    monkeypatch.setattr(
        main, "build_corpus", lambda *a, **k: _Corpus(failure="URLError: refused")
    )

    assert api.get("/standards.json?repository=owner/name").status_code == 502
    assert "owner/name" not in main._AUDIT_CACHE
