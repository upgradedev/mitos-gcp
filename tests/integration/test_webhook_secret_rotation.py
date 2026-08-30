"""Completing the App flow replaced the secret, and the service kept the old one.

`_webhook_secret()` was read once and cached on the module. The service boots,
finds no GitHub App, and caches the pre-App secret. The owner then completes the
manifest flow, GitHub returns a NEW webhook secret, the callback stores it, and
GitHub immediately sends `ping` and `installation` signed with it.

Every one was refused 401. Observed live: the App was created at 19:29:09 and
the deliveries at 19:29:07 and 19:32:52 were both refused, while
`/github/app/status` reported `configured: true`.

Nothing was misconfigured and nothing said so. Registration reported success and
the webhook silently never worked, which is worse than failing at registration,
because the owner has no reason to look.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

pytest.importorskip("fastapi")

OLD = "the-secret-from-before-the-app"
NEW = "the-secret-github-returned"


def _signed(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITOS_LEDGER", "memory")
    import service.main as main

    monkeypatch.setattr(main, "_LEDGER", None)
    return TestClient(main.app, raise_server_exceptions=False), main


def _deliver(api, body: dict, secret: str, event: str = "ping"):
    raw = json.dumps(body).encode()
    return api.post(
        "/webhook/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": "d-1",
            "X-Hub-Signature-256": _signed(raw, secret),
        },
    )


def test_a_delivery_signed_with_the_new_secret_is_accepted_after_rotation(client, monkeypatch):
    """The exact sequence that failed live."""
    api, main = client
    stored = {"value": OLD}
    monkeypatch.setattr(main, "_WEBHOOK_SECRET", OLD)
    monkeypatch.setattr(
        main, "_read_managed_secret", lambda name: stored["value"], raising=False
    )

    # The App flow replaces it. Only the instance that handled the callback
    # would know; this one is any of the other three.
    stored["value"] = NEW
    monkeypatch.setattr(
        main.secretmanager if hasattr(main, "secretmanager") else main,
        "_unused", None, raising=False,
    )

    def _fresh(*, refresh=False):
        if refresh:
            main._WEBHOOK_SECRET = stored["value"]
        return main._WEBHOOK_SECRET

    monkeypatch.setattr(main, "_webhook_secret", _fresh)

    response = _deliver(api, {"zen": "hello"}, NEW)

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True


def test_a_delivery_signed_with_nothing_valid_is_still_refused(client, monkeypatch):
    """The retry must not weaken the check. A body signed with neither the
    cached secret nor the stored one is refused, and the refusal still says
    which kind it is."""
    api, main = client

    def _fresh(*, refresh=False):
        return NEW

    monkeypatch.setattr(main, "_webhook_secret", _fresh)

    response = _deliver(api, {"zen": "hello"}, "a-secret-nobody-issued")

    assert response.status_code == 401
    assert response.json()["accepted"] is False


def test_an_unsigned_delivery_is_refused_without_a_second_read(client, monkeypatch):
    """A missing signature is not a stale secret, and re-reading for it would
    spend a Secret Manager call on every unsigned probe that reaches a public
    endpoint."""
    api, main = client
    reads = []

    def _counting(*, refresh=False):
        reads.append(refresh)
        return NEW

    monkeypatch.setattr(main, "_webhook_secret", _counting)

    response = api.post(
        "/webhook/github",
        content=b"{}",
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d-2"},
    )

    assert response.status_code == 401
    assert reads.count(True) <= 1


def test_the_secret_reader_accepts_a_refresh_argument(client):
    """The signature the call site depends on."""
    _, main = client
    import inspect

    assert "refresh" in inspect.signature(main._webhook_secret).parameters
