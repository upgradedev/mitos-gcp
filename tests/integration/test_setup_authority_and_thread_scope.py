"""Two P0 defects, both reachable by anybody, both verified before being fixed.

**Anyone could bind their own GitHub App to this deployment.** The manifest
callback checked `mitos_github_manifest_state` against a cookie the same
response had just set. That is CSRF protection, and nothing else asked who the
caller was. So: load `/github/app/new`, click through, create an App under your
own GitHub account, and GitHub redirects back with a code and the state you were
handed. The callback then wrote your private key, your client secret and your
webhook secret into this deployment's Secret Manager and overwrote the Firestore
record. After that you hold the webhook secret, so the reader accepts deliveries
you sign, and mints installation tokens with your key.

**The public thread served the entire ledger.** `GET /thread` returned
`ledger().all()`, unfiltered, and the webhook handler appends the analysis of
every real pull request to that same ledger. Nothing leaked while no real
analysis had ever run, which is precisely why it was invisible: it would have
begun publishing repository names, findings and provenance the first time the
product was used, anonymously, to anyone. It was verified live that the endpoint
returns 400 entries and no repository names today.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITOS_LEDGER", "memory")
    monkeypatch.setenv("MITOS_DEMO_MODE", "true")
    monkeypatch.setenv("MITOS_PUBLIC_URL", "https://mitos.example.test")
    import service.main as main

    # `DEMO_MODE` is read once at import (service/main.py:95), so setting the
    # environment variable only works if this file is the first to import the
    # module. It is not, when the whole suite runs. Set the attribute the gate
    # actually reads.
    monkeypatch.setattr(main, "DEMO_MODE", True)

    return TestClient(main.app, raise_server_exceptions=False), main


def test_a_stranger_cannot_open_the_page_that_binds_an_app(client, monkeypatch):
    api, _ = client
    monkeypatch.setenv("MITOS_SETUP_TOKEN", "the-owners-token")

    response = api.get("/github/app/new")

    assert response.status_code == 403, response.text
    assert "setup token" in response.json()["detail"]


def test_a_deployment_with_no_token_refuses_rather_than_allows(client, monkeypatch):
    """The safe direction. Forgetting to configure it must not reopen the hole."""
    api, _ = client
    monkeypatch.delenv("MITOS_SETUP_TOKEN", raising=False)

    response = api.get("/github/app/new?setup_token=anything")

    assert response.status_code == 403
    assert "MITOS_SETUP_TOKEN" in response.json()["detail"]


def test_a_wrong_token_is_refused(client, monkeypatch):
    api, _ = client
    monkeypatch.setenv("MITOS_SETUP_TOKEN", "the-owners-token")

    assert api.get("/github/app/new?setup_token=nearly").status_code == 403


def test_the_owner_with_the_token_still_gets_the_page(client, monkeypatch):
    """The refusal has to let the one legitimate caller through, or it is not a
    fix, it is a removal."""
    api, _ = client
    monkeypatch.setenv("MITOS_SETUP_TOKEN", "the-owners-token")

    response = api.get("/github/app/new?setup_token=the-owners-token")

    assert response.status_code == 200
    assert "Continue to GitHub" in response.text
    assert response.cookies.get("mitos_github_manifest_state")


def test_the_header_works_as_well_as_the_query(client, monkeypatch):
    api, _ = client
    monkeypatch.setenv("MITOS_SETUP_TOKEN", "the-owners-token")

    response = api.get("/github/app/new", headers={"X-Mitos-Setup-Token": "the-owners-token"})

    assert response.status_code == 200


def test_the_state_cookie_is_only_issued_to_an_authorised_caller(client, monkeypatch):
    """The callback trusts that cookie, and GitHub's redirect cannot carry a
    header of ours, so the authority has to be proven where the cookie is set."""
    api, _ = client
    monkeypatch.setenv("MITOS_SETUP_TOKEN", "the-owners-token")

    refused = api.get("/github/app/new")

    assert refused.status_code == 403
    assert "mitos_github_manifest_state" not in refused.cookies


def _entry(main, repository=None):
    from mitos.ledger import Entry

    return Entry(
        kind="finding.raised",
        actor="db-architect-leader",
        subject="a column",
        payload={"repository": repository, "detail": "retention"},
        run_id="run-1",
    )


def test_the_public_thread_never_serves_an_entry_that_names_a_repository(client, monkeypatch):
    api, main = client
    led = main.ledger()
    led.append(_entry(main, repository=None))
    led.append(_entry(main, repository="acme/private-billing"))

    body = api.get("/thread?limit=50").json()

    served = [e for e in body["entries"]]
    assert served, "the demo corpus entry should still be served"
    for entry in served:
        assert not (entry.get("payload") or {}).get("repository"), entry
    assert "acme/private-billing" not in api.get("/thread?limit=50").text


def test_the_public_thread_says_what_it_is_scoped_to(client):
    api, _ = client

    body = api.get("/thread?limit=5").json()

    assert "demo corpus" in body["scope"]
    assert "/api/workspace/thread" in body["scope"]


def test_the_filter_reads_the_payload_because_entry_has_no_repository_field(client):
    """`Entry` carries kind, actor, subject, payload, parent_id, run_id,
    entry_id and recorded_at. The chore puts the repository in the payload, so
    a filter on an attribute would silently match nothing."""
    _, main = client
    from mitos.ledger import Entry

    assert not hasattr(Entry(kind="k", actor="a", subject="s"), "repository")
    assert main._names_a_repository(_entry(main, repository="acme/x")) is True
    assert main._names_a_repository(_entry(main, repository=None)) is False
