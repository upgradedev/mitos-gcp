"""The model has a deadline, and missing it makes the fleet stricter not broken.

Measured before this existed: three consecutive classifications of the same
small diff on the global Vertex endpoint took 91.2s, 19.0s and 13.4s. The median
was fine and the tail was not, and nothing anywhere imposed a deadline, so a
slow call was absorbed in full and then retried up to four times.

One missing timeout produced three symptoms that looked unrelated: the same
recorded demo ran 156s, 209s and 645s at one pace, `POST /run` stopped answering
inside two minutes, and the deployed judge suite failed with a connection reset
after ten.

The other half of the fix is what happens when the deadline is missed. Adding a
timeout without it would have traded a hang for a crash.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from mitos.evaluator import Verdict, _with_critic
from mitos.fleet import route, route_with_model
from mitos.gemini import ModelTooSlow, _run
from mitos.fixtures import PR_4471


# --------------------------------------------------------------------------
# The deadline
# --------------------------------------------------------------------------


async def _hangs():
    await asyncio.sleep(60)


def test_a_call_that_never_returns_is_cut_off():
    started = time.monotonic()

    with pytest.raises(ModelTooSlow):
        _run(lambda: _hangs(), attempts=1, attempt_timeout=0.2, total_budget=5.0)

    assert time.monotonic() - started < 3.0


def test_bounded_attempts_cannot_add_up_to_an_unbounded_wait():
    """The mistake a per-attempt timeout invites.

    Four attempts at forty-five seconds plus backoff is seven minutes, which is
    not a bound anybody can plan around, so the loop is bounded twice.
    """
    started = time.monotonic()

    with pytest.raises(ModelTooSlow):
        _run(lambda: _hangs(), attempts=8, attempt_timeout=0.3, total_budget=1.5)

    assert time.monotonic() - started < 4.0


def test_a_quota_error_is_reported_as_a_quota_error():
    """Relabelling it as slowness sends the next reader to look at latency for
    a problem that is a billing quota."""

    async def busy():
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    with pytest.raises(RuntimeError, match="quota"):
        _run(busy, attempts=2, attempt_timeout=5.0, total_budget=5.0)


def test_a_call_that_answers_in_time_is_not_disturbed():
    """So none of the above is a timeout that simply refuses everything."""

    async def quick():
        return {"ok": True}

    assert _run(quick, attempts=2, attempt_timeout=5.0, total_budget=5.0) == {"ok": True}


def test_the_backoff_carries_jitter():
    """Four specialists retrying a shared endpoint in lockstep is a thundering
    herd of our own making."""
    import inspect

    from mitos import gemini

    source = inspect.getsource(gemini._run)
    assert "SystemRandom().uniform" in source, "the backoff sleeps a fixed interval"


# --------------------------------------------------------------------------
# What a missed deadline does to the fleet
# --------------------------------------------------------------------------


class _Unreachable:
    def classify(self, pr):
        raise ModelTooSlow("the model did not answer within 45s")

    def review(self, draft, findings):
        raise ModelTooSlow("the model did not answer within 45s")


def test_an_unreachable_model_leaves_the_dispatch_exactly_as_the_rules_made_it():
    """Union-only means an absent model contributes nothing.

    It must not contribute a crash either: before the deadline existed this call
    simply hung, and a deadline without this would have swapped one failure for
    a louder one.
    """
    deterministic = route(PR_4471)

    dispatch, divergence = route_with_model(PR_4471, _Unreachable())

    assert {s.name for s in dispatch.signals} == {s.name for s in deterministic.signals}
    assert set(dispatch.woken) == set(deterministic.woken)
    assert divergence["model_reached"] is False
    assert "ModelTooSlow" in divergence["why"]


def test_an_unreachable_critic_says_so_on_the_card_rather_than_going_quiet():
    """A card the critic never saw and a card it had nothing to add to look
    identical unless one of them says so, and the person approving cannot tell.
    """
    verdict = Verdict(passed=True, findings=[], advisories=[], checked=["secret-scan"])

    out = _with_critic(verdict, "a draft", _Unreachable())

    assert out.passed is True, "an unreachable critic must not flip the gate"
    assert "model-critic-unavailable" in out.checked
    assert any("did not run" in a.detail for a in out.advisories), out.advisories


def test_an_unreachable_model_never_makes_the_gate_more_permissive():
    """ADR-002 at the point where the model is absent rather than wrong."""
    failing = Verdict(
        passed=False,
        findings=[],
        advisories=[],
        checked=["secret-scan"],
    )

    out = _with_critic(failing, "a draft", _Unreachable())

    assert out.passed is False


def test_a_tool_using_call_gets_a_budget_sized_for_a_tool_loop():
    """A classification is one exchange; an agentic call is several.

    Held to the one-shot deadline, the agentic specialist came back having
    refused without opening anything, which reads exactly like an agent that
    guessed. The live suite caught it. The two budgets are separate because the
    two things are different sizes of work, not because one was inconvenient.
    """
    from mitos.gemini import (
        _AGENT_BUDGET_S,
        _AGENT_TIMEOUT_S,
        _ATTEMPT_TIMEOUT_S,
        _TOTAL_BUDGET_S,
    )

    assert _AGENT_TIMEOUT_S > _ATTEMPT_TIMEOUT_S
    assert _AGENT_BUDGET_S > _TOTAL_BUDGET_S


def test_both_tool_using_paths_actually_use_that_budget():
    """Declaring the constant and not passing it is the failure this catches."""
    import inspect

    from mitos import gemini

    for method in (gemini.AgenticSpecialist.assess, gemini.StandardsReader.read):
        source = inspect.getsource(method)
        assert "_AGENT_TIMEOUT_S" in source, f"{method.__qualname__} uses the one-shot deadline"
        assert "_AGENT_BUDGET_S" in source, f"{method.__qualname__} uses the one-shot budget"
