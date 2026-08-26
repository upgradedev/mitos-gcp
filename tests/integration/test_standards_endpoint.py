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
