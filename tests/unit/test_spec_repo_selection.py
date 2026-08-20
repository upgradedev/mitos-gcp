"""Which publisher gets built, and what the do-nothing one reports.

The `GitSpecRepo` push itself needs git, SSH and a credential, and is exercised
against the real repository. What is covered here is the part that decides
whether anything is published at all, because getting that wrong silently is the
worst outcome available: a run that reports success and writes nothing.
"""

from __future__ import annotations

import pytest

from mitos.spec_repo import DEFAULT_BRANCH, GitSpecRepo, NullSpecRepo, build_spec_repo


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("MITOS_SPEC_REMOTE", "MITOS_SPEC_BASE", "MITOS_WRITE_SECRET"):
        monkeypatch.delenv(key, raising=False)


def test_no_remote_configured_means_nothing_is_published():
    assert isinstance(build_spec_repo(), NullSpecRepo)


def test_a_configured_remote_selects_the_real_publisher(monkeypatch):
    monkeypatch.setenv("MITOS_SPEC_REMOTE", "git@github.com:example/spec.git")
    repo = build_spec_repo("proj")
    assert isinstance(repo, GitSpecRepo)
    assert repo.remote == "git@github.com:example/spec.git"
    assert repo.base == DEFAULT_BRANCH


def test_the_base_branch_can_be_overridden(monkeypatch):
    monkeypatch.setenv("MITOS_SPEC_REMOTE", "git@github.com:example/spec.git")
    monkeypatch.setenv("MITOS_SPEC_BASE", "develop")
    assert build_spec_repo().base == "develop"


def test_the_do_nothing_publisher_says_it_published_nothing():
    """`published` is False and the reason is stated, so a caller cannot mistake
    silence for success. This is why the class is not called DryRun."""
    out = NullSpecRepo().publish(
        path="docs/x.md", body="hello", message="m", branch="b"
    )
    assert out["published"] is False
    assert "nothing was written" in out["reason"]
    assert out["path"] == "docs/x.md"
    assert out["branch"] == "b"
    assert out["bytes"] == 5


def test_the_do_nothing_publisher_measures_bytes_not_characters():
    out = NullSpecRepo().publish(path="p", body="né", message="m", branch="b")
    assert out["bytes"] == 3
