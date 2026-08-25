"""The service has to know its own public address, or say it does not.

The GitHub App manifest is the one place this is unforgiving. Every URL in it is
stored by GitHub and called back later, so a wrong one fails minutes or days
afterwards, on GitHub's side, with a message about GitHub rather than about us.

That happened. Every URL in the deployed manifest came out `http://`:

    redirect_url:    http://mitos-reader-....run.app/github/app/manifest/callback
    hook_attributes: http://mitos-reader-....run.app/webhook/github

GitHub refused the whole manifest with "redirect_url must be a valid URL", and
the second one would have registered a plain text endpoint for an HMAC-signed
delivery, which is worse than the setup failing.

These tests are in the integration suite because they import `service.main`,
which pulls in httpx; `tests/unit` is standard library only and a guard there
enforces it.
"""

from __future__ import annotations

import os

import pytest

from service.main import NoPublicUrl, _public_url

BEHIND_A_PROXY = "http://mitos-reader-437828525303.europe-west1.run.app/"


class _Request:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _no_inherited_env(monkeypatch):
    monkeypatch.delenv("MITOS_PUBLIC_URL", raising=False)


def test_a_configured_url_is_used_as_it_is(monkeypatch):
    monkeypatch.setenv("MITOS_PUBLIC_URL", "https://mitos.example.test/")

    assert _public_url(_Request("http://internal:8080/")) == "https://mitos.example.test"


def test_an_empty_variable_is_absent_rather_than_a_value(monkeypatch):
    """`os.environ.get(name, default)` returns the empty string for a variable
    that is set and empty, so the default never runs.

    A blank `MITOS_PUBLIC_URL` produced `""`, and every URL in the manifest
    became a relative path, which is the literal thing GitHub was complaining
    about.
    """
    monkeypatch.setenv("MITOS_PUBLIC_URL", "")

    resolved = _public_url(
        _Request(BEHIND_A_PROXY, {"x-forwarded-proto": "https"})
    )

    assert resolved.startswith("https://")
    assert resolved.endswith(".run.app")


def test_whitespace_is_also_absent(monkeypatch):
    monkeypatch.setenv("MITOS_PUBLIC_URL", "   ")

    assert _public_url(
        _Request(BEHIND_A_PROXY, {"x-forwarded-proto": "https"})
    ).startswith("https://")


def test_the_forwarded_scheme_is_honoured_because_cloud_run_terminates_tls():
    """`request.base_url` reports the scheme this process saw, which is http.

    `/connect` had this exact bug and `dashboard.public_base` was written and
    tested to fix it. This helper did not use it, so the same mistake shipped
    twice in one service.
    """
    resolved = _public_url(_Request(BEHIND_A_PROXY, {"x-forwarded-proto": "https"}))

    assert resolved == "https://mitos-reader-437828525303.europe-west1.run.app"


def test_a_plain_http_origin_is_refused_rather_than_returned():
    """The failure has to happen here, where the message names this service.

    Returning `http://...` builds a manifest GitHub rejects, and registers a
    webhook endpoint without TLS if it does not.
    """
    with pytest.raises(NoPublicUrl):
        _public_url(_Request(BEHIND_A_PROXY))


@pytest.mark.parametrize(
    "configured",
    ["not a url", "ftp://example.test", "https://", "//example.test", "example.test"],
)
def test_a_configured_value_that_is_not_an_absolute_https_url_is_refused(
    monkeypatch, configured
):
    monkeypatch.setenv("MITOS_PUBLIC_URL", configured)

    with pytest.raises(NoPublicUrl):
        _public_url(_Request(BEHIND_A_PROXY))


def test_every_manifest_url_is_an_absolute_https_url(monkeypatch):
    """The property that actually matters, asserted over the whole manifest.

    Checking `redirect_url` alone would have passed while `hook_attributes.url`
    registered a plain text webhook.
    """
    import html
    import json
    import re

    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITOS_PUBLIC_URL", "https://mitos.example.test")
    monkeypatch.setenv("MITOS_LEDGER", "memory")

    from service.main import app

    page = TestClient(app).get("/github/app/new").text
    field = re.search(r'name=["\']manifest["\']\s+value=["\'](.*?)["\']', page, re.S)
    assert field, "the manifest field is no longer where this test looks"
    manifest = json.loads(html.unescape(field.group(1)))

    urls = [manifest["redirect_url"], manifest["setup_url"]]
    urls += list(manifest.get("callback_urls") or [])
    urls.append(manifest["hook_attributes"]["url"])

    assert urls, "no urls were found, so this test asserts nothing"
    for url in urls:
        assert url.startswith("https://mitos.example.test/"), url


def test_a_deployment_that_cannot_say_its_address_answers_503_not_500(monkeypatch):
    """A configuration problem, answered as one.

    Raising out of the route produced `500 Internal Server Error` with no body,
    which tells an operator nothing and reads like a bug in the flow rather than
    a missing setting.
    """
    from fastapi.testclient import TestClient

    monkeypatch.delenv("MITOS_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MITOS_LEDGER", "memory")

    from service.main import app

    response = TestClient(app, raise_server_exceptions=False).get("/github/app/new")

    assert response.status_code == 503
    body = response.json()
    assert "MITOS_PUBLIC_URL" in body["fix"]
    assert body["detail"], "the refusal does not say what it saw"
