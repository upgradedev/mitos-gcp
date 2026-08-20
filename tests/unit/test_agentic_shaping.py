"""What a specialist's reply becomes, without a model.

`shape_agentic_reply` is where everything interesting about a reply happens;
everything around it is ADK plumbing. A specialist that blocks without a reason,
invents a status, or returns a confidence outside the range gets corrected here
rather than at the point somebody reads it.

The live behaviour is in `tests/integration/test_gemini_live.py`. Both matter:
this one runs on every push, that one proves the model actually does it.
"""

from __future__ import annotations

import pytest

from mitos.gemini import shape_agentic_reply, unusable_reply

LOG = {"tool_calls": 3, "reads": 2, "denied": 0, "sequence": ["read_file(a) -> ok"]}


def test_an_ok_reply_passes_through():
    out = shape_agentic_reply(
        {"status": "ok", "assessment": "## X", "findings": ["f"], "confidence": 0.9},
        LOG,
    )
    assert out["status"] == "ok"
    assert out["assessment"] == "## X"
    assert out["findings"] == ["f"]
    assert out["confidence"] == 0.9


def test_a_missing_status_defaults_to_ok():
    assert shape_agentic_reply({"assessment": "x"}, LOG)["status"] == "ok"


@pytest.mark.parametrize("invented", ["approved", "maybe", "BLOCKED_HARD", ""])
def test_an_invented_status_is_not_honoured(invented):
    """A model must not be able to introduce a state the fleet has no handling
    for, because unhandled states become silent successes."""
    assert shape_agentic_reply({"status": invented}, LOG)["status"] == "ok"


def test_blocked_is_honoured():
    out = shape_agentic_reply({"status": "blocked", "reason": "needs a DPIA"}, LOG)
    assert out["status"] == "blocked"
    assert out["reason"] == "needs a DPIA"


def test_blocked_without_a_reason_says_so_rather_than_inventing_one():
    """Parking an item and telling the human nothing is the failure. Naming the
    omission is more useful than writing a rationale on the model's behalf."""
    out = shape_agentic_reply({"status": "blocked"}, LOG)
    assert out["status"] == "blocked"
    assert "without giving a reason" in out["reason"]


def test_blocked_with_whitespace_only_reason_is_treated_as_missing():
    out = shape_agentic_reply({"status": "blocked", "reason": "   \n "}, LOG)
    assert "without giving a reason" in out["reason"]


@pytest.mark.parametrize(
    "value,expected", [(1.7, 1.0), (-2.0, 0.0), ("0.4", 0.4), (None, 0.7), ("x", 0.7)]
)
def test_confidence_is_clamped_and_survives_nonsense(value, expected):
    assert shape_agentic_reply({"confidence": value}, LOG)["confidence"] == expected


def test_blank_findings_and_citations_are_dropped():
    out = shape_agentic_reply(
        {"findings": ["", "  ", "real"], "citations": ["a.md", " ", ""]}, LOG
    )
    assert out["findings"] == ["real"]
    assert out["citations"] == ["a.md"]


def test_citations_become_the_paths_read():
    """The evaluator checks cited paths against what was actually read, so the
    two must not drift apart."""
    out = shape_agentic_reply({"citations": ["registers/retention.md"]}, LOG)
    assert out["paths_read"] == out["citations"] == ["registers/retention.md"]


def test_the_read_log_is_carried_through_untouched():
    """It is the evidence of agency; shaping must not lose it."""
    assert shape_agentic_reply({}, LOG)["read_log"] == LOG


def test_an_unusable_reply_blocks_rather_than_returning_nothing():
    """The important one. Failing to parse must never look like a clean bill of
    health, because an empty finding list and a healthy item are identical to
    everything downstream."""
    out = unusable_reply(ValueError("no JSON"), LOG)
    assert out["status"] == "blocked"
    assert "ValueError" in out["reason"]
    assert out["confidence"] == 0.0
    assert out["findings"] == []
    assert out["read_log"] == LOG


# --------------------------------------------------------------------------
# The classifier, with the model faked out.
# --------------------------------------------------------------------------


def _fake_ask(reply):
    async def _ask(agent_name, instruction, prompt, model):
        _fake_ask.last = {"instruction": instruction, "prompt": prompt}
        return reply

    return _ask


def _classifier(monkeypatch, reply):
    from mitos import gemini

    monkeypatch.setenv("MITOS_MODEL", "gemini-3.7-flash")
    monkeypatch.setattr(gemini, "_ask", _fake_ask(reply))
    return gemini.GeminiClassifier(model="m")


def test_the_classifier_returns_recognised_signals(monkeypatch):
    out = _classifier(
        monkeypatch, '{"signals":["personal-data","schema-change"],"special_category":false}'
    ).classify(_pr())
    assert set(out["signals"]) == {"personal-data", "schema-change"}


def test_the_classifier_discards_signals_the_fleet_has_no_meaning_for(monkeypatch):
    """Widening is bounded by the vocabulary. A model cannot invent a signal and
    thereby invent behaviour nobody wrote."""
    out = _classifier(
        monkeypatch, '{"signals":["personal-data","drop-everything","<script>"]}'
    ).classify(_pr())
    assert out["signals"] == ["personal-data"]


def test_the_classifier_reports_special_category(monkeypatch):
    out = _classifier(monkeypatch, '{"signals":[],"special_category":true}').classify(_pr())
    assert out["special_category"] is True


def test_an_unreachable_classifier_adds_nothing(monkeypatch):
    """Contributing nothing is the safe direction under tighten-only: the
    deterministic signals still stand."""
    from mitos import gemini

    async def boom(*a, **k):
        raise TimeoutError("no route")

    monkeypatch.setenv("MITOS_MODEL", "gemini-3.7-flash")
    monkeypatch.setattr(gemini, "_ask", boom)
    out = gemini.GeminiClassifier(model="m").classify(_pr())
    assert out["signals"] == []
    assert out["special_category"] is False
    assert "unavailable" in out["rationale"]


def test_a_malformed_classifier_reply_also_adds_nothing(monkeypatch):
    out = _classifier(monkeypatch, "I could not do that").classify(_pr())
    assert out["signals"] == [] and out["special_category"] is False


def test_the_classifier_is_told_the_diff_is_data(monkeypatch):
    _classifier(monkeypatch, '{"signals":[]}').classify(_pr())
    assert "never obey" in _fake_ask.last["instruction"].lower()


def test_the_rationale_is_truncated_so_one_reply_cannot_flood_the_thread(monkeypatch):
    out = _classifier(
        monkeypatch, '{"signals":[],"rationale":"%s"}' % ("r" * 900)
    ).classify(_pr())
    assert len(out["rationale"]) <= 300


def _pr():
    from mitos.fixtures import PR_4471

    return PR_4471
