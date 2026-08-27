"""The App registration flow, end to end, minus the one click nobody can automate.

Everything after the form POST was untested. `github_app_manifest_callback`,
`_store_github_app_secret`, `/github/app/install` and `/github/app/setup/callback`
appeared in the test suite only inside docstrings that mentioned them by name.

That is the worst possible place for a gap. GitHub returns an App's credentials
in the conversion response and **never again**. A defect anywhere in this path
is discovered by the owner, once, after the App exists, and the recovery is
deleting it on github.com and starting over. The Terraform bug found earlier
this week lived exactly here: the runtime writes three secrets and the project
declared one, and nothing would have failed until a real App was created.

What cannot be automated is one step: the click on github.com that converts the
manifest into an App. There is no API for it. GitHub requires a signed-in human
to consent, which is the correct design for something that creates an account
resource, and driving that UI with somebody's session would be both fragile and
wrong. So the seam is exactly there: this fakes GitHub's redirect back to us,
with a `code` and the `state` we issued, and everything on our side of that line
is real.

The remaining honest gap is GitHub's own contract. A stub cannot notice that
`PUT /contents` began requiring a blob sha; only a real call can. That is
`tests/integration/test_gemini_live.py`'s argument applied to a different API,
and it needs a test App whose private key lives in CI secrets, which is a
decision rather than an oversight.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

# `/github/app/new` now requires a setup token. It used to answer 200 to
# anyone, and the manifest callback checked only a state cookie the same
# response had just set, so any visitor could bind their own GitHub App to
# this deployment. These tests asked for the page without proving anything,
# which is how they kept passing over the hole.
SETUP_TOKEN = "test-setup-token"

# Not shaped like a real key on purpose. A PEM header in a fixture is a PEM
# header in the repository, and gitleaks is the first stage of CI with no
# ignore file by standing rule, so it found this and was right to. What these
# tests need is a distinctive string that must never reach a log or a
# document, and it does not have to look like a key to be that.
PEM = "private-key-material-that-must-never-be-logged"
CLIENT_SECRET = "cs-secret-value"
WEBHOOK_SECRET = "wh-secret-value"

CONVERSION = {
    "id": 91011,
    "slug": "mitos-change-intelligence",
    "client_id": "Iv1.abcdef",
    "client_secret": CLIENT_SECRET,
    "pem": PEM,
    "webhook_secret": WEBHOOK_SECRET,
    "owner": {"login": "upgradedev"},
}


class _Response:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else CONVERSION

    def json(self):
        return self._payload


class _Doc:
    """Enough Firestore to record what the callback wrote."""

    def __init__(self, sink):
        self.sink = sink

    def set(self, document):
        self.sink.append(document)


class _Collection:
    def __init__(self, sink):
        self.sink = sink

    def document(self, _name):
        return _Doc(self.sink)


class _Firestore:
    def __init__(self, sink):
        self.sink = sink

    def collection(self, _name):
        return _Collection(self.sink)


@pytest.fixture()
def flow(monkeypatch):
    """The service, with GitHub and the two stores replaced and recorded."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITOS_PUBLIC_URL", "https://mitos.example.test")
    monkeypatch.setenv("MITOS_LEDGER", "memory")
    monkeypatch.setenv("MITOS_STAGE", "prod")
    monkeypatch.setenv("MITOS_SETUP_TOKEN", SETUP_TOKEN)

    import service.main as main

    stored: dict[str, str] = {}
    written: list[dict] = []
    posted: list[tuple[str, dict]] = []

    def fake_post(url, **kwargs):
        posted.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(main.httpx, "post", fake_post)
    monkeypatch.setattr(
        main, "_store_github_app_secret", lambda secret_id, value: stored.__setitem__(secret_id, value)
    )

    # Injected rather than imported. `google-cloud-firestore` is a spike
    # dependency, so a laptop running the offline suite does not have it, and a
    # test of our callback should not need somebody else's client library to
    # decide whether our callback is correct.
    import sys
    import types

    fake = types.ModuleType("google.cloud.firestore")
    fake.Client = lambda project=None: _Firestore(written)
    fake.SERVER_TIMESTAMP = "<server timestamp>"
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)

    client = TestClient(main.app, raise_server_exceptions=False)
    return client, main, {"stored": stored, "written": written, "posted": posted}


def _begin(client, main):
    """`/github/app/new`, returning the state it issued and the cookie jar."""
    from service.manifest import parse

    page = client.get(f"/github/app/new?setup_token={SETUP_TOKEN}")
    assert page.status_code == 200, page.text
    action, manifest = parse(page.text)
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(action).query)["state"][0]
    return state, manifest


def test_the_whole_flow_from_the_form_to_a_configured_app(flow):
    """The seam is GitHub's click. Everything on our side of it is real here."""
    client, main, seen = flow
    state, manifest = _begin(client, main)

    assert manifest["redirect_url"].endswith("/github/app/manifest/callback")

    done = client.get(
        f"/github/app/manifest/callback?code=abc123&state={state}",
        follow_redirects=False,
    )

    assert done.status_code == 303, done.text
    assert done.headers["location"] == "/#repositories?github_app=created"

    url, kwargs = seen["posted"][0]
    assert url == "https://api.github.com/app-manifests/abc123/conversions"
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"


def test_the_three_credentials_are_stored_under_the_names_that_read_them(flow):
    """The bug that shipped: the runtime writes three secrets and Terraform
    declared one, under a name nothing reads. Asserted on the exact ids, because
    a typo here is discovered by the owner after the App exists."""
    client, main, seen = flow
    state, _ = _begin(client, main)

    client.get(f"/github/app/manifest/callback?code=abc123&state={state}", follow_redirects=False)

    assert seen["stored"] == {
        "mitos-prod-github-app-private-key": PEM,
        "mitos-prod-github-app-client-secret": CLIENT_SECRET,
        "mitos-prod-github-app-webhook-secret": WEBHOOK_SECRET,
    }


def test_the_record_written_carries_the_pointer_and_never_the_credential(flow):
    """Firestore holds where the secrets are, not what they are. The private key
    in a document is a private key in every backup and every export of it."""
    client, main, seen = flow
    state, _ = _begin(client, main)

    client.get(f"/github/app/manifest/callback?code=abc123&state={state}", follow_redirects=False)

    record = seen["written"][0]
    assert record["app_id"] == 91011
    assert record["slug"] == "mitos-change-intelligence"
    assert record["secret_prefix"] == "mitos-prod-github-app"
    assert record["credentials_stored"] is True

    body = json.dumps(record, default=str)
    for secret in (PEM, CLIENT_SECRET, WEBHOOK_SECRET):
        assert secret not in body, "a credential reached the Firestore document"


def test_a_state_that_was_never_issued_is_refused(flow):
    """The whole point of the state: a link somebody else made, pointing at our
    callback, must not complete a registration."""
    client, main, seen = flow
    _begin(client, main)

    refused = client.get(
        "/github/app/manifest/callback?code=abc123&state=not-the-one-we-issued",
        follow_redirects=False,
    )

    assert refused.status_code == 400
    assert seen["posted"] == [], "it called GitHub before checking the state"
    assert seen["stored"] == {}


def test_a_callback_with_no_cookie_at_all_is_refused(flow):
    """The cookie is the other half. Without it there is nothing to compare
    against, and comparing against nothing must not pass."""
    client, main, seen = flow
    state, _ = _begin(client, main)
    client.cookies.clear()

    refused = client.get(
        f"/github/app/manifest/callback?code=abc123&state={state}", follow_redirects=False
    )

    assert refused.status_code == 400
    assert seen["stored"] == {}


def test_the_state_is_spent_once(flow):
    """The cookie is deleted on success, so replaying the same redirect cannot
    register a second time against a code GitHub has already converted."""
    client, main, seen = flow
    state, _ = _begin(client, main)

    first = client.get(
        f"/github/app/manifest/callback?code=abc123&state={state}", follow_redirects=False
    )
    assert first.status_code == 303
    assert 'mitos_github_manifest_state=""' in first.headers.get(
        "set-cookie", ""
    ) or "Max-Age=0" in first.headers.get("set-cookie", "")

    replay = client.get(
        f"/github/app/manifest/callback?code=abc123&state={state}", follow_redirects=False
    )
    assert replay.status_code == 400


def test_a_conversion_github_refuses_is_not_reported_as_a_registration(flow, monkeypatch):
    client, main, seen = flow
    state, _ = _begin(client, main)
    monkeypatch.setattr(main.httpx, "post", lambda url, **kw: _Response(status_code=422, payload={}))

    refused = client.get(
        f"/github/app/manifest/callback?code=abc123&state={state}", follow_redirects=False
    )

    assert refused.status_code == 502
    assert seen["stored"] == {}


def test_a_storage_failure_says_the_app_exists_and_leaks_no_credential(flow, monkeypatch, capsys):
    """The worst moment in the whole flow. GitHub has already created the App
    and returned credentials it will not return again, and we could not keep
    them. The answer has to say so, and must not print what it was holding."""
    client, main, seen = flow
    state, _ = _begin(client, main)

    def explode(secret_id, value):
        raise RuntimeError("permission denied on the secret")

    monkeypatch.setattr(main, "_store_github_app_secret", explode)

    failed = client.get(
        f"/github/app/manifest/callback?code=abc123&state={state}", follow_redirects=False
    )

    assert failed.status_code == 503
    assert "created" in failed.json()["detail"].lower()

    printed = capsys.readouterr()
    logged = printed.out + printed.err
    assert "github_app.storage_failed" in logged
    for secret in (PEM, CLIENT_SECRET, WEBHOOK_SECRET):
        assert secret not in logged, "a credential was printed while reporting the failure"


def test_install_sends_you_to_create_one_when_there_is_none(flow, monkeypatch):
    client, main, _ = flow
    monkeypatch.setattr(main, "_github_app_metadata", lambda: {})
    monkeypatch.delenv("MITOS_GITHUB_APP_SLUG", raising=False)

    hop = client.get("/github/app/install", follow_redirects=False)

    assert hop.status_code == 302
    assert hop.headers["location"] == "/github/app/new"


def test_install_sends_you_to_github_when_there_is_one(flow, monkeypatch):
    client, main, _ = flow
    monkeypatch.setattr(main, "_github_app_metadata", lambda: {"slug": "mitos-change-intelligence"})

    hop = client.get("/github/app/install", follow_redirects=False)

    assert hop.status_code == 302
    assert hop.headers["location"] == (
        "https://github.com/apps/mitos-change-intelligence/installations/new"
    )


def test_the_setup_return_carries_the_installation_into_the_interface(flow):
    client, _, _ = flow

    back = client.get(
        "/github/app/setup/callback?installation_id=555&setup_action=install",
        follow_redirects=False,
    )

    assert back.status_code == 303
    assert back.headers["location"] == (
        "/#repositories?installation_id=555&setup_action=install"
    )


@pytest.mark.parametrize("installation_id", ["0", "-1"])
def test_a_setup_return_without_a_real_installation_is_refused(flow, installation_id):
    client, _, _ = flow

    refused = client.get(
        f"/github/app/setup/callback?installation_id={installation_id}",
        follow_redirects=False,
    )

    assert refused.status_code == 400


# The sign-in callback, which had no test of any kind either. Found by mutation:
# deleting its state comparison broke nothing in a suite of 689.
#
# Only the CSRF check is covered here, deliberately. The rest of that function
# exchanges a code for a token, reads the GitHub profile and projects workspace
# memberships across several Firestore collections, and a faithful stub for it is
# a day's work for a path that is not on the submission's critical line. The
# state comparison is different: it is a security control, it is two lines, and
# it was load-bearing with nothing holding it.


def test_a_sign_in_callback_with_the_wrong_state_is_refused(flow):
    """Without this, any page can link to our callback with a code it obtained
    and have the browser complete a sign-in against it."""
    client, _, seen = flow
    client.get("/github/auth/start", follow_redirects=False)

    refused = client.get(
        "/github/auth/callback?code=abc&state=not-the-one-we-issued",
        follow_redirects=False,
    )

    assert refused.status_code == 400
    assert "state" in refused.json()["detail"].lower()
    assert seen["posted"] == [], "it went to GitHub before checking the state"


def test_a_sign_in_callback_with_no_cookie_is_refused(flow):
    """Comparing against nothing must not pass. `not expected` is the half of
    that condition a constant-time comparison cannot provide."""
    client, _, seen = flow

    refused = client.get(
        "/github/auth/callback?code=abc&state=anything", follow_redirects=False
    )

    assert refused.status_code == 400
    assert seen["posted"] == []
