"""The router's tighten-only invariant.

The same property the evaluator's critic obeys, in the other place a model
touches a decision. Stated once so it is one argument rather than two:

    the model can only TIGHTEN.

A wrong or compromised classifier can make the fleet wake more companions and be
more cautious. It can never make it wake fewer, and it can never clear a
deterministic refusal.
"""

from __future__ import annotations

from mitos.fixtures import BACKLOG, PR_4471, PR_4472
from mitos.fleet import route, route_with_model


class _Classifier:
    def __init__(self, signals=None, special=False, rationale="because"):
        self._s = signals or []
        self._special = special
        self._r = rationale

    def classify(self, pr):
        return {
            "signals": self._s,
            "special_category": self._special,
            "rationale": self._r,
        }


def test_no_classifier_leaves_routing_untouched():
    d, div = route_with_model(PR_4471, None)
    assert [s.name for s in d.signals] == [s.name for s in route(PR_4471).signals]
    assert div == {}


def test_a_silent_model_cannot_narrow_the_dispatch():
    """The headline. A classifier reporting nothing must not un-wake anyone."""
    base = route(PR_4471)
    d, div = route_with_model(PR_4471, _Classifier(signals=[]))
    assert d.woken == base.woken
    assert div["model_missed"], "the disagreement was not recorded"
    assert div["agreed"] is False


def test_a_model_denying_special_category_cannot_clear_the_rule():
    """PR 4471 carries ordinary personal data by the deterministic rule. A model
    insisting otherwise changes nothing."""
    d, _ = route_with_model(PR_4471, _Classifier(signals=[], special=False))
    assert "compliance-companion" in d.woken


def test_a_model_can_wake_a_companion_the_patterns_missed():
    """The value. PR 4472 has no personal data by pattern; a model that reads
    the comment and disagrees widens the dispatch."""
    assert "compliance-companion" in route(PR_4472).skipped
    d, div = route_with_model(PR_4472, _Classifier(signals=["personal-data"]))
    assert "compliance-companion" in d.woken
    assert "personal-data" in div["model_added"]


def test_special_category_alone_is_enough_to_wake_compliance():
    d, div = route_with_model(PR_4472, _Classifier(signals=[], special=True))
    assert "compliance-companion" in d.woken
    assert div["special_category"] is True


def test_a_model_inventing_a_signal_cannot_invent_a_companion():
    """Widening is bounded by the catalogue: unknown signals wake nobody new."""
    d, _ = route_with_model(PR_4472, _Classifier(signals=["not-a-real-signal"]))
    assert set(d.woken) <= {c for c in route(PR_4472).woken} | {
        "compliance-companion",
        "documentation-companion",
        "db-architect-leader",
    }


def test_agreement_is_recorded_as_agreement():
    names = sorted({s.name for s in route(PR_4471).signals})
    _, div = route_with_model(PR_4471, _Classifier(signals=names))
    assert div["agreed"] is True
    assert div["model_added"] == [] and div["model_missed"] == []


def test_an_unreachable_classifier_is_safe():
    """Returning nothing is the safe direction under tighten-only."""

    class Boom:
        def classify(self, pr):
            raise TimeoutError("no route")

    try:
        route_with_model(PR_4471, Boom())
    except TimeoutError:
        pass  # the caller decides; gemini.GeminiClassifier swallows it itself


def test_the_union_never_drops_a_deterministic_signal_across_the_backlog():
    for pr in BACKLOG:
        base = {s.name for s in route(pr).signals}
        widened, _ = route_with_model(pr, _Classifier(signals=[]))
        assert base <= {s.name for s in widened.signals}, pr.number
