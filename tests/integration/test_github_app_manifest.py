"""The manifest is checked as a document, by the same code that checks it live.

`test_public_url.py` proves `_public_url` returns an absolute https URL. That is
a property of a helper, and the bug was never only in the helper: the page also
posted to GitHub with no `state` on the action, carried the state on
`redirect_url` instead, asked for three webhook events that cannot be subscribed
to, and was served with `Cache-Control: private`. A unit test on the helper
passes over every one of those.

So this renders the actual route and hands the page to
`scripts/check_manifest.py`, which is the same module `deployed.yml` runs
against the live URL. One set of rules, applied in the repository and in
production, rather than two lists that drift — which has already happened twice
here with the copy checks.
"""

from __future__ import annotations

import pytest

from scripts.check_manifest import SUBSCRIBABLE, check, parse


@pytest.fixture()
def page(monkeypatch):
    """The page as the route really renders it."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITOS_PUBLIC_URL", "https://mitos.example.test")
    monkeypatch.setenv("MITOS_LEDGER", "memory")

    from service.main import app

    response = TestClient(app).get("/github/app/new")
    assert response.status_code == 200, response.text
    return response


def test_the_served_manifest_has_nothing_wrong_with_it(page):
    assert check(page.text) == []


def test_the_manifest_is_reported_against_a_page_that_is_wrong():
    """The check has to be able to fail, or it is decoration.

    Every fault below shipped at once in the deployed manifest.
    """
    broken = (
        '<form id="manifest" method="post" '
        'action="https://github.com/settings/apps/new">'
        '<input name="manifest" value="'
        "{&quot;url&quot;: &quot;http://mitos.example.test&quot;, "
        "&quot;hook_attributes&quot;: {&quot;url&quot;: "
        "&quot;http://mitos.example.test/webhook/github&quot;}, "
        "&quot;redirect_url&quot;: &quot;/github/app/manifest/callback?state=x&quot;, "
        "&quot;setup_url&quot;: &quot;http://mitos.example.test/s&quot;, "
        "&quot;default_events&quot;: [&quot;ping&quot;]}"
        '"></form>'
    )

    found = " | ".join(check(broken))

    assert "no state" in found
    assert "not https" in found
    assert "hook_attributes.url" in found
    assert "redirect_url: scheme is absent" in found
    assert "query string" in found
    assert "'ping'" in found


def test_a_url_with_a_scheme_but_no_host_is_still_refused():
    """`https:///github/auth/callback` looks absolute and is not.

    The empty-variable bug produced exactly this shape: a base of `""` joined
    to a path.
    """
    from scripts.check_manifest import _absolute_https

    assert _absolute_https("https:///github/auth/callback") == (
        "no host, so it is a path rather than a URL"
    )
    assert _absolute_https("") == "missing or not a string"
    assert _absolute_https(None) == "missing or not a string"
    assert _absolute_https("https://mitos.example.test/x") is None


def test_the_state_is_on_the_form_action_and_matches_the_cookie(page):
    """GitHub's documented shape, and the reason it is safe to use it.

    The state was on `redirect_url`, which is the URL GitHub stores. GitHub
    passes `state` back on the redirect next to `code`, so putting it on the
    action loses nothing and leaves the stored URL a bare endpoint.

    The cookie is the other half: `github_app_manifest_callback` compares them,
    so a state the browser never received would refuse the callback.
    """
    from urllib.parse import parse_qs, urlparse

    action, manifest = parse(page.text)
    state = parse_qs(urlparse(action).query)["state"][0]

    assert state
    assert page.cookies["mitos_github_manifest_state"] == state
    assert "state" not in urlparse(manifest["redirect_url"]).query


def test_the_page_is_never_stored(page):
    """A single-use token paired with a ten minute cookie must not be cached.

    It was served `Cache-Control: private` with no max-age, which leaves the
    decision to a browser heuristic. A reused copy carries a state the cookie no
    longer matches, and the failure surfaces as GitHub refusing the callback.
    """
    assert page.headers["cache-control"] == "no-store"


def test_only_events_that_can_be_subscribed_to_are_requested(page):
    """`ping` is GitHub's confirmation that a webhook exists, not an event to
    ask for; `installation` and `installation_repositories` reach every App
    already. All three were in the list."""
    _, manifest = parse(page.text)

    events = manifest["default_events"]
    assert events, "asking for no events would stop the webhook entirely"
    assert set(events) <= SUBSCRIBABLE, events
    assert "pull_request" in events, "the whole product is a reaction to a pull request"


def test_the_manifest_is_correct_behind_a_proxy_with_no_configured_url(monkeypatch):
    """The deployment path that produced the original bug.

    With `MITOS_PUBLIC_URL` absent the address comes from the request, and
    behind Cloud Run's proxy the connection this process sees is http. Every URL
    came out `http://` and GitHub refused the manifest.
    """
    from fastapi.testclient import TestClient

    monkeypatch.delenv("MITOS_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MITOS_LEDGER", "memory")

    from service.main import app

    page = TestClient(app).get(
        "/github/app/new",
        headers={"x-forwarded-proto": "https", "host": "mitos.example.test"},
    )

    assert page.status_code == 200, page.text
    assert check(page.text) == []
    _, manifest = parse(page.text)
    assert manifest["url"] == "https://mitos.example.test"
