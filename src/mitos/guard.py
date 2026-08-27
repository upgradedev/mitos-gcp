"""The Mitos guard: a deterministic privilege check that runs inside ADK's
tool-call interceptor, not inside a prompt.

The whole entry rests on one claim: no agent in the fleet can talk its way past
this. That is only true because the check runs in `before_tool_callback`, which
ADK evaluates before it dispatches the tool, and because the model has no way to
reach the decision. A system-prompt instruction saying "do not call write tools"
is a request. This is a control.

The claim is checkable, not asserted. ADK dispatches a tool in
`google/adk/flows/llm_flows/functions.py`: it runs every
`canonical_before_tool_callbacks` first, and only calls the tool when the
collected `function_response is None`. Returning a non-empty dict here therefore
means the tool is never invoked, and the dict becomes its result.

`tests/integration/test_adk_interceptor.py` proves it against the real dispatcher, with
a stub model that demands the write on every turn.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# Roles carried by the three Cloud Run services. Each service boots with exactly
# one, and it is not model-supplied: it comes from the deployment, so a
# compromised agent cannot elect a different one.
ROLE_READER = "reader"
ROLE_EVALUATOR = "evaluator"
ROLE_WRITER = "writer"

# Tools that mutate something outside the provenance ledger. The reader and the
# evaluator hold no credential that can call these, and the guard refuses them a
# second time in-process so a misconfigured deployment still fails closed.
WRITE_TOOLS = frozenset(
    {
        "write_spec_repo",
        "open_pull_request",
        "set_commit_status",
    }
)

# Which roles may call a write tool at all. The writer service is the only one,
# and even there the call is gated on an approved, content-addressed plan.
WRITE_ROLES = frozenset({ROLE_WRITER})


class GuardDenial(Exception):
    """Raised only by `assert_allowed`, the non-ADK entry point."""


def _denial(tool_name: str, role: str, reason: str) -> dict[str, Any]:
    """Build the tool result that stands in for the blocked call.

    It must be a NON-EMPTY dict. ADK treats a falsy return as "let the next
    callback run", and an empty dict from the last callback in the chain still
    suppresses the tool because the dispatcher tests `is None` rather than
    truthiness. Returning something non-empty is unambiguous under any number of
    callbacks, and it is what the model sees as the tool's output, so it also has
    to read as a refusal rather than as a success.
    """
    return {
        "status": "denied",
        "denied_by": "mitos-guard",
        "tool": tool_name,
        "role": role,
        "reason": reason,
    }


def is_allowed(tool_name: str, role: str) -> tuple[bool, str]:
    """The whole policy, as one pure function.

    Kept free of ADK types so it can be exercised directly and so the ADK
    callback below stays a thin adapter.
    """
    if tool_name in WRITE_TOOLS and role not in WRITE_ROLES:
        return False, (
            f"role {role!r} holds no write credential; {tool_name!r} is a "
            "governed write and runs only in the writer service after a human "
            "approves a content-addressed plan"
        )
    return True, ""


def assert_allowed(tool_name: str, role: str) -> None:
    """Raising form, for call sites that are not ADK tool dispatch."""
    allowed, reason = is_allowed(tool_name, role)
    if not allowed:
        raise GuardDenial(reason)


def make_before_tool_guard(role: str):
    """Return an ADK `before_tool_callback` bound to one service role.

    ADK passes callback arguments by keyword, so the parameter names `tool`,
    `args` and `tool_context` are load-bearing and must not be renamed.

    Returns None to let the tool run, or a non-empty dict to stop it. The dict
    becomes the tool result and the tool is never invoked.
    """

    def before_tool_guard(
        *,
        tool: Any,
        args: Optional[Mapping[str, Any]] = None,
        tool_context: Any = None,
    ) -> Optional[dict[str, Any]]:
        tool_name = getattr(tool, "name", None) or str(tool)
        allowed, reason = is_allowed(tool_name, role)
        if allowed:
            return None
        return _denial(tool_name, role, reason)

    before_tool_guard.mitos_role = role  # type: ignore[attr-defined]
    return before_tool_guard
