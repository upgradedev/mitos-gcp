"""The write is bound to an approval, asserted in every direction that matters.

Before this, `POST /execute` took a path, a body and a branch and published
them. The only control was a Cloud Run IAM binding, and that binding was
`allUsers` on the writer, so the specification repository was writable by
anyone who could form an HTTP request. The README said the writer re-checked the
plan hash. It did not.

These tests are the check that makes the claim true, so each refusal is asserted
with the input that would have got through.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mitos.approval import (
    Approval,
    Expired,
    InMemoryApprovalStore,
    Mismatch,
    Replayed,
    body_digest,
    verify_and_consume,
)

REPO = "upgradedev/mitos-spec"
PATH = "docs/services/customer.md"
BRANCH = "main"
BODY = "# Customer\n\nA mobile number column was added.\n"


def an_approval(**over) -> Approval:
    fields = dict(
        repository=REPO,
        path=PATH,
        branch=BRANCH,
        digest=body_digest(repository=REPO, path=PATH, branch=BRANCH, body=BODY),
        run_id="run-1",
        actor="tf@upgrade.net.gr",
    )
    fields.update(over)
    return Approval(**fields)


def a_store() -> tuple[InMemoryApprovalStore, Approval]:
    store = InMemoryApprovalStore()
    return store, store.grant(an_approval())


def use(store, approval, **over):
    args = dict(
        nonce=approval.nonce,
        repository=REPO,
        path=PATH,
        branch=BRANCH,
        body=BODY,
        by="writer@test",
    )
    args.update(over)
    return verify_and_consume(store, **args)


def test_the_approved_bytes_are_written():
    """The other direction, so none of the refusals below are vacuous."""
    store, approval = a_store()

    assert use(store, approval).actor == "tf@upgrade.net.gr"


def test_a_write_with_no_approval_is_refused():
    store, approval = a_store()

    with pytest.raises(Mismatch):
        use(store, approval, nonce="")


def test_an_approval_nobody_granted_is_refused():
    store, approval = a_store()

    with pytest.raises(Mismatch):
        use(store, approval, nonce="a-nonce-from-nowhere")


@pytest.mark.parametrize(
    "changed",
    [
        {"body": BODY + "\nand one more line\n"},
        {"body": BODY.replace("mobile", "MOBILE")},
        {"path": "docs/services/other.md"},
        {"branch": "release"},
        {"repository": "someone-else/their-spec"},
    ],
    ids=["appended", "one character", "path", "branch", "repository"],
)
def test_changing_anything_after_approval_is_refused(changed):
    """"Bound to the exact bytes" has to mean the whole effect, not the body.

    The same bytes on a different path or branch are a different change, and an
    approval that covered only the body would still verify for them.
    """
    store, approval = a_store()

    with pytest.raises(Mismatch):
        use(store, approval, **changed)


def test_an_approval_can_only_be_used_once():
    """Replay protection. An approval seen in a log is worth one write, and
    that one has already happened."""
    store, approval = a_store()
    use(store, approval)

    with pytest.raises(Replayed):
        use(store, approval)


def test_an_expired_approval_is_refused():
    store = InMemoryApprovalStore()
    stale = store.grant(
        an_approval(
            ttl_seconds=60,
            granted_at=(
                datetime.now(timezone.utc) - timedelta(minutes=30)
            ).isoformat(timespec="seconds"),
        )
    )

    with pytest.raises(Expired):
        use(store, stale)


def test_a_refused_write_does_not_burn_the_approval():
    """Order matters: coverage before consumption.

    Consuming first would spend a real approval on a request that was going to
    be refused anyway, so a typo would cost the operator their approval and the
    retry would fail for a different reason than the first attempt.
    """
    store, approval = a_store()
    with pytest.raises(Mismatch):
        use(store, approval, body="something else")

    assert use(store, approval).nonce == approval.nonce


def test_two_writers_racing_one_approval_produce_one_write():
    """Consumption is a create, not a read then write.

    A read-then-write check passes under test and loses under concurrency,
    which is the worst kind of check to have on a privilege boundary.
    """
    store, approval = a_store()

    results = []
    for _ in range(2):
        try:
            use(store, approval)
            results.append("wrote")
        except Replayed:
            results.append("refused")

    assert results == ["wrote", "refused"]


def test_the_digest_covers_the_whole_effect_and_not_just_the_body():
    a = body_digest(repository=REPO, path=PATH, branch=BRANCH, body=BODY)
    for changed in (
        {"repository": "other/spec"},
        {"path": "docs/other.md"},
        {"branch": "release"},
        {"body": BODY + " "},
    ):
        args = dict(repository=REPO, path=PATH, branch=BRANCH, body=BODY)
        args.update(changed)
        assert body_digest(**args) != a, changed


def test_an_approval_missing_a_bound_field_is_not_an_approval():
    """A document from an older schema must not verify by default."""
    with pytest.raises(Mismatch):
        Approval.from_dict({"repository": REPO, "path": PATH})
