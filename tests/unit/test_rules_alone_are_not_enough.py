"""The case the patterns cannot get right.

This suite exists because of a fair criticism: for most of this project's life
every outcome was decided by a regular expression, and the model wrote prose. A
fleet like that is a rules engine with a language model attached, and deleting
the model would change nothing.

PR 4483 is the counter-example, and this file pins the half of it that needs no
credential: **the deterministic router misses it entirely.** The column is called
`vuln_code`, which matches no pattern, and the fact that it holds medical
dependency data is stated only in a comment.

The other half, that a model reading the repository catches it and refuses, is
in `tests/integration/test_gemini_live.py` because it needs a model. Both halves
are needed. This one alone would be an argument that the rules are bad; that one
alone would be an argument that could not be checked offline.
"""

from __future__ import annotations

from mitos.envelope import Status
from mitos.fixtures import BACKLOG
from mitos.fleet import route, route_with_model, run_specialist

PR = [p for p in BACKLOG if p.number == 4483][0]


class _ModelSees:
    """A classifier standing in for one that reads the comment and understands
    it. The live suite proves Gemini actually does this."""

    def classify(self, pr):
        return {
            "signals": ["personal-data"],
            "special_category": True,
            "rationale": "medical dependency data behind an opaque column name",
        }


def test_the_field_name_matches_no_pattern():
    """If a pattern did match, the fixture would not be testing anything."""
    from mitos.fleet import PERSONAL_DATA_TERMS

    blob = PR.diff_text().lower()
    matched = [t for t in PERSONAL_DATA_TERMS if t in blob.split("//")[0].lower()]
    assert not matched, (
        f"the column name gives it away via {matched}, so the rules would catch "
        f"it and the fixture proves nothing"
    )


def test_the_rules_alone_never_wake_compliance():
    """The failure. A health-data field reaches a human as an ordinary schema
    change."""
    assert "compliance-companion" in route(PR).skipped


def test_the_rules_alone_would_complete_this_item():
    """Nobody refuses, so the item is approved and shipped."""
    dispatch = route(PR)
    for name in dispatch.woken:
        response = run_specialist(name, PR, dispatch.signals)
        assert response is None or response.status is Status.OK, (
            "something refused, so the rules do catch it after all"
        )


def test_a_model_that_reads_the_comment_widens_the_dispatch():
    """The fix, with the model's judgement stubbed. Live proof is elsewhere."""
    dispatch, divergence = route_with_model(PR, _ModelSees())
    assert "compliance-companion" in dispatch.woken
    assert divergence["special_category"] is True
    assert "personal-data" in divergence["model_added"]


def test_the_disagreement_is_recorded_not_silently_resolved():
    """Months later somebody needs to know the rules missed this and why it was
    caught anyway."""
    _, divergence = route_with_model(PR, _ModelSees())
    assert divergence["agreed"] is False
    assert divergence["rationale"]
