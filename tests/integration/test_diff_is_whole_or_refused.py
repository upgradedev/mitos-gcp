"""A verdict over part of a change is a verdict about a different change.

`_fetch_diff` made one call to `/pulls/{n}/files` and took what came back.
GitHub returns thirty files by default, so a pull request of seventy was
analysed as twenty-eight: reproduced against github/docs#45585, where
`changed_files` is 70, page one holds 30, and two of those are plain-text JSON
with no `patch` key that the old comprehension dropped in silence.

It sent no credential, so a private repository answered 404. And it was not
pinned: `/pulls/{n}/files` reports the pull request as it is now, and cannot be
pinned. `sha`, `ref`, `head_sha` and `commit_sha` were each tried against the
live API and all four returned a response identical to the unparameterised one.

The harm is not the missing files, it is the verdict. `run_chore` appends
`evaluator.verdict` with `passed = not findings` and no installation guard, so a
short read produces fewer findings, `passed=True`, and a completed governance
verdict for a change the fleet read forty per cent of. That entry is what
`/thread` and `metrics.py` show. The GitHub check run is a no-op until an App
exists, so a fix that stops at `conclusion="neutral"` fixes nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _GitHub:
    """Answers keyed on what the URL is asking for."""

    def __init__(self, changed_files, pages=None, compare=None, base="basesha"):
        self.changed_files = changed_files
        self.pages = pages or []
        self.compare = compare
        self.base = base
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "/compare/" in url:
            return _Response({"files": self.compare or []})
        if url.endswith(tuple(f"/pulls/{n}" for n in range(1, 20))):
            return _Response({"changed_files": self.changed_files, "base": {"sha": self.base}})
        page = (kwargs.get("params") or {}).get("page", 1)
        return _Response(self.pages[page - 1] if page <= len(self.pages) else [])


def _file(name, patch="@@ -1 +1 @@"):
    return {"filename": name, "patch": patch}


@pytest.fixture()
def main(monkeypatch):
    monkeypatch.setenv("MITOS_LEDGER", "memory")
    import service.main as module

    return module


def test_a_seventy_file_change_is_not_read_as_twenty_eight(main, monkeypatch):
    """The reproduced number. Two pages, exhausted."""
    github = _GitHub(
        changed_files=70,
        pages=[[_file(f"a{i}.py") for i in range(100)][:100], [_file(f"b{i}.py") for i in range(0)]],
    )
    github.pages = [[_file(f"a{i}.py") for i in range(70)]]
    monkeypatch.setattr(main, "httpx", github)

    diff = main._fetch_diff("owner/repo", 1)

    assert len(diff.files) == 70
    assert diff.whole is False, "unpinned reads must not claim to be whole"
    assert "pinned" in diff.reason


def test_a_pinned_compare_is_used_when_the_change_is_small_enough(main, monkeypatch):
    github = _GitHub(changed_files=3, compare=[_file("a.py"), _file("b.py"), _file("c.py")])
    monkeypatch.setattr(main, "httpx", github)

    diff = main._fetch_diff("owner/repo", 1, head_sha="headsha")

    assert diff.whole is True
    assert diff.pinned_to == "headsha"
    assert any("/compare/basesha...headsha" in url for url, _ in github.calls)


def test_a_change_too_large_to_compare_falls_back_and_says_it_is_unpinned(main, monkeypatch):
    """`/compare` cannot paginate its files array and is documented to cap at
    300, while `/pulls/{n}/files` paginates to 3000. Swapping wholesale would
    make very large pull requests read LESS than before."""
    github = _GitHub(changed_files=400, pages=[[_file(f"a{i}.py") for i in range(400)]])
    monkeypatch.setattr(main, "httpx", github)

    diff = main._fetch_diff("owner/repo", 1, head_sha="headsha")

    assert not any("/compare/" in url for url, _ in github.calls)
    assert diff.whole is False and "pinned" in diff.reason


def test_a_file_with_no_patch_is_counted_rather_than_dropped(main, monkeypatch):
    github = _GitHub(
        changed_files=3,
        compare=[_file("a.py"), {"filename": "big.json"}, _file("c.py")],
    )
    monkeypatch.setattr(main, "httpx", github)

    diff = main._fetch_diff("owner/repo", 1, head_sha="headsha")

    assert len(diff.files) == 2
    assert diff.whole is False
    assert "big.json" in diff.reason


def test_a_count_that_disagrees_with_github_is_not_whole(main, monkeypatch):
    github = _GitHub(changed_files=10, compare=[_file("a.py")])
    monkeypatch.setattr(main, "httpx", github)

    diff = main._fetch_diff("owner/repo", 1, head_sha="headsha")

    assert diff.whole is False
    assert "10 changed files and 1 were read" in diff.reason


def test_the_installation_token_is_only_sent_when_there_is_one(main, monkeypatch):
    """A read path that always needs a token is a read path that can be used to
    write, which is the argument this project makes about itself."""
    github = _GitHub(changed_files=1, compare=[_file("a.py")])
    monkeypatch.setattr(main, "httpx", github)
    monkeypatch.setattr(main, "_github_installation_token", lambda i: "tok-9")

    main._fetch_diff("owner/repo", 1, head_sha="h")
    assert all("Authorization" not in (k.get("headers") or {}) for _, k in github.calls)

    github.calls.clear()
    main._fetch_diff("owner/repo", 1, head_sha="h", installation_id=42)
    assert all(
        (k.get("headers") or {}).get("Authorization") == "Bearer tok-9" for _, k in github.calls
    )


def test_the_diff_still_behaves_as_the_list_its_callers_expect(main, monkeypatch):
    github = _GitHub(changed_files=2, compare=[_file("a.py"), _file("b.py")])
    monkeypatch.setattr(main, "httpx", github)

    diff = main._fetch_diff("owner/repo", 1, head_sha="h")

    assert len(diff) == 2
    assert [f["path"] for f in diff] == ["a.py", "b.py"]
    assert bool(diff) is True
