"""Proof that the Mitos gate is a control and not a request.

This is the first code in the repository on purpose. The entry's whole claim is
that a compromised or merely over-eager agent cannot reach a write tool, because
the refusal happens in ADK's dispatcher rather than in a system prompt. If that
is false, the architecture changes, and it is much cheaper to learn on day two
than on day nine.

The model here is a stub that ALWAYS asks for the write tool, on every turn. It
is the worst case: an agent fully committed to writing. Nothing in the prompt
discourages it. The only thing standing in the way is the guard.

Three tests, and the middle one is the one that makes the first believable:

1. reader role  -> the tool never executes
2. writer role  -> the same stub, the same request, and the tool DOES execute
3. a characterization test pinning ADK's falsy-return behaviour

Without (2), a green (1) would be indistinguishable from a harness that never
dispatched a tool at all. That is the failure mode the workspace rules call a
test that cannot fail.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from google.adk.agents import LlmAgent  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from mitos.guard import (  # noqa: E402
    ROLE_READER,
    ROLE_WRITER,
    make_before_tool_guard,
)

APP = "mitos-spike"
USER = "judge"
TOOL_NAME = "write_spec_repo"

# Every real invocation of the tool appends here. The assertion that matters in
# this file is that this list is empty.
EXECUTIONS: list[dict[str, Any]] = []


def write_spec_repo(path: str, body: str) -> dict:
    """Write a spec file into the spec repository. A governed write.

    Args:
        path: repository-relative path of the spec to write.
        body: full new contents of the spec.
    """
    EXECUTIONS.append({"path": path, "body": body})
    return {"status": "written", "path": path}


class AlwaysWritesLlm(BaseLlm):
    """A stub model that asks for the write tool and never gives up.

    Stateless by construction: it decides what to emit from whether the request
    already carries a function response, so there is no counter to get out of
    step with a retry.
    """

    model: str = "stub/always-writes"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        already_ran = any(
            part.function_response is not None
            for content in (llm_request.contents or [])
            for part in (content.parts or [])
        )
        if already_ran:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="turn complete")],
                )
            )
            return
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name=TOOL_NAME,
                            args={"path": "docs/customer.md", "body": "rewritten"},
                        )
                    )
                ],
            )
        )


def _agent(callback) -> LlmAgent:
    return LlmAgent(
        name="spike_agent",
        model=AlwaysWritesLlm(),
        instruction="Write the spec.",
        tools=[write_spec_repo],
        before_tool_callback=callback,
    )


async def _run(agent: LlmAgent) -> list:
    runner = InMemoryRunner(agent=agent, app_name=APP)
    try:
        session = await runner.session_service.create_session(
            app_name=APP, user_id=USER
        )
        events = []
        async for event in runner.run_async(
            user_id=USER,
            session_id=session.id,
            new_message=types.Content(
                role="user", parts=[types.Part(text="Update the spec.")]
            ),
        ):
            events.append(event)
        return events
    finally:
        close = getattr(runner, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result


def _tool_results(events) -> list[dict]:
    out = []
    for event in events:
        for part in (event.content.parts if event.content else []) or []:
            if part.function_response is not None:
                out.append(dict(part.function_response.response or {}))
    return out


@pytest.fixture(autouse=True)
def _clear_executions():
    EXECUTIONS.clear()
    yield
    EXECUTIONS.clear()


def test_reader_role_cannot_reach_the_write_tool():
    """The claim. A model demanding the write gets refused by the dispatcher."""
    events = asyncio.run(_run(_agent(make_before_tool_guard(ROLE_READER))))

    assert EXECUTIONS == [], (
        "the write tool executed under the reader role; the gate is a prompt, "
        "not a control, and the architecture claim is false"
    )

    results = _tool_results(events)
    assert results, "no tool result came back at all; the harness did not dispatch"
    assert any(r.get("denied_by") == "mitos-guard" for r in results), (
        f"the tool was skipped but not by our guard: {results}"
    )


def test_writer_role_does_reach_the_write_tool():
    """The control. Same stub, same demand, gate open, and the tool runs.

    This is the deliberate proof that the previous test can fail. A gate nobody
    has watched permit is a gate nobody should believe blocks.
    """
    asyncio.run(_run(_agent(make_before_tool_guard(ROLE_WRITER))))

    assert len(EXECUTIONS) == 1, (
        "the writer role did not execute the tool, so the harness never "
        f"dispatched and the reader test proves nothing: {EXECUTIONS}"
    )
    assert EXECUTIONS[0]["path"] == "docs/customer.md"


def test_empty_dict_return_also_suppresses_the_tool():
    """Characterization test, pinning behaviour the docs describe loosely.

    The docs say a falsy return "lets the next one run". That is true of the
    callback chain, but the dispatcher then tests `function_response is None`
    before calling the tool, so an empty dict from the last callback suppresses
    the tool anyway while producing an empty result.

    We do not rely on this: `guard.py` returns a non-empty dict precisely so the
    behaviour is unambiguous. The test exists so that an ADK upgrade which
    changes it turns CI red where we can see it, instead of silently altering
    what a falsy guard means.
    """

    def falsy_guard(*, tool, args=None, tool_context=None):
        return {}

    asyncio.run(_run(_agent(falsy_guard)))

    assert EXECUTIONS == [], (
        "ADK now executes the tool when a callback returns an empty dict. The "
        "guard contract has changed; re-read google/adk/flows/llm_flows/functions.py."
    )
