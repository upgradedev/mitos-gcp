"""A delivery is handled once, even when two instances get it at the same time.

GitHub retries a delivery it did not get a timely answer for, and Cloud Run runs
up to four readers, so the same pull request can arrive twice within seconds on
two different instances. Nothing keyed on the delivery id, so both ran the whole
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
    """A retried delivery is GitHub behaving correctly.

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
