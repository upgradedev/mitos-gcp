"""A delivery is handled once, even when two instances get it at the same time.

Cloud Run runs up to four readers, so one delivery can be handed to two
instances, and a person clicking Redeliver sends the same delivery id again.

Not because GitHub retries on its own. It does not: "GitHub does not
automatically redeliver failed deliveries", and this handler answers 202 in
milliseconds before the work starts, so GitHub records a success and there is
nothing for a retry policy to act on even if one existed. These comments said
the opposite for a long time, and the mechanism below is right for the reasons
that are actually true rather than the one that was assumed. Nothing keyed on the delivery id, so both ran the whole
chore: four model calls each, and two accounts of one event in the thread that
is supposed to be the account.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mitos.once import AlreadySeen, InMemoryClaims


def test_the_first_claim_succeeds_and_the_second_does_not():
    claims = InMemoryClaims()
    claims.claim("delivery-1")

    with pytest.raises(AlreadySeen):
        claims.claim("delivery-1")


def test_a_different_delivery_is_not_blocked_by_the_first():
    """Otherwise one webhook would close the door on every later one."""
    claims = InMemoryClaims()
    claims.claim("delivery-1")

    claims.claim("delivery-2")


def test_claiming_records_when_and_what_so_a_duplicate_can_be_explained():
    claims = InMemoryClaims()
    claims.claim("delivery-1", note="upgradedev/mitos-spec#4471")

    seen = claims.seen("delivery-1")
    assert seen["note"] == "upgradedev/mitos-spec#4471"
    assert seen["at"]


def test_an_unclaimed_delivery_reads_as_unseen_rather_than_raising():
    assert InMemoryClaims().seen("never-delivered") is None


def test_the_claim_is_taken_before_anything_is_appended_or_started():
    """Order is the whole property.

    Claiming after the append leaves the duplicate entry in the thread, and
    claiming after the thread starts leaves the duplicate work running. Asserted
    against the source because the alternative is a live race.
    """
    source = (Path(__file__).resolve().parents[2] / "service" / "main.py").read_text(
        encoding="utf-8"
    )
    claim_at = source.index("claims().claim(delivery.delivery_id")
    append_at = source.index('kind="trigger.webhook"')
    thread_at = source.index("threading.Thread(target=work")

    assert claim_at < append_at, "the duplicate is appended before it is refused"
    assert claim_at < thread_at, "the duplicate chore starts before it is refused"


def test_a_duplicate_is_answered_with_success_not_an_error():
    """A repeated delivery id is normal, from a race or from Redeliver.

    Answering it with a failure is how a webhook gets disabled, and a disabled
    webhook is a fleet that stops hearing about pull requests.
    """
    source = (Path(__file__).resolve().parents[2] / "service" / "main.py").read_text(
        encoding="utf-8"
    )
    after = source[source.index("except AlreadySeen:") :][:600]

    assert '"accepted": True' in after
    assert '"duplicate": True' in after
    assert "status_code=4" not in after and "status_code=5" not in after


def test_an_abandoned_claim_expires_so_the_delivery_can_be_handled_again():
    """The regression this file was written to prevent, and then caused.

    The first version marked the delivery permanently on receipt, before the
    work started. An instance that died in between left a claim with nothing
    behind it, so the same delivery arriving again was answered "duplicate" and
    the chore never ran: duplicate work traded for lost work, which is strictly
    worse here, because a duplicate is visible in the thread and a silent loss
    is not.

    The name used to say the crash "does not lose the delivery". It does. What
    a lease buys is that a LATER arrival of the same delivery id can proceed
    instead of being refused by a claim nobody is behind. Nothing produces that
    later arrival on its own: GitHub does not automatically redeliver, and this
    handler answers 202 before the work starts, so GitHub records a success.
    Somebody clicking Redeliver produces it. The distinction is the whole
    difference between a recoverable loss and a recovered one.
    """
    from mitos.once import _expired

    abandoned = {"at": "2020-01-01T00:00:00+00:00", "done": False, "note": ""}

    assert _expired(abandoned) is True


def test_a_finished_delivery_is_refused_forever():
    """The other direction, so the lease is not simply a timer that forgets."""
    from mitos.once import _expired

    old_and_done = {"at": "2020-01-01T00:00:00+00:00", "done": True}

    assert _expired(old_and_done) is False


def test_work_still_running_is_not_taken_over():
    claims = InMemoryClaims()
    claims.claim("d1")

    with pytest.raises(AlreadySeen):
        claims.claim("d1")


def test_a_completed_claim_cannot_be_reclaimed():
    claims = InMemoryClaims()
    claims.claim("d1")
    claims.complete("d1", outcome="chore finished")

    with pytest.raises(AlreadySeen):
        claims.claim("d1")


def test_an_unreadable_timestamp_counts_as_abandoned():
    """A claim nobody can date is a claim nobody can rely on.

    Refusing the retry for the sake of a field we cannot parse loses the
    delivery, which is the failure this whole mechanism exists to avoid.
    """
    from mitos.once import _expired

    assert _expired({"at": "not a date", "done": False}) is True
    assert _expired({"done": False}) is True


def test_the_lease_outlasts_the_slowest_run_observed():
    """Taking a lease from a run that is still going produces exactly the
    duplicate this exists to prevent. The slowest chore measured against live
    Gemini was 303 seconds."""
    from mitos.once import LEASE_SECONDS

    assert LEASE_SECONDS > 303


def test_success_closes_the_lease_and_failure_leaves_it_open():
    """A failed run should be retryable. Completing it on the way out would
    tell GitHub's next delivery that the work already happened."""
    source = (Path(__file__).resolve().parents[2] / "service" / "main.py").read_text(
        encoding="utf-8"
    )
    handler = source[source.index("def work() -> None:") :][:3000]

    assert "claims().complete(" in handler
    fail_at = handler.index('kind="trigger.failed"')
    complete_at = handler.index("claims().complete(")
    assert complete_at > fail_at, "the lease is closed before the failure branch"
    assert "else:" in handler[fail_at:complete_at], (
        "completion is not on the success path only"
    )
