"""The interface offered approval to a role the service refuses.

`ProductViews.tsx` enabled the approve button for `owner` or `reviewer`. The
service enforces `_require_role(request, workspace_id, frozenset({"owner"}))`.
So a reviewer was shown an enabled control whose only possible outcome is a 403,
which reads as a broken product rather than as a boundary being held.

The same panel also rendered, at the same time, an enabled "Approve suggested
PR" button and a note saying "Approval is not yet available in this deployment".
Both conditions were `run.plans > 0`, so they appeared together. The note was
left over from before the endpoint existed.

Text over both files, standard library only, for the reason
`test_offline_suite_stays_offline.py` exists.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VIEWS = (REPO / "web" / "src" / "views" / "ProductViews.tsx").read_text(encoding="utf-8")
SERVICE = (REPO / "service" / "main.py").read_text(encoding="utf-8")


def test_the_service_still_requires_an_owner_to_approve():
    """The premise. If this changes, the assertion below is about nothing."""
    approve = SERVICE[SERVICE.index("suggested-changes/approve") :][:2000]

    assert 'frozenset({"owner"})' in approve, (
        "the approval endpoint no longer requires owner, so the interface rule "
        "below needs revisiting rather than the test deleting"
    )


def test_the_interface_does_not_offer_approval_to_a_reviewer():
    match = re.search(r"const canApprove = ([^;]+);", VIEWS)

    assert match, "canApprove is gone; who may approve is now decided elsewhere"
    assert "reviewer" not in match.group(1), (
        f"the button is enabled for a role the service refuses: {match.group(1)}"
    )
    assert "owner" in match.group(1)


def test_no_panel_says_approval_is_unavailable_while_offering_it():
    for phrase in (
        "Approval is not yet available",
        "not yet available in this deployment",
    ):
        assert phrase not in VIEWS, (
            f"the interface still says {phrase!r} beside a working approve button"
        )


def test_the_panel_says_what_approval_is_bound_to():
    """Replacing a false statement with nothing would also pass the test above."""
    assert "sha256" in VIEWS
    assert "until an owner approves" in VIEWS


# ---------------------------------------------------------------------------
# The second opinion reaches the person approving
# ---------------------------------------------------------------------------
#
# The requirement was not "call a second model". It was that the human-facing
# approval card visibly changes when the second model has something to add. A
# call that only writes an invisible log satisfies the first and not the second,
# and the first on its own is a claim rather than a feature.


def _proposed_change_component() -> str:
    start = VIEWS.index("function ProposedChange(")
    return VIEWS[start : VIEWS.index("function RunDetail(", start)]


def test_the_service_still_hands_the_advisories_to_the_browser():
    """The premise, checked the same way as the one above: the assertion after
    this is about nothing if the endpoint stops sending them."""
    detail = SERVICE[SERVICE.index("suggested-changes/") :][:6000]

    assert '"advisories"' in detail


def test_the_card_renders_them_rather_than_counting_them():
    card = _proposed_change_component()

    assert "change.advisories.map" in card, (
        "the approval card no longer renders the advisory text, so a second "
        "opinion reaches a log and not the person approving"
    )


def test_they_sit_above_the_confirmation_and_not_below_it():
    """Order is the whole point. "I have read these bytes and I am approving
    this write" is the last thing on the card, and a second opinion printed
    after it is a second opinion the reader ticks past."""
    card = _proposed_change_component()

    assert card.index("change.advisories.map") < card.index("I have read these bytes")


def test_the_card_does_not_overstate_what_the_second_model_did():
    """It cannot approve, cannot clear a finding and cannot change the verdict.
    The card says so, next to the advisories, because a reader who thinks a
    model judged this change is reading a different product than the one that
    exists."""
    card = _proposed_change_component()
    block = card[card.index("change.advisories.length") :]

    assert "cannot approve" in block
    assert "cannot change the result" in block


def test_nothing_is_shown_when_there_is_nothing_to_say():
    """An empty amber panel on every card teaches a reader to ignore the amber
    panel."""
    card = _proposed_change_component()

    assert "change.advisories.length > 0 &&" in card
