"""`configured: false` must mean there is no App, not that we could not look.

`_github_app_metadata()` returned `{}` for both, and `/github/app/status`
turned that into `configured: false`, which the interface renders as "Create
your Mitos GitHub App".

Told that during a Firestore outage, somebody creates a second GitHub App while
the first one exists. Cleaning that up is manual work on github.com, and the
credentials GitHub returns for an App it returns exactly once, so the wrong one
is not recoverable by retrying.

The callers that only mint installation tokens are right not to care: no
credentials either way, and they already raise. Only the endpoint whose job is
to report readiness needs the distinction.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITOS_LEDGER", "memory")
    import service.main as main

    return TestClient(main.app, raise_server_exceptions=False), main


def test_a_store_that_did_not_answer_is_not_reported_as_no_app(client, monkeypatch):
    api, main = client
    monkeypatch.setattr(
        main, "_read_github_app_metadata",
        lambda: ({}, "ServiceUnavailable: 503 failed to connect"),
    )

    body = api.get("/github/app/status").json()

    assert body["configured"] is None, (
        "false here means there is no App, and the truth is that we could not look"
    )
    assert "ServiceUnavailable" in body["status_unavailable"]


def test_an_empty_store_really_is_no_app(client, monkeypatch):
    """The distinction has to cut both ways, or it is just a null."""
    api, main = client
    monkeypatch.setattr(main, "_read_github_app_metadata", lambda: ({}, ""))
    monkeypatch.setattr(main, "_webhook_secret", lambda: main.NO_SECRET_CONFIGURED)

    body = api.get("/github/app/status").json()

    assert body["configured"] is False
    assert body["status_unavailable"] is None
    # `null`, not a link. The route is owner-only, so advertising it to every
    # visitor produced a prominent button whose only outcome was 403, which
    # reads as a broken product rather than a closed door.
    assert body["create_url"] is None
    assert "setup token" in body["setup"]["needs"]
    assert "terraform" in body["setup"]["how"]


def test_a_configured_app_is_reported_as_configured(client, monkeypatch):
    api, main = client
    monkeypatch.setattr(
        main, "_read_github_app_metadata",
        lambda: ({"slug": "mitos-change-intelligence", "credentials_stored": True}, ""),
    )

    body = api.get("/github/app/status").json()

    assert body["configured"] is True
    assert body["app_slug"] == "mitos-change-intelligence"
    assert body["install_url"] == "/github/app/install"


def test_the_status_never_carries_a_credential(client, monkeypatch):
    """The docstring on the route promises this. Asserted rather than trusted,
    because the record it reads is the one holding the private key."""
    api, main = client
    monkeypatch.setattr(
        main, "_read_github_app_metadata",
        lambda: ({
            "slug": "s", "credentials_stored": True, "app_id": 1,
            "pem": "private-key-material",
            "client_secret": "shhh", "webhook_secret": "alsoshhh",
        }, ""),
    )

    text = api.get("/github/app/status").text

    for secret in ("private-key-material", "shhh", "alsoshhh"):
        assert secret not in text, f"{secret!r} reached the status payload"
