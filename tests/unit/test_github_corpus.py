"""Reading a real repository.

The adapter that turns Mitos from a demonstration into something you can point
at your own code. Before it, the diff was yours and the specifications the
agents compared it against were ours, which produces confident findings about a
repository that does not exist.

The transport is faked here so the suite stays offline and fast. That it works
against a real repository is asserted in the live suite.
"""

from __future__ import annotations

import json

import pytest

from mitos.tools import (
    DEFAULT_PREFIXES,
    DictCorpus,
    GitHubCorpus,
    ReadLog,
    build_corpus,
    make_tools,
)

TREE = {
    "tree": [
        {"type": "blob", "path": "docs/specs/customer.md"},
        {"type": "blob", "path": "docs/specs/billing.md"},
        {"type": "blob", "path": "src/app.py"},
        {"type": "blob", "path": ".github/workflows/ci.yml"},
        {"type": "blob", "path": "secrets.env"},
        {"type": "tree", "path": "docs/specs"},
    ]
}


def corpus(scope=None, tree=None, bodies=None, fail=False):
    c = GitHubCorpus("owner/repo", ref="main", scope=scope)
    calls = []

    def fake_get(url):
        calls.append(url)
        if fail:
            raise OSError("no route to host")
        if "git/trees" in url:
            return json.dumps(tree if tree is not None else TREE).encode()
        return (bodies or {}).get(url.rsplit("/", 1)[-1], "body").encode()

    c._get = fake_get  # noqa: SLF001
    c.calls = calls
    return c


def test_only_paths_inside_the_scope_are_visible():
    c = corpus(scope=("docs/",))
    assert c.paths() == ["docs/specs/billing.md", "docs/specs/customer.md"]


def test_a_wider_scope_sees_more():
    assert "src/app.py" in corpus(scope=("docs/", "src/")).paths()


def test_directories_are_not_files():
    """The tree API returns both. An agent asked to read a directory gets a
    confusing error rather than a file."""
    assert "docs/specs" not in corpus(scope=("docs/",)).paths()


def test_things_outside_the_scope_are_invisible_not_merely_refused():
    """Invisible matters. A listing that mentions `secrets.env` tells an agent
    it exists, which is information it was not given."""
    listed = corpus(scope=("docs/",)).paths()
    assert "secrets.env" not in listed
    assert ".github/workflows/ci.yml" not in listed


def test_the_listing_is_one_call_however_many_paths():
    """A walk is a request per directory, and an agent that explores would spend
    its whole budget navigating."""
    c = corpus(scope=("docs/", "src/"))
    c.paths()
    c.paths()
    assert sum(1 for u in c.calls if "git/trees" in u) == 1


def test_reading_the_same_file_twice_fetches_once():
    c = corpus(scope=("docs/",))
    c.read("docs/specs/billing.md")
    c.read("docs/specs/billing.md")
    assert sum(1 for u in c.calls if "raw.githubusercontent" in u) == 1


def test_reading_something_outside_the_scope_raises_key_error():
    """Same shape as the dictionary corpus, so the tool reports 'no such file'
    rather than leaking a transport error to the model."""
    with pytest.raises(KeyError):
        corpus(scope=("docs/",)).read("secrets.env")


def test_an_unreachable_repository_is_empty_rather_than_an_exception():
    """The agent finds nothing, says so, and the run is visibly thin instead of
    silently wrong."""
    c = corpus(fail=True)
    assert c.paths() == []


def test_the_tools_refuse_a_path_outside_the_scope_they_were_given():
    log = ReadLog()
    _, read_file, _ = make_tools(corpus(scope=("docs/",)), log, scope=("docs/",))
    out = read_file("src/app.py")
    assert out["readable"] is False
    assert "outside the readable scope" in out["error"]


def test_the_tools_read_a_path_inside_the_scope():
    log = ReadLog()
    _, read_file, _ = make_tools(corpus(scope=("docs/",)), log, scope=("docs/",))
    assert read_file("docs/specs/billing.md")["readable"] is True
    assert log.reads == 1


# --------------------------------------------------------------------------
# Which corpus gets built
# --------------------------------------------------------------------------


def test_no_repository_means_the_demo_corpus():
    assert isinstance(build_corpus(), DictCorpus)


def test_a_repository_means_the_real_one():
    c = build_corpus("owner/repo", ref="main", scope=("docs/",))
    assert isinstance(c, GitHubCorpus)
    assert c.repository == "owner/repo"
    assert c.ref == "main"
    assert c.scope == ("docs/",)


def test_the_default_scope_is_used_when_none_is_given():
    assert build_corpus("owner/repo").scope == DEFAULT_PREFIXES


# --------------------------------------------------------------------------
# The repository name reaches a URL, so it is validated before it gets there
# --------------------------------------------------------------------------


REJECTED = [
    # Escapes the /repos/ prefix once a client normalises the path, so the
    # request lands on a different GitHub endpoint than the one intended.
    "../../user",
    "a/b/../../../orgs/github",
    # Truncates the path, so the trailing /git/trees/HEAD never arrives.
    "a/b#frag",
    # Appends parameters to somebody else's request.
    "a/b?per_page=1&x=",
    "noslash",
    "/leading",
    "trailing/",
    "a b/c",
    "a/b c",
    "-lead/x",
    "a/..",
    "",
]

ACCEPTED = [
    "upgradedev/archon-gcp-agentic",
    "a/b",
    "x-y/z.dot_underscore",
    "A1/B2",
]


@pytest.mark.parametrize("repository", REJECTED)
def test_a_repository_name_that_could_steer_a_url_is_refused(repository):
    """`repository` arrives from a query string on a public page.

    No credential is sent today, so the worst case is a wrong public read. The
    README already contemplates a token for private repositories, and a token
    would turn this into a credentialed request to an endpoint the caller
    picked.
    """
    with pytest.raises(ValueError):
        GitHubCorpus(repository=repository)


@pytest.mark.parametrize("repository", ACCEPTED)
def test_a_real_repository_name_is_not_refused(repository):
    """The other direction, so the check is not simply refusing everything."""
    assert GitHubCorpus(repository=repository).repository == repository


@pytest.mark.parametrize("ref", ["../etc", "x..y", "a" * 300, "with space"])
def test_a_ref_that_could_steer_a_url_is_refused(ref):
    with pytest.raises(ValueError):
        GitHubCorpus(repository="a/b", ref=ref)


@pytest.mark.parametrize("ref", ["HEAD", "main", "v1.2.3", "feature/x", "a" * 40])
def test_a_real_ref_is_not_refused(ref):
    assert GitHubCorpus(repository="a/b", ref=ref).ref == ref


def test_the_refusal_happens_before_any_request_is_made():
    """Validation at construction, not at call time.

    A check that only runs when a read happens leaves every other caller
    holding an object that will misbehave later.
    """
    with pytest.raises(ValueError):
        GitHubCorpus(repository="../../user")
