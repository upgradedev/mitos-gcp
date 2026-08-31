"""The fleet's memory was one memory, shared by every repository it watched.

`run_chore` wrote every entry with the module constant `SUBJECT`
("services/customer", the demo fixture's subject) and, worse, passed that same
constant to `ledger.recall`. `recall` filters on subject and kind and nothing
else. So the "what do we already know about this subject" step read the
`finding.deferred` and `finding.raised` entries of every run in the deployment,
whichever repository they came from, plus the demo corpus.

It could not be found offline. With one repository a shared key and a scoped key
return the same rows, so every test agreed with the bug. It surfaced by
installing the GitHub App on a second repository and reading the thread of a real
run, which reported a subject that appeared nowhere in the pull request.

ADR-012 derives tenancy from the installation precisely so it cannot be forged by
a request body. That guarantee stops at the read endpoints if the fleet's own
memory is global underneath them, which is what this was.

Standard library only, per `test_offline_suite_stays_offline.py`.
"""

from __future__ import annotations

from mitos.chore import SUBJECT, subject_of
from mitos.fixtures import PullRequest
from mitos.ledger import Entry, InMemoryLedger

RECALLED = {"finding.deferred", "finding.raised"}


def _pr(paths: list[str]) -> PullRequest:
    return PullRequest(
        number=1, title="t", author="a", files=[{"path": p} for p in paths]
    )


def test_the_demo_path_keeps_the_fixture_subject():
    """`repository` is absent offline, and the corpus a stranger reads is the
    thing this project asks people to run. Changing its subject would rewrite
    what `/thread` returns for a reason unrelated to the defect."""
    assert subject_of(None, _pr(["services/customer/x.sql"])) == SUBJECT
    assert subject_of("", _pr(["services/customer/x.sql"])) == SUBJECT


def test_the_same_service_in_two_repositories_is_two_subjects():
    """The whole defect in one assertion."""
    ours = subject_of("upgradedev/mitos-gcp", _pr(["services/customer/a.sql"]))
    theirs = subject_of("upgradedev/other-repo", _pr(["services/customer/a.sql"]))

    assert ours != theirs, (
        "two repositories with a service of the same name share one memory, "
        f"both keyed on {ours!r}"
    )
    assert ours.startswith("upgradedev/mitos-gcp")
    assert theirs.startswith("upgradedev/other-repo")


def test_the_subject_is_the_service_rather_than_the_file():
    """The claim this memory supports is about a service across weeks, so two
    pull requests touching one service have to recall each other."""
    one = subject_of(
        "upgradedev/mitos-gcp", _pr(["services/customer/migrations/V1.sql"])
    )
    two = subject_of(
        "upgradedev/mitos-gcp", _pr(["services/customer/src/Model.java"])
    )

    assert one == two == "upgradedev/mitos-gcp:services/customer"


def test_a_change_with_nothing_in_common_is_keyed_on_the_repository():
    """Two services in one pull request share only `services`, and a file at the
    root shares nothing. Neither may silently borrow a narrower key."""
    assert subject_of("r/x", _pr(["README.md"])) == "r/x"
    assert subject_of("r/x", _pr([])) == "r/x"
    assert (
        subject_of("r/x", _pr(["services/customer/a.sql", "services/billing/b.sql"]))
        == "r/x:services"
    )
    assert subject_of("r/x", _pr(["a/one.sql", "b/two.sql"])) == "r/x"


def test_a_finding_raised_about_one_repository_is_not_recalled_by_another():
    """The derivation above only matters if `recall` then separates them.

    This is the assertion that would have gone red on the live defect: one
    ledger, two repositories, a finding raised about the first, and the second
    asking what it already knows.
    """
    led = InMemoryLedger()
    ours = subject_of("upgradedev/mitos-gcp", _pr(["services/customer/a.sql"]))
    theirs = subject_of("upgradedev/other-repo", _pr(["services/customer/a.sql"]))

    led.append(
        Entry(
            kind="finding.raised",
            actor="compliance-companion",
            subject=ours,
            payload={"finding": "personal data without a retention entry"},
            run_id="ours",
        )
    )

    assert len(led.recall(ours, kinds=RECALLED)) == 1, (
        "a repository can no longer recall its own findings, which is a bigger "
        "problem than the leak this test was written for"
    )
    leaked = led.recall(theirs, kinds=RECALLED)
    assert leaked == [], (
        "a finding raised about one repository was recalled by a run about "
        f"another: {[e.payload for e in leaked]}"
    )


def test_the_demo_corpus_is_not_recalled_by_a_real_repository():
    """The corpus is synthetic and served to anyone. A real run treating it as
    prior knowledge would be reasoning about a customer that does not exist."""
    led = InMemoryLedger()
    led.append(
        Entry(
            kind="finding.deferred",
            actor="compliance-companion",
            subject=SUBJECT,
            payload={"finding": "from the demo corpus", "expires_on": "2026-01-01"},
            run_id="demo",
        )
    )

    real = subject_of("upgradedev/mitos-gcp", _pr(["services/customer/a.sql"]))

    assert led.recall(real, kinds=RECALLED) == [], (
        "a real run recalled the demo corpus as prior knowledge about its own "
        "repository"
    )
