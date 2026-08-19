"""The policy, exercised directly. No ADK, no fleet, no I/O.

`is_allowed` is deliberately free of ADK types so it can be tested like this and
so the callback stays a thin adapter over it. These are the cases that decide
whether the privilege boundary means anything.
"""

from __future__ import annotations

import pytest

from mitos.guard import (
    GuardDenial,
    ROLE_EVALUATOR,
    ROLE_READER,
    ROLE_WRITER,
    WRITE_TOOLS,
    assert_allowed,
    is_allowed,
    make_before_tool_guard,
)

NON_WRITE_ROLES = [ROLE_READER, ROLE_EVALUATOR]


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
@pytest.mark.parametrize("role", NON_WRITE_ROLES)
def test_no_write_tool_is_reachable_from_a_non_write_role(tool, role):
    allowed, reason = is_allowed(tool, role)
    assert not allowed
    assert role in reason and tool in reason, "the refusal has to say what and who"


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_every_write_tool_is_reachable_from_the_writer(tool):
    allowed, _ = is_allowed(tool, ROLE_WRITER)
    assert allowed


def test_a_read_tool_is_reachable_from_every_role():
    for role in (ROLE_READER, ROLE_EVALUATOR, ROLE_WRITER):
        assert is_allowed("read_pull_request", role)[0]


def test_an_unknown_role_cannot_write():
    """Fail closed. A typo in a deployment must not grant write."""
    assert not is_allowed("write_spec_repo", "redaer")[0]
    assert not is_allowed("write_spec_repo", "")[0]


def test_assert_allowed_raises_with_the_reason():
    with pytest.raises(GuardDenial) as exc:
        assert_allowed("write_spec_repo", ROLE_READER)
    assert "write_spec_repo" in str(exc.value)
    assert_allowed("write_spec_repo", ROLE_WRITER)  # does not raise


class _Tool:
    def __init__(self, name):
        self.name = name


def test_the_callback_returns_none_to_allow():
    guard = make_before_tool_guard(ROLE_WRITER)
    assert guard(tool=_Tool("write_spec_repo"), args={}, tool_context=None) is None


def test_the_callback_returns_a_non_empty_dict_to_block():
    """Non-empty is load-bearing. ADK treats a falsy return as 'let the next
    callback run', so an empty dict would be ambiguous in a chain."""
    guard = make_before_tool_guard(ROLE_READER)
    out = guard(tool=_Tool("write_spec_repo"), args={}, tool_context=None)
    assert isinstance(out, dict)
    assert out, "an empty dict is falsy and would be ambiguous to ADK"
    assert out["denied_by"] == "mitos-guard"
    assert out["status"] == "denied"


def test_the_callback_keeps_the_parameter_names_adk_passes_by_keyword():
    """ADK calls the callback with tool=, args= and tool_context=. Renaming any
    of them is a TypeError at dispatch time, in production, not here."""
    import inspect

    params = inspect.signature(make_before_tool_guard(ROLE_READER)).parameters
    assert set(params) == {"tool", "args", "tool_context"}
    for p in params.values():
        assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_callback_handles_a_tool_with_no_name_attribute():
    guard = make_before_tool_guard(ROLE_READER)
    assert guard(tool="write_spec_repo", args=None, tool_context=None)
