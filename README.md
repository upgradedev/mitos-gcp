# Mitos

[![CI](https://github.com/upgradedev/mitos-gcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/upgradedev/mitos-gcp/actions/workflows/ci.yml)
[![Submission video](https://github.com/upgradedev/mitos-gcp/actions/workflows/video.yml/badge.svg?branch=main)](https://github.com/upgradedev/mitos-gcp/actions/workflows/video.yml)
[![coverage 87%](https://img.shields.io/badge/coverage-87%25-green.svg)](https://github.com/upgradedev/mitos-gcp/actions/runs/32577912740)
[![Gemini 3.7 Flash](https://img.shields.io/badge/Gemini-3.7%20Flash-4285F4.svg)](https://cloud.google.com/vertex-ai)
[![Cloud Run, 3 identities](https://img.shields.io/badge/Cloud%20Run-3%20identities-4285F4.svg)](https://console.cloud.google.com/run?project=upgradegr-mitos)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A schema change ships on Tuesday. In March, a regulator asks who approved the mobile-number
column and why. Mitos is the fleet that answers that, and the thread you follow back.**

**▶ Demo video:** `PENDING_YOUTUBE_UPLOAD`
*(the 3:49 master is built and verified in CI. This line is replaced with the public URL at upload,
and every CI run annotates a warning while the placeholder is still here so it cannot be quietly
forgotten.)*

Named for Ariadne's thread. You can always retrace your way out.

Built for **All Things Agentic (Google)**, track **The Fortified Enterprise Fleet**.

## Who this is for

The engineering lead at a **regulated European electricity distribution operator**. Every schema
change that touches a customer field drags four things behind it: a specification that silently goes
stale, a record of processing under GDPR Article 30, a retention entry, and an owner who has to be
told. Today one person chases all four by hand after the fact, and finds out they missed one when
someone external asks.

Mitos does that chase unattended and returns **one diff to approve**.

## The one thing it does

A pull request lands carrying a schema change on a field that holds personal data. Nobody opens
Mitos; a webhook starts it. The fleet works out which specialists the change concerns, remembers what
it already decided about that service weeks ago, has its first draft **rejected by its own gate**,
repairs it, and comes back with a single content-addressed diff for a human.

Everything before the approval is autonomous. The approval is the last step, not a stall in the
middle.

```mermaid
flowchart TB
    PR["Pull request<br/>schema change, 4 files, 3 languages"] -->|webhook| R
    L -.->|"query subscription<br/><b>the trigger</b>"| R

    subgraph READER["mitos-reader &nbsp;·&nbsp; Cloud Run"]
        direction TB
        R["architect-leader<br/><i>router: decides who wakes</i>"]
        R --> S1["db-architect-leader"]
        R --> S2["documentation-companion"]
        R --> S3["compliance-companion<br/><i>skipped when no personal data</i>"]
    end

    S1 & S2 & S3 --> D["draft"]

    subgraph EVAL["mitos-evaluator &nbsp;·&nbsp; Cloud Run"]
        G["deterministic gate<br/>secrets · injection · bypass · hallucinated paths"]
        C["Gemini critic<br/><i>may only ADD findings</i>"]
        G --> C
    end

    D --> G
    C -->|FAIL| RE["repair"] --> G
    C -->|PASS| CARD["approval card<br/>sha256 of the exact plan"]

    CARD --> H(["human approves"])

    subgraph WRITER["mitos-writer &nbsp;·&nbsp; Cloud Run"]
        W["governed write<br/><i>refuses any other hash</i>"]
    end

    H --> W --> OUT["spec repo PR<br/>+ red status check on the code PR"]

    READER -.->|append only| L[("Firestore<br/>provenance thread")]
    EVAL -.->|append only| L
    WRITER -.->|append only| L

    SEC[["Secret Manager<br/>spec-repo write token"]]
    WRITER ==>|"granted"| SEC
    READER -.->|"PermissionDenied"| SEC
    EVAL -.->|"PermissionDenied"| SEC
```

The two dotted red-herring arrows into Secret Manager are the point of the whole design. The reader
and the evaluator **ask** for the write credential and Google IAM refuses them. Nothing in our code
decides that.

## Why Google Cloud is load-bearing

> **Mitos has no scheduler and no queue. Its agents hold open Firestore query subscriptions, so a
> compliance finding deferred until 12 August wakes the fleet on 12 August because the query itself
> is the subscription. Take Firestore away and the thread you follow back stops being the control
> plane and becomes a log, and you need a queue, a poller and a separate state store to get the same
> behaviour, none of which can be retraced as one thread.**

A change feed is not the same thing. DynamoDB Streams is shard-ordered, consumed server-side, and
delivers events about a *table*. `on_snapshot` subscribes to a **query**: *every finding whose
deferral is still open*. The process holding it is handed the current result set and then every
change to it, so "wake me when this set changes" needs no poller, no queue, and no second store to
remember what was already seen.

The consequence is architectural rather than cosmetic. **The provenance thread stops being a log and
becomes the thing that dispatches work.** One store is the memory, the audit trail and the control
plane at once, which is why a run can be retraced as a single thread instead of reconciled across
three systems. It is also what makes the track's hardest phrase mechanical: a deferral until
12 August is a standing query, and the fleet wakes when the world moves past that date with nobody
scheduling anything.

Watch it, with no account. `wakeups` only increments when Firestore delivered a snapshot in which a
deferral had expired:

```bash
curl -s https://mitos-reader-437828525303.europe-west1.run.app/watch
```

Cloud Run throttles CPU to zero between requests by default, which would suspend a subscription
silently. The reader is deployed with `--no-cpu-throttling` for exactly that reason. A listener that
only runs while someone happens to be calling you is a poller with extra steps.

### And the privilege boundary underneath it

> Mitos runs its reader, its evaluator and its writer as three Cloud Run services under three
> separate service accounts, and enforces every gate decision inside ADK's tool-call interceptor
> rather than in a prompt, so the identity that reads production data holds no credential that can
> write it and no agent in the fleet can talk its way past the gate.

**Open the thread.** The product is named for a thread you can follow back, so it
is drawn rather than listed. Click any outcome and the whole path to the pull
request that caused it lights up:

**https://mitos-reader-437828525303.europe-west1.run.app/thread/view**

Check the rest yourself, with no account:

```bash
curl -s https://mitos-reader-437828525303.europe-west1.run.app/identity
curl -s https://mitos-writer-437828525303.europe-west1.run.app/identity
```

| Identity | may call `write_spec_repo` | reaches the write credential |
|---|---|---|
| `mitos-reader` | `false` | **PermissionDenied**, from IAM |
| `mitos-evaluator` | `false` | **PermissionDenied**, from IAM |
| `mitos-writer` | `true` | yes |

The credential is an **SSH deploy key scoped to a single repository**, not a personal access token.
A token would carry the whole account. This carries write access to `mitos-spec` and nothing else,
which is the difference between saying least privilege and doing it.

So the boundary has a consequence rather than being a demonstration. Ask the reader to write and it
refuses, because it has nothing to write with:

```bash
curl -s -X POST https://mitos-reader-437828525303.europe-west1.run.app/execute \
  -H 'content-type: application/json' \
  -d '{"path":"docs/x.md","body":"x","message":"m","branch":"b"}'
# {"detail":"the reader service holds no credential that can write"}
```

The reader orchestrates the whole chore and then has to **ask** the writer service, over an
authenticated call, and the writer re-checks the plan hash itself rather than trusting the caller.

`/identity` does not read a config flag. It **attempts** the access and reports what came back.

### The interceptor, and the proof it can fail

The refusal happens in ADK's dispatcher, not in a system prompt. In
`google/adk/flows/llm_flows/functions.py` the dispatcher runs every registered callback and invokes
the tool only when the collected response `is None`, so a non-empty dict from
[`src/mitos/guard.py`](src/mitos/guard.py) means the tool is never called.

| Run | Result | What it establishes |
|---|---|---|
| [32234664424](https://github.com/upgradedev/mitos-gcp/actions/runs/32234664424) | 3 passed | a stub model demanding the write never reaches the tool as reader, and **does** reach it as writer, so the harness genuinely dispatches |
| [32234805908](https://github.com/upgradedev/mitos-gcp/actions/runs/32234805908) | 1 failed | **proof the gate has teeth.** Guard disabled by one edit; the same model then wrote `docs/customer.md` |
| local, 2026-08-19 | 6 passed | the same block against **live Gemini 3.7**, not a stub. `tests/integration/test_gemini_live.py` |

A gate nobody has watched go red is a gate nobody should believe.

### Is this actually agentic, or a rules engine with a model attached

A fair question, and for most of this project's life the honest answer was the
second one. Every outcome was decided by a regular expression; the model wrote
prose. Deleting it would have changed nothing.

**A specialist now gets a repository and a question instead of an answer, and
decides what to open.** The choice is real and it differs per item:

```
PR 4473  list_paths(*)  search(supply)  read_file(registers/retention.md)
         read_file(docs/specs/customer-record.md)  ...          -> ok
PR 4477  list_paths(*)  search(vulnerability)  read_file(registers/retention.md)
         read_file(docs/specs/customer-record.md)               -> blocked
```

That sequence is recorded in the provenance thread, so the agency is inspectable
rather than claimed. A fixed pipeline produces the same log on every item.

**The case that settles it.** `PR 4483` adds a column called `vuln_code`. No
pattern matches it, and only a comment says it holds medical dependency data
from a questionnaire.

| | compliance woken | outcome |
|---|---|---|
| deterministic rules alone | **no** | **completed**, and health data ships |
| with the model reading the repository | yes | **blocked**, citing the register it opened |

Both halves are pinned by tests. [`test_rules_alone_are_not_enough.py`](tests/unit/test_rules_alone_are_not_enough.py)
asserts the rules miss it and needs no credential;
[`test_gemini_live.py`](tests/integration/test_gemini_live.py) asserts the model
catches it. If either stops being true, the build says so.

### Bounded reads, which is why the guard is not decoration

An agent that genuinely decides where to look can decide to look somewhere it
should not. So the reads are bounded in [`src/mitos/tools.py`](src/mitos/tools.py)
and enforced in the interceptor, not requested in a prompt:

| Bound | What it refuses |
|---|---|
| scope | anything outside `docs/`, `services/`, `registers/` |
| traversal | `..` and absolute paths |
| size | a single read is capped |
| budget | a finite number of successful reads per run |

A refused read does not consume the budget, so an agent that guesses a path
badly is not locked out of files it is entitled to open. That distinction was a
real bug: counting refusals inflated the number past the cap, so the limit never
stopped anything.

### Why a model is allowed near the gate

The evaluator is deterministic. A Gemini critic sits behind it and **can only add findings**.
`_with_critic` in [`src/mitos/evaluator.py`](src/mitos/evaluator.py) computes
`passed = deterministic_passed AND the critic found nothing`; there is no branch that removes a
finding or flips a verdict. [`tests/unit/test_critic_invariant.py`](tests/unit/test_critic_invariant.py)
feeds it a critic that insists everything is approved and asserts the deterministic findings survive.

A gate a model can argue its way out of is not a gate, and the model reviewing the draft is the same
class of thing that wrote it.

## What the track asked for

| Track wording | Where it is |
|---|---|
| "cataloged for cross-department use" | the catalogue is a queried structure, not a table in a document. The router reads it to decide who wakes, so adding a companion changes behaviour. `GET /catalog` |
| "safely maintain context across weeks of asynchronous operations" | **the query subscription is the mechanism, not the storage.** A deferral until 12 August is a standing query; the fleet wakes when the world moves past that date with nobody scheduling anything. `GET /watch` counts how many times it has. The demo also runs the chore twice and the second run recalls what the first wrote, live |
| "without violating enterprise compliance, data sovereignty, or security policies" | three identities, one write credential, an append-only ledger with no mutation method, and a write addressed by sha256 |

## Run it

**Nothing to install.** It is deployed, and these are live right now:

| | |
|---|---|
| **Watch the thread** | https://mitos-reader-437828525303.europe-west1.run.app/thread/view |
| Who each service is, and what it cannot reach | [`/identity`](https://mitos-reader-437828525303.europe-west1.run.app/identity) |
| The subscription, and how many times it woke | [`/watch`](https://mitos-reader-437828525303.europe-west1.run.app/watch) |
| The catalogue the router queries | [`/catalog`](https://mitos-reader-437828525303.europe-west1.run.app/catalog) |
| The API | [`/openapi.json`](https://mitos-reader-437828525303.europe-west1.run.app/openapi.json) |

**Watch the chore happen.** It streams, so the first beat arrives in under a
second even though the whole thing takes a minute or two with a model reading the
repository:

```bash
curl -N -X POST https://mitos-reader-437828525303.europe-west1.run.app/run/stream   -H 'content-type: application/json' -d '{"pr":4471,"seed":true}'
```

**Ask the reader to write, and watch it refuse:**

```bash
curl -s -X POST https://mitos-reader-437828525303.europe-west1.run.app/execute   -H 'content-type: application/json'   -d '{"path":"docs/x.md","body":"x","message":"m","branch":"b"}'
# {"detail":"the reader service holds no credential that can write"}
```

### From source, against the same stack

```bash
pip install -r requirements/spike.txt
export GOOGLE_CLOUD_PROJECT=upgradegr-mitos MITOS_MODEL=gemini-3.7-flash
PYTHONPATH=src python -m mitos.demo
```

The default is Firestore and Gemini, deliberately. If it cannot reach them it
prints **THIS IS NOT THE REAL SYSTEM** in red and points back at the deployed
URL, because a demo that quietly falls back to an in-memory ledger shows a stub
and nobody watching can tell.

`--ledger memory` exists for CI, where a test suite must not need a cloud
account. It announces itself on screen.

Python 3.10+; CI runs 3.13.

**Gemini 3.x is served on Vertex's `global` endpoint, not the regional ones.**
Every regional endpoint returns 404 for a 3.x model id while serving 2.5 happily.
That cost an hour to find and `test_gemini_live.py` pins it.

## Tests

| Layer | What it covers |
|---|---|
| `tests/unit` | the policy, the ledger contract, every detector, the critic invariant |
| `tests/integration` | the chore end to end, the ADK dispatcher, Firestore against the emulator, live Gemini |
| `tests/e2e` | the journey a judge watches, driven the way this README says to run it |

**86% coverage against an 85% floor**, measured on `main` by
[CI run 32577912740](https://github.com/upgradedev/mitos-gcp/actions/runs/32577912740):
85.98%, 1369 statements, 192 missed. The command that produced it, and the whole
of what it covers:

```bash
python -m pytest tests/unit tests/e2e tests/integration/test_chore.py \
  --cov=mitos --cov-report=term-missing --cov-fail-under=85
```

The unit suite, the end-to-end journey and the chore integration test. The
Firestore adapter suite and the live Gemini suite run as separate CI jobs and sit
outside that number, so read it as coverage of the offline path rather than of
everything that runs.

**The margin is 0.98 points, and that is the honest number rather than a
comfortable one.** Coverage has sat near 86% on `main` since 2026-08-20
([run 32400213647](https://github.com/upgradedev/mitos-gcp/actions/runs/32400213647),
86.06%) while the model, webhook and corpus layers landed. The floor stays at 85
and the number comes up to meet it. Widening the floor to make the margin look
wider would delete the only thing that notices.

The Firestore adapter is exercised against the emulator rather than trusted because
the in-memory one passes, and a separate CI step fails the build if that suite ever
skips.

gitleaks runs over history with **no ignore file**. Every credential-shaped test value is assembled
at runtime in [`tests/synthetic_secrets.py`](tests/synthetic_secrets.py), because allowlisting a
pattern to make a secret scan pass is how secret scanners stop working.

## The video

Built by [`.github/workflows/video.yml`](.github/workflows/video.yml), never on a developer machine.
`video/record.py` runs the demo as a subprocess and records every byte it printed with the second it
printed it; `video/build.py` replays exactly that, at exactly that speed. **Nothing is cut and no
beat is sped up.** If the chore fails, the recording fails and no video is produced.

## Status, stated honestly

| Claim | State |
|---|---|
| the gate is a control, not a prompt | **proven in CI, both directions**, and against live Gemini 3.7 |
| three Cloud Run services, three service accounts | **deployed**, verifiable with the two `curl`s above |
| Firestore provenance thread | **deployed**, append-only |
| Gemini 3.7 reads the diffs and reviews the drafts | **live**, `MITOS_MODEL=gemini-3.7-flash` |
| the spec-repo write | **real.** The writer service pushes a branch to [upgradedev/mitos-spec](https://github.com/upgradedev/mitos-spec) over SSH, using a deploy key scoped to that one repository. Commits are authored by `mitos-writer@upgradegr-mitos.iam.gserviceaccount.com` |
| the webhook | **real.** A GitHub webhook on [upgradedev/mitos-spec](https://github.com/upgradedev/mitos-spec) posts to `/webhook/github`. Signature verified with HMAC-SHA256 over the raw body, repository allowlisted, and the fleet wakes with nobody calling anything |

Nothing in this product is simulated any more. The trigger was the last one, and
it closed on 2026-08-21: a real pull request on the specification repository woke
the fleet, which read the diff from the public GitHub API, dispatched, exercised
the interceptor and produced a plan. GitHub's own delivery log shows `202 OK`.

A webhook never approves a write. It produces a plan and stops at the approval,
because the one thing a human is there for is the thing an automatic trigger must
not do on their behalf.

## Pre-existing components

Every line of code in this repository was written during the submission period. Two bodies of
earlier **design** work by the same author informed it, and both are named here because both
rulebooks require it.

| Pre-existing work | What it is | What was carried across |
|---|---|---|
| **ARKON companion definitions** | 58 agent definitions used as configuration for the author's own development workflow | the fleet's *shape*: leaders, specialists, and a mandatory evaluator between a generator and any real system. Five are productised here. None of their text is reused verbatim and the definitions are not part of this repository |
| **ARKON platform design notes (ADR-015, "Typed Agent Cards")** | an internal architecture decision record on typed agent contracts and capability-based routing | the idea that an agent publishes a typed input/output contract, that a coordinator routes by declared capability rather than hardcoded dispatch, and that a handoff carries a status a callee can push back with. The document is not in this repository and no code was copied from it |

Neither body of work is or contains a deployable product, and neither is submitted. What is
submitted is this repository.

## Licence

MIT. See [`LICENSE`](LICENSE).
