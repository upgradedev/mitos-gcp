# Mitos

A fleet of institutional agents with bounded reads, an evaluator gate, one human-approved governed
write, and a thread of provenance you can follow back.

Named for Ariadne's thread. The promise is that you can always retrace your way out.

Built for **All Things Agentic (Google)**, track **The Fortified Enterprise Fleet**.

## Why ADK is load-bearing

Mitos enforces every gate decision inside ADK's tool-call interceptor rather than in a prompt. A
system-prompt instruction saying "do not call write tools" is a request. `before_tool_callback` is a
control: ADK evaluates it before it dispatches the tool, and the model has no way to reach the
decision.

The mechanism, read from the ADK source rather than inferred. In
`google/adk/flows/llm_flows/functions.py`, the dispatcher runs every registered callback first and
invokes the tool only when the collected response `is None`:

```python
for before_callback in agent.canonical_before_tool_callbacks:
    ...
    function_response = callback_result
    if function_response:
        break

if function_response is None:
    function_response = await __call_tool_async(tool, args=..., tool_context=...)
```

So a non-empty dict from [`src/mitos/guard.py`](src/mitos/guard.py) means the tool is never invoked,
and the dict becomes what the model sees as the tool's result.

One detail the ADK documentation gets loosely: it says a falsy return "lets the next one run". That
is true of the callback chain, but the dispatcher then tests `is None` rather than truthiness, so an
empty dict from the last callback in the chain suppresses the tool anyway. The guard returns a
**non-empty** dict so the contract is unambiguous under any number of callbacks, and
`test_empty_dict_return_also_suppresses_the_tool` pins the behaviour so an ADK upgrade that changes
it turns CI red.

### The proof, and the proof that it can fail

The test model is a stub that demands the write tool on **every** turn. Nothing in the prompt
discourages it. It is the worst case: an agent fully committed to writing.

| Run | Result | What it establishes |
|---|---|---|
| [32234664424](https://github.com/upgradedev/mitos-gcp/actions/runs/32234664424) | 3 passed, google-adk 2.7.1 | under the reader role the tool never executes. Under the writer role the **identical** request does execute, so the harness genuinely dispatches |
| [32234805908](https://github.com/upgradedev/mitos-gcp/actions/runs/32234805908) | 1 failed, 2 passed | **proof the gate has teeth.** The guard was disabled by one deliberate edit; the same stub then wrote `docs/customer.md` and the reader test failed with *"the gate is a prompt, not a control"* |
| [32234931833](https://github.com/upgradedev/mitos-gcp/actions/runs/32234931833) | success | guard restored |

The middle row is the one that matters. A gate nobody has watched go red is a gate nobody should
believe. The second row is why the first is not merely a harness that never dispatched a tool at all.

Both are reproducible: `python -m pytest tests/spike -q`.

## Spin-up

No API key, no cloud credential and no paid call are needed to reproduce the gate proof. The model is
a stub, so the suite is offline by construction.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements/spike.txt
python -m pytest tests/spike -q
```

Requires Python 3.10 or newer; CI runs 3.12.

## Status, stated honestly

| Claim | State |
|---|---|
| the gate is a control and not a prompt | **proven**, in CI, both directions, links above |
| the guard's policy is deterministic and model-independent | **in the repo**, `src/mitos/guard.py` |
| three Cloud Run services under three separate service accounts | **not built yet.** An architectural intention, not a shipped fact |
| Firestore as the provenance ledger | **not built yet** |
| the fleet, the evaluator gate, the governed write | **not built yet** |

Anything marked not built is not claimed anywhere else in this repository either. When it ships, this
table changes with it.

## Pre-existing components

Nothing in this repository predates the submission period. The fleet's *shape* is derived from ARKON,
a set of 58 companion definitions used as configuration for the authors' own workflow; those
definitions are not part of this repository and none of their content is reused verbatim.

## Licence

MIT. See [`LICENSE`](LICENSE).
