"""The GitHub write path, which had no test of any kind.

`service/main.py` makes five calls that change something on GitHub: create and
update a check run, create a ref, put a file, open a pull request. 661 tests
executed none of them. They are about to run against a real repository for the
first time, so the cost of a defect here is the owner's time during the one
demonstration that matters.

Writing them found one. `PUT /repos/{owner}/{repo}/contents/{path}` requires the
blob sha of the file being replaced: GitHub's own wording is "Required if you
are updating a file." The code sent no sha, so it could create a file and could
not update one. What this feature publishes is a *repaired document*, which
means the path usually exists already, so the common case was the broken one.
The reason it looked correct is that creating a new file needs no sha.

httpx is stubbed rather than mocked with a library: each test declares the
sequence of responses GitHub would give and then asserts on the requests that
were made. No network, and the assertions are about our payloads rather than
about a mocking framework.
"""

from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("fastapi")


class _Response:
    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = {} if payload is None else payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _GitHub:
    """Every request made, and the answers handed back in order per method."""

    def __init__(self, **answers: list[_Response]) -> None:
        self.answers = {method: list(items) for method, items in answers.items()}
        self.calls: list[tuple[str, str, dict]] = []

    def _next(self, method: str, url: str, kwargs: dict) -> _Response:
        self.calls.append((method, url, kwargs))
        queue = self.answers.get(method)
        if not queue:
            raise AssertionError(f"unexpected {method} {url}")
        return queue.pop(0)

    def get(self, url, **kwargs):
        return self._next("get", url, kwargs)

    def post(self, url, **kwargs):
        return self._next("post", url, kwargs)

    def patch(self, url, **kwargs):
        return self._next("patch", url, kwargs)

    def put(self, url, **kwargs):
        return self._next("put", url, kwargs)

    def sent(self, method: str, contains: str) -> dict:
        for made, url, kwargs in self.calls:
            if made == method and contains in url:
                return kwargs.get("json") or {}
        raise AssertionError(f"no {method} to a url containing {contains!r}: {self.urls()}")

    def urls(self) -> list[str]:
        return [f"{m.upper()} {u}" for m, u, _ in self.calls]


@pytest.fixture()
def main(monkeypatch):
    monkeypatch.setenv("MITOS_LEDGER", "memory")
    import service.main as module

    # Records what each caller asked for. Both write paths used to mint an
    # installation-wide token while knowing the repository, so the stub took one
    # argument and the tests could not have noticed.
    minted: list[tuple] = []

    def _token(installation_id, repository=""):
        minted.append((installation_id, repository))
        return "tok-123"

    monkeypatch.setattr(module, "_github_installation_token", _token)
    module._minted_for_tests = minted
    return module


def _suggest(main, github, **over):
    kwargs = {
        "installation_id": 42, "repository": "owner/repo", "source_pr": 7,
        "expected_head": "headsha", "path": "docs/spec.md",
        "body": "repaired", "run_id": "run-abcdefgh",
    }
    kwargs.update(over)
    return main._github_suggested_pr(**kwargs)


def _reads(*, contents: _Response, existing_pull=None) -> list[_Response]:
    """The five GETs the helper makes.

    Source pull request, repository, base ref, the file being replaced, and the
    adoption check that asks whether a pull request already exists for this
    branch. The fifth was added when a crash between opening the pull request
    and recording it was found to leave the change stuck: the retry POSTed for a
    head that already had one, got 422, and raised.
    """
    return [
        _Response(200, {"head": {"sha": "headsha"}}),
        _Response(200, {"default_branch": "main"}),
        _Response(200, {"object": {"sha": "basesha"}}),
        contents,
        _Response(200, existing_pull if existing_pull is not None else []),
    ]


def test_updating_a_file_that_exists_sends_its_blob_sha(main, monkeypatch):
    """The defect these tests were written to find.

    Without the sha GitHub refuses the write with 422, and the document this
    feature repairs almost always exists already.
    """
    github = _GitHub(
        get=_reads(contents=_Response(200, {"sha": "blobsha", "type": "file"})),
        post=[_Response(201, {}), _Response(201, {"html_url": "u", "number": 9})],
        put=[_Response(200, {"commit": {"sha": "commitsha"}})],
    )
    monkeypatch.setattr(main, "httpx", github)

    result = _suggest(main, github)

    assert github.sent("put", "/contents/")["sha"] == "blobsha"
    assert result["published"] is True
    assert result["pull_number"] == 9


def test_creating_a_file_that_does_not_exist_sends_no_sha(main, monkeypatch):
    """A 404 from the read is the normal answer for a new path, not an error."""
    github = _GitHub(
        get=_reads(contents=_Response(404, {"message": "Not Found"})),
        post=[_Response(201, {}), _Response(201, {"html_url": "u", "number": 9})],
        put=[_Response(201, {"commit": {"sha": "commitsha"}})],
    )
    monkeypatch.setattr(main, "httpx", github)

    _suggest(main, github)

    assert "sha" not in github.sent("put", "/contents/")


def test_the_existing_file_is_read_on_the_new_branch_not_the_default(main, monkeypatch):
    """Reading the default branch would send the wrong sha whenever the branch
    already carries a change, and GitHub refuses a stale sha with 409."""
    github = _GitHub(
        get=_reads(contents=_Response(200, {"sha": "blobsha"})),
        post=[_Response(201, {}), _Response(201, {"html_url": "u", "number": 9})],
        put=[_Response(200, {"commit": {"sha": "c"}})],
    )
    monkeypatch.setattr(main, "httpx", github)

    _suggest(main, github)

    contents_reads = [k for m, u, k in github.calls if m == "get" and "/contents/" in u]
    assert contents_reads, github.urls()
    assert contents_reads[0]["params"] == {"ref": "mitos/suggestion-7-run-abcd"}


def test_a_directory_at_that_path_is_refused_by_us_and_not_by_github(main, monkeypatch):
    """A directory answers with a list. Naming the path here beats a 422 whose
    message is about a missing sha."""
    from fastapi import HTTPException

    github = _GitHub(
        get=_reads(contents=_Response(200, [{"name": "a.md"}])),
        post=[_Response(201, {})],
        put=[],
    )
    monkeypatch.setattr(main, "httpx", github)

    with pytest.raises(HTTPException) as raised:
        _suggest(main, github)

    assert raised.value.status_code == 409
    assert "docs/spec.md" in raised.value.detail


def test_a_read_that_fails_for_any_other_reason_stops_the_write(main, monkeypatch):
    """500 is not 404. Treating every non-200 as absent would send no sha and
    turn a transient failure into a refused write with a misleading message."""
    github = _GitHub(
        get=_reads(contents=_Response(500, {})),
        post=[_Response(201, {})],
        put=[],
    )
    monkeypatch.setattr(main, "httpx", github)

    with pytest.raises(RuntimeError):
        _suggest(main, github)


def test_a_moved_head_is_refused_before_anything_is_written(main, monkeypatch):
    """The approval is bound to a commit. If the pull request moved under it,
    nothing may be published against the new one."""
    from fastapi import HTTPException

    github = _GitHub(get=[_Response(200, {"head": {"sha": "somethingelse"}})])
    monkeypatch.setattr(main, "httpx", github)

    with pytest.raises(HTTPException) as raised:
        _suggest(main, github)

    assert raised.value.status_code == 409
    assert github.urls() == ["GET https://api.github.com/repos/owner/repo/pulls/7"]


def test_the_check_run_is_created_with_a_head_sha_and_updated_without_one(main, monkeypatch):
    """Create and update are different requests to different URLs, and sending
    `head_sha` on the update is rejected."""
    github = _GitHub(post=[_Response(201, {"id": 555})], patch=[_Response(200, {"id": 555})])
    monkeypatch.setattr(main, "httpx", github)

    created = main._github_check(
        repository="owner/repo", installation_id=42, head_sha="abc", status="queued",
    )
    assert created == 555
    body = github.sent("post", "/check-runs")
    assert body["head_sha"] == "abc"
    assert "conclusion" not in body

    main._github_check(
        repository="owner/repo", installation_id=42, head_sha="abc",
        status="completed", check_run_id=555, conclusion="success",
    )
    updated = github.sent("patch", "/check-runs/555")
    assert "head_sha" not in updated
    assert updated["conclusion"] == "success"


def test_a_check_without_a_head_sha_is_not_sent_at_all(main, monkeypatch):
    """There is nothing to attach a check run to, and GitHub would refuse it."""
    github = _GitHub()
    monkeypatch.setattr(main, "httpx", github)

    assert main._github_check(
        repository="owner/repo", installation_id=42, head_sha="", status="queued",
        check_run_id=77,
    ) == 77
    assert github.calls == []


def test_a_failing_check_never_breaks_the_analysis(main, monkeypatch, capsys):
    """Checks are reporting, not orchestration. A GitHub outage must not stop
    the run or reject the webhook, and the existing id has to survive so later
    updates still address the right check run."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("github is down")

    monkeypatch.setattr(main, "_github_check", explode)

    assert main._safe_github_check(check_run_id=99) == 99
    assert json.loads(capsys.readouterr().out)["event"] == "github.check_failed"


def test_the_summary_is_truncated_to_what_github_accepts(main, monkeypatch):
    github = _GitHub(post=[_Response(201, {"id": 1})])
    monkeypatch.setattr(main, "httpx", github)

    main._github_check(
        repository="owner/repo", installation_id=42, head_sha="abc",
        status="queued", summary="x" * 70000,
    )

    assert len(github.sent("post", "/check-runs")["output"]["summary"]) == 65000


def test_the_installation_token_authorises_every_write(main, monkeypatch):
    """One token, on all of them. A request that reached GitHub unauthenticated
    would fail as a permission problem rather than as a bug here."""
    github = _GitHub(
        get=_reads(contents=_Response(404, {})),
        post=[_Response(201, {}), _Response(201, {"html_url": "u", "number": 9})],
        put=[_Response(201, {"commit": {"sha": "c"}})],
    )
    monkeypatch.setattr(main, "httpx", github)

    _suggest(main, github)

    assert github.calls
    for method, url, kwargs in github.calls:
        assert kwargs["headers"]["Authorization"] == "Bearer tok-123", f"{method} {url}"


def test_the_body_is_sent_as_base64_because_the_contents_api_requires_it(main, monkeypatch):
    github = _GitHub(
        get=_reads(contents=_Response(404, {})),
        post=[_Response(201, {}), _Response(201, {"html_url": "u", "number": 9})],
        put=[_Response(201, {"commit": {"sha": "c"}})],
    )
    monkeypatch.setattr(main, "httpx", github)

    _suggest(main, github, body="# repaired\nwith a line\n")

    sent = github.sent("put", "/contents/")["content"]
    assert base64.b64decode(sent).decode("utf-8") == "# repaired\nwith a line\n"


def test_a_retry_adopts_the_pull_request_that_already_exists(main, monkeypatch):
    """Exactly once, across a crash.

    `create_ref` already treated 422 as success because the branch may survive an
    interrupted attempt. The pull request did not get the same treatment, so a
    crash between opening it and recording it left the change stuck: a real pull
    request open, and every retry a 422 raised as a 500.
    """
    github = _GitHub(
        get=_reads(
            contents=_Response(200, {"sha": "blobsha"}),
            existing_pull=[{"html_url": "https://github.com/owner/repo/pull/9", "number": 9}],
        ),
        post=[_Response(201, {})],
        put=[_Response(200, {"commit": {"sha": "c"}})],
    )
    monkeypatch.setattr(main, "httpx", github)

    result = _suggest(main, github)

    assert result["published"] is True
    assert result["pull_number"] == 9
    assert "already existed" in result["adopted"]
    opened = [u for m, u, _ in github.calls if m == "post" and u.endswith("/pulls")]
    assert not opened, "a second pull request was opened for a branch that had one"


def test_the_installation_token_is_scoped_to_one_repository(monkeypatch):
    # Not the `main` fixture: it replaces `_github_installation_token` with a
    # stub, and this test is about the real one.
    monkeypatch.setenv("MITOS_LEDGER", "memory")
    import service.main as main

    sent = {}

    class _Post:
        def post(self, url, **kwargs):
            sent.update(kwargs)
            return _Response(201, {"token": "tok"})

    monkeypatch.setattr(main, "httpx", _Post())
    monkeypatch.setattr(main, "_github_app_metadata", lambda: {"secret_prefix": "p", "app_id": 1})
    monkeypatch.setattr(main, "_read_managed_secret", lambda name: "key")
    monkeypatch.setattr(main.jwt, "encode", lambda *a, **k: "jwt")

    main._github_installation_token(42, repository="owner/private-billing")

    assert sent["json"] == {"repositories": ["private-billing"]}


def test_every_write_token_names_its_repository(main, monkeypatch):
    """An installation token with no repository carries the App's permissions
    across every repository the installation covers. Both write paths knew
    which one they were writing to and asked for all of them anyway."""
    module = main

    class _Created:
        status_code = 201

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": 5}

    # Stubbed, because the assertion is about the token the caller asks for and
    # not about GitHub. Without this the test reached api.github.com and failed
    # on a 401, which is a real request this suite has no business making.
    monkeypatch.setattr(module.httpx, "post", lambda *a, **k: _Created())

    module._github_check(
        repository="acme/billing", installation_id=42, head_sha="abc",
        status="queued",
    )

    assert module._minted_for_tests, "no token was minted, so this proves nothing"
    assert all(
        repository == "acme/billing" for _installation, repository in module._minted_for_tests
    ), module._minted_for_tests
