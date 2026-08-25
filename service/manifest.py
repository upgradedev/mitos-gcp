"""Validate the GitHub App manifest this service serves, without asking GitHub.

The manifest is the one artefact here that a third party parses and stores. Get
a URL wrong and nothing fails now: it fails minutes or days later, on GitHub's
side, with a message about GitHub rather than about us. That is what happened —
every URL came out `http://` and GitHub answered "redirect_url must be a valid
URL", which names the wrong field and none of the cause.

Three callers use these rules and must agree, which is the whole point of them
being a module rather than a check written three times:

  * `service/main.py` applies them to its own manifest before rendering, so the
    flow cannot send GitHub something we already know is wrong. That is the
    difference between an error in our words, naming the field and the cause,
    and GitHub's, which named the wrong field and none of the cause.
  * `tests/integration/test_github_app_manifest.py` renders the route in
    process, so a bad manifest cannot be merged.
  * `.github/workflows/deployed.yml` fetches the live page, so a manifest that
    is correct in the repository and wrong in production is caught by a
    schedule rather than by somebody trying to install the App.

Deliberately NOT a check against github.com. Posting the manifest to
`https://github.com/settings/apps/new` unauthenticated looks like an oracle and
is not one: the same bytes were accepted twenty times in a row and refused four
times a minute later, because that endpoint's unauthenticated behaviour depends
on session and anti-abuse state rather than on the manifest. A gate built on it
would go red on days nothing changed, and a gate people learn to ignore is worse
than no gate. `scripts/probe_manifest.py` keeps that round trip as a diagnostic,
clearly marked as not a gate.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

# `ping` is GitHub's confirmation that a webhook was configured; it is delivered
# on creation and is not subscribable. `installation` and
# `installation_repositories` go to every App by default and cannot be
# subscribed to either. Asking for them is at best ignored, and an event name
# GitHub does not accept refuses the whole manifest.
SUBSCRIBABLE = {
    "check_run",
    "check_suite",
    "issue_comment",
    "issues",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "push",
    "release",
    "status",
    "workflow_run",
}


class _Form(HTMLParser):
    """Pull the form action and the manifest payload out of the page.

    An HTML parser rather than a regular expression, because the manifest is
    HTML-escaped JSON full of quotes and slashes and CLAUDE.md has a standing
    rule about regexes here that was written after one lost a byte in transit.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action: str | None = None
        self.manifest_json: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and a.get("id") == "manifest":
            self.action = a.get("action")
        if tag == "input" and a.get("name") == "manifest":
            self.manifest_json = a.get("value")


def parse(page: str) -> tuple[str, dict]:
    """Return `(form action, manifest)` or raise with what was missing."""
    form = _Form()
    form.feed(page)
    if form.action is None:
        raise ValueError("no <form id=manifest> in the page")
    if form.manifest_json is None:
        raise ValueError("no <input name=manifest> in the form")
    return form.action, json.loads(form.manifest_json)


def _absolute_https(value: object) -> str | None:
    """Describe what is wrong with `value` as a URL, or None if nothing is."""
    if not isinstance(value, str) or not value:
        return "missing or not a string"
    parsed = urlparse(value)
    if parsed.scheme != "https":
        return f"scheme is {parsed.scheme or 'absent'}, not https"
    if not parsed.netloc:
        return "no host, so it is a path rather than a URL"
    return None


def problems(action: str, manifest: dict) -> list[str]:
    """Every reason GitHub would be right to refuse this, most direct first."""
    found: list[str] = []

    # Where the form posts. `form-action https://github.com` in the CSP already
    # constrains this, but the CSP is a header and this is the document; a page
    # that posts the manifest somewhere else is worth naming loudly.
    posted_to = urlparse(action)
    if (posted_to.scheme, posted_to.netloc) != ("https", "github.com"):
        found.append(f"form posts to {action!r}, not https://github.com")

    # GitHub's documented shape carries the CSRF state on the action URL, and
    # passes it back on `redirect_url` alongside `code`. Keeping it there rather
    # than in `redirect_url` means the URL GitHub stores is a bare endpoint.
    state = parse_qs(posted_to.query).get("state", [""])[0]
    if not state:
        found.append("the form action carries no state, so the callback cannot be verified")

    urls: list[tuple[str, object]] = [
        ("url", manifest.get("url")),
        ("hook_attributes.url", (manifest.get("hook_attributes") or {}).get("url")),
        ("redirect_url", manifest.get("redirect_url")),
        ("setup_url", manifest.get("setup_url")),
    ]
    urls += [
        (f"callback_urls[{i}]", u) for i, u in enumerate(manifest.get("callback_urls") or [])
    ]

    for field, value in urls:
        wrong = _absolute_https(value)
        if wrong:
            found.append(f"{field}: {wrong} ({value!r})")

    # Checking `redirect_url` alone would have passed the original bug on three
    # of these four fields, and `hook_attributes.url` is the one that matters
    # most: an http webhook endpoint receives HMAC-signed deliveries in clear.
    origins = {
        urlparse(v).netloc for _, v in urls if isinstance(v, str) and urlparse(v).netloc
    }
    if len(origins) > 1:
        found.append(f"the manifest names more than one host: {sorted(origins)}")

    if urlparse(str(manifest.get("redirect_url") or "")).query:
        found.append("redirect_url carries a query string; state belongs on the form action")

    for event in manifest.get("default_events") or []:
        if event not in SUBSCRIBABLE:
            found.append(f"default_events contains {event!r}, which cannot be subscribed to")

    return found


def check(page: str) -> list[str]:
    action, manifest = parse(page)
    return problems(action, manifest)
