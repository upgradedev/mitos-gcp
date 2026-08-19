"""The handoff contract.

The one property worth protecting: a refusal must carry a reason. An agent that
parks an item and says nothing has moved the work to a human without telling
them anything, which is worse than not parking it.
"""

from __future__ import annotations

import pytest

from mitos.envelope import Outcome, Response, Status, Tainted, Trust


def test_ok_needs_no_reason():
    assert Response(companion="c", status=Status.OK).status is Status.OK


@pytest.mark.parametrize("status", [Status.BLOCKED, Status.NEEDS_CHANGES, Status.ERROR])
def test_a_refusal_without_a_reason_is_rejected_at_construction(status):
    """Caught where it is written, not where it is read."""
    with pytest.raises(ValueError) as exc:
        Response(companion="compliance-companion", status=status)
    assert "compliance-companion" in str(exc.value)
    assert status.value in str(exc.value)


@pytest.mark.parametrize("status", [Status.BLOCKED, Status.NEEDS_CHANGES])
def test_whitespace_is_not_a_reason(status):
    with pytest.raises(ValueError):
        Response(companion="c", status=status, reason="   ")


def test_only_blocked_and_error_park_the_item():
    """needs_changes is a push-back, not a stop: something upstream has to
    change, but the fleet has not given up."""
    assert Response(companion="c", status=Status.BLOCKED, reason="r").parks_the_item
    assert Response(companion="c", status=Status.ERROR, reason="r").parks_the_item
    assert not Response(companion="c", status=Status.OK).parks_the_item
    assert not Response(
        companion="c", status=Status.NEEDS_CHANGES, reason="r"
    ).parks_the_item


@pytest.mark.parametrize("bad", [-0.1, 1.1, 42.0])
def test_confidence_outside_zero_to_one_is_rejected(bad):
    with pytest.raises(ValueError):
        Response(companion="c", status=Status.OK, confidence=bad)


def test_a_response_serialises_everything_a_reader_needs():
    d = Response(
        companion="compliance-companion",
        status=Status.BLOCKED,
        reason="needs a DPIA",
        findings=["f"],
        citations=["a.sql"],
        confidence=0.7,
    ).as_dict()
    assert d["status"] == "blocked"
    assert d["reason"] == "needs a DPIA"
    assert d["citations"] == ["a.sql"]
    assert d["confidence"] == 0.7


def test_tainted_refuses_to_mark_something_trusted():
    """The type exists to say untrusted. Letting it say the opposite would make
    it decorative."""
    assert Tainted(value="diff").trust is Trust.UNTRUSTED
    with pytest.raises(ValueError):
        Tainted(value="diff", trust=Trust.TRUSTED)


def test_outcome_truncates_the_hash_for_display_but_keeps_the_state():
    o = Outcome(pr_number=1, title="t", state="completed", plan_hash="a" * 64)
    assert o.as_dict()["plan_hash"] == "a" * 16
    assert o.as_dict()["state"] == "completed"
