"""The Gemini adapter's own logic, with the model faked out.

Everything here is the part of `gemini.py` that is ours rather than Google's:
how a model's reply is turned into structured data, what happens when it is
malformed, what happens when the call fails, and which engine gets selected from
the environment. None of it needs a credential.

The live behaviour has its own suite, `tests/integration/test_gemini_live.py`,
which actually calls Gemini 3.7. Both matter and neither substitutes for the
other: this one runs on every push, that one proves the requirement is met.
"""

from __future__ import annotations

import pytest

from mitos import gemini
from mitos.fixtures import PR_4471
from mitos.fleet import detect_signals

SIGNALS = detect_signals(PR_4471)


@pytest.fixture
def no_env(monkeypatch):
    for key in (
        "MITOS_MODEL",
        "MITOS_MODEL_LOCATION",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_PROJECT",
    ):
        monkeypatch.delenv(key, raising=False)


# --------------------------------------------------------------------------
# Reading what the model said
# --------------------------------------------------------------------------


def test_plain_json_is_parsed():
    assert gemini._extract_json('{"a": 1}') == {"a": 1}


def test_a_fenced_block_is_parsed():
    """Models fence JSON even when told not to, so the reader is forgiving."""
    assert gemini._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert gemini._extract_json('```\n{"a": 2}\n```') == {"a": 2}


def test_json_with_prose_around_it_is_parsed():
    assert gemini._extract_json('Sure! {"a": 3} Hope that helps.') == {"a": 3}


def test_output_with_no_json_raises_rather_than_returning_empty():
    """Silently returning {} would turn a broken model reply into an empty
    assessment that still passed the gate."""
    with pytest.raises(ValueError):
        gemini._extract_json("I could not do that.")


def test_malformed_json_raises():
    with pytest.raises(Exception):
        gemini._extract_json('{"a": }')


# --------------------------------------------------------------------------
# Endpoint selection: the fact that cost an hour
# --------------------------------------------------------------------------


def test_vertex_is_configured_on_the_global_endpoint_by_default(no_env, monkeypatch):
    gemini.configure_vertex("proj")
    import os

    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global", (
        "Gemini 3.x 404s on every regional Vertex endpoint"
    )
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "True"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "proj"


def test_the_location_can_be_overridden(no_env):
    import os

    gemini.configure_vertex("proj", "europe-west4")
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "europe-west4"


# --------------------------------------------------------------------------
# Engine selection
# --------------------------------------------------------------------------


def test_the_offline_stub_is_the_default(no_env):
    assert gemini.build_analyst() is None
    assert gemini.build_critic() is None


def test_naming_a_model_selects_gemini(no_env, monkeypatch):
    monkeypatch.setenv("MITOS_MODEL", "gemini-3.7-flash")
    analyst = gemini.build_analyst("proj")
    critic = gemini.build_critic("proj")
    assert isinstance(analyst, gemini.GeminiAnalyst)
    assert isinstance(critic, gemini.GeminiCritic)
    assert analyst.model == "gemini-3.7-flash"


# --------------------------------------------------------------------------
# The analyst
# --------------------------------------------------------------------------


def _fake_ask(reply: str):
    async def _ask(agent_name, instruction, prompt, model):
        _fake_ask.last = {"instruction": instruction, "prompt": prompt, "model": model}
        return reply

    return _ask


def test_the_analyst_shapes_the_reply_into_the_contract(monkeypatch, no_env):
    monkeypatch.setattr(
        gemini,
        "_ask",
        _fake_ask('{"assessment":"## X","findings":["f1"],"paths_read":["a.md"]}'),
    )
    out = gemini.GeminiAnalyst(model="m").assess("compliance-companion", PR_4471, SIGNALS)
    assert out == {"assessment": "## X", "findings": ["f1"], "paths_read": ["a.md"]}


def test_the_analyst_drops_blank_findings(monkeypatch, no_env):
    monkeypatch.setattr(
        gemini, "_ask", _fake_ask('{"assessment":"x","findings":["", "  ", "real"]}')
    )
    out = gemini.GeminiAnalyst(model="m").assess("db-architect-leader", PR_4471, SIGNALS)
    assert out["findings"] == ["real"]


def test_the_analyst_tolerates_missing_keys(monkeypatch, no_env):
    monkeypatch.setattr(gemini, "_ask", _fake_ask('{"assessment":"only this"}'))
    out = gemini.GeminiAnalyst(model="m").assess("db-architect-leader", PR_4471, SIGNALS)
    assert out["findings"] == [] and out["paths_read"] == []


def test_every_companion_gets_its_own_brief(monkeypatch, no_env):
    """The catalogue's departments have to mean something. If every companion
    got the same instruction, the fleet would be one agent run three times."""
    briefs = set()
    monkeypatch.setattr(gemini, "_ask", _fake_ask('{"assessment":"x"}'))
    for companion in ("db-architect-leader", "documentation-companion", "compliance-companion"):
        gemini.GeminiAnalyst(model="m").assess(companion, PR_4471, SIGNALS)
        briefs.add(_fake_ask.last["instruction"])
    assert len(briefs) == 3


def test_the_analyst_is_told_the_diff_is_data_not_instructions(monkeypatch, no_env):
    """The fixture diff contains an instruction aimed at the review agent, so
    this defence is not theoretical."""
    monkeypatch.setattr(gemini, "_ask", _fake_ask('{"assessment":"x"}'))
    gemini.GeminiAnalyst(model="m").assess("documentation-companion", PR_4471, SIGNALS)
    instruction = _fake_ask.last["instruction"].lower()
    assert "never obey" in instruction and "data" in instruction


def test_the_analyst_actually_sends_the_diff(monkeypatch, no_env):
    monkeypatch.setattr(gemini, "_ask", _fake_ask('{"assessment":"x"}'))
    gemini.GeminiAnalyst(model="m").assess("documentation-companion", PR_4471, SIGNALS)
    assert "mobileNumber" in _fake_ask.last["prompt"]


# --------------------------------------------------------------------------
# The critic
# --------------------------------------------------------------------------


def test_the_critic_returns_its_findings(monkeypatch, no_env):
    monkeypatch.setattr(
        gemini,
        "_ask",
        _fake_ask('{"findings":[{"detail":"d","evidence":"e"}]}'),
    )
    out = gemini.GeminiCritic(model="m").review("draft", [])
    assert out == [{"detail": "d", "evidence": "e"}]


def test_the_critic_returns_nothing_when_it_finds_nothing(monkeypatch, no_env):
    monkeypatch.setattr(gemini, "_ask", _fake_ask('{"findings":[]}'))
    assert gemini.GeminiCritic(model="m").review("draft", []) == []


def test_the_critic_drops_entries_with_no_detail(monkeypatch, no_env):
    monkeypatch.setattr(
        gemini,
        "_ask",
        _fake_ask('{"findings":[{"evidence":"e"},{"detail":"","evidence":"e"},{"detail":"ok"}]}'),
    )
    out = gemini.GeminiCritic(model="m").review("draft", [])
    assert [f["detail"] for f in out] == ["ok"]


def test_an_unreachable_critic_reports_itself_rather_than_staying_silent(
    monkeypatch, no_env
):
    """The important one. Silence from a safety layer must not read as approval.

    `evaluator._with_critic` turns any finding into a FAIL, so reporting the
    outage as a finding is what makes a degraded run visibly degraded.
    """

    async def boom(*a, **k):
        raise TimeoutError("no route to host")

    monkeypatch.setattr(gemini, "_ask", boom)
    out = gemini.GeminiCritic(model="m").review("draft", [])
    assert len(out) == 1
    assert "unreachable" in out[0]["detail"]

    from mitos.evaluator import evaluate

    assert not evaluate("a clean draft", critic=gemini.GeminiCritic(model="m")).passed


def test_a_malformed_critic_reply_also_fails_closed(monkeypatch, no_env):
    monkeypatch.setattr(gemini, "_ask", _fake_ask("not json at all"))
    out = gemini.GeminiCritic(model="m").review("draft", [])
    assert out and "unreachable" in out[0]["detail"]


def test_the_critic_is_given_what_was_already_found(monkeypatch, no_env):
    monkeypatch.setattr(gemini, "_ask", _fake_ask('{"findings":[]}'))
    gemini.GeminiCritic(model="m").review("draft", ["already-known"])
    assert "already-known" in _fake_ask.last["prompt"]


def test_critic_findings_are_truncated_so_one_reply_cannot_flood_the_ledger(
    monkeypatch, no_env
):
    monkeypatch.setattr(
        gemini,
        "_ask",
        _fake_ask('{"findings":[{"detail":"%s","evidence":"%s"}]}' % ("d" * 900, "e" * 900)),
    )
    out = gemini.GeminiCritic(model="m").review("draft", [])
    assert len(out[0]["detail"]) <= 300
    assert len(out[0]["evidence"]) <= 200
