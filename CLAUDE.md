# Mitos — project instructions and decision record

Required by ORG_STANDARDS §2. Every decision here is one that would surprise a
new contributor, which is the bar that section sets.

## Working on this repo

```bash
# ORG_STANDARDS §1, session rescan. Run before changing anything.
git log --oneline -10
git status
find tests -name "test_*.py" | wc -l          # 44 files, 708 passing, 2026-08-27
python scripts/generate_openapi.py --check     # the spec must match the app
```

Stop and investigate if the test count drops. The number above is a reading,
not a target: it was `baseline 15 files, 180 passing` for long enough that a
drop of two hundred would have looked like growth. Re-date it when you change it.

The offline path is standard library only. `pip install -r requirements/spike.txt`
is needed only for the ADK and Firestore suites.

## Architecture Decision Records

### ADR-001 — Firestore query subscriptions are the control plane
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** the fleet holds an open `on_snapshot` subscription to a Firestore
query and wakes when the matching set changes. There is no scheduler and no queue.
**Reason:** the entry has to be one that stops working when Google Cloud is
removed, and three services with three identities is three Lambdas with three IAM
roles. A live subscription to a *query* has no clean equivalent: a change feed is
shard-ordered, consumed server-side, and is about a table. It also makes "context
across weeks of asynchronous operations" a mechanism rather than a claim.
**Consequence:** the provenance thread is the memory, the audit trail and the
control plane at once, so a run retraces as one thread. The limit is worth
stating next to the decision: the query carries no date, so the passage of
time on its own delivers no snapshot and wakes nothing. An expired deferral
is escalated the next time the set changes for any reason. The README claimed
the calendar alone was enough; it is not, and making it so needs a durable
timer this build does not have. Cloud Run must be
deployed with `--no-cpu-throttling`, or CPU is throttled to zero between requests
and the subscription is suspended with no error. Portability of this component is
deliberately abandoned.

### ADR-002 — The model can only tighten
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** everywhere a model touches a decision, its output is unioned and
never subtracted. The evaluator's critic may add findings and never remove one.
The router's classifier may add signals and never remove one, and cannot clear a
deterministic refusal.
**Reason:** the model reviewing a draft is the same class of thing that wrote it,
and the fixture diff contains an instruction telling the reviewer to approve
itself. A gate a model can argue its way out of is not a gate.
**Consequence:** a wrong or compromised model can make the fleet do more work or
be more cautious, never less. Degradation is safe by construction: an unreachable
classifier contributes nothing. `evaluator._with_critic` and
`fleet.route_with_model` are both union-only by construction, but only the
former carries a structural test asserting that no subtracting branch exists.
The router's guarantee rests on the code and on behavioural tests over the
backlog, which is weaker, and this paragraph claimed otherwise for weeks.

### ADR-003 — The gate is deterministic; the model is a second opinion
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** secret detection, injection detection, guardrail-bypass detection
and path-hallucination checks are regular expressions, not model judgements.
**Reason:** the demo must reject on every take. A rejection that depends on the
model misbehaving is a demo that works until the one recording you keep. The
poison is planted in the fixture input instead, so a faithful generator carries
it forward and the deterministic gate catches it every time.
**Consequence:** the offline path needs no credential and is what CI and the
recorded video run. It also means the interesting refusals are rules, which is a
fair criticism, and ADR-002 is the answer to it.

### ADR-004 — Two backends behind one interface, everywhere
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** `Ledger` is a protocol with `InMemoryLedger` and `FirestoreLedger`.
The same pattern is used for the analyst, the critic, the spec repository and the
watcher.
**Reason:** the whole chore has to be runnable by a stranger with no cloud
account, and the deployment should be a swap rather than a rewrite.
**Consequence:** coverage that skips the Firestore adapter would be a fiction, so
the adapter is exercised against the emulator in CI and a separate step fails the
build if that suite ever skips.

### ADR-005 — The write credential is a repository-scoped deploy key
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** the writer publishes over SSH with a deploy key scoped to
`upgradedev/mitos-spec`, held in Secret Manager, readable by one service account.
**Reason:** a personal access token carries the whole account, and the entry's
argument is least privilege. A placeholder secret would have made the boundary a
demonstration rather than an architecture.
**Consequence:** the reader orchestrates the chore and must ask the writer over
an authenticated call, because IAM will not give it the key. The image needs
`git` and `ssh`, which is why deployment is an explicit Dockerfile rather than
buildpacks.

### ADR-006 — A specialist may refuse
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** specialists return a typed envelope with
`ok | needs_changes | blocked | error`, and a refusal without a reason raises at
construction.
**Reason:** a fleet whose agents can only succeed produces an answer for every
item, including the ones where the honest answer is that a human has to look.
Carried across from ADR-015 in earlier platform design work, named in the README.
**Consequence:** the backlog produces a real count including parked items, which
is what the autonomy criterion actually asks for. Irreversible migrations and
GDPR Article 9 data are refused rather than assessed.

### ADR-007 — An unattended wake cannot reach the write credential
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** the action taken when the subscription fires is limited to
appending an escalation to the thread.
**Reason:** waking is cheap and nobody is watching.
**Consequence:** an expired deferral escalates but never publishes. Tested by
asserting no `write.executed` or `plan.proposed` entry can result from a wake.

### ADR-008 — Deployment is an explicit Dockerfile, not buildpacks
**Date:** 2026-08-19 | **Status:** Implemented
**Decision:** one image, three deployments, differing only by service account and
`MITOS_ROLE`.
**Reason:** the writer needs `git` and `ssh`, and relying on whatever a buildpack
includes is how a deployment breaks quietly later. Buildpacks also stopped
resolving Python 3.12 mid-build, which is the same class of surprise.
**Consequence:** the image runs as a non-root user, which matters most on the one
service holding a credential that can change something. Python is pinned to 3.13
in `.python-version` and in CI together, so they cannot drift.

### ADR-009 — The recorded video runs deterministically; the model is proven in CI
**Date:** 2026-08-24 | **Status:** Implemented
**Decision:** `video.yml` records the demo with `MITOS_MODEL=stub`. The
submission video contains no live model call. Gemini 3.7 is exercised by
`tests/integration/test_gemini_live.py` on every pull request instead, and a
separate CI step fails the build if that suite skips.
**Reason:** the recording used to end on a live Gemini call, and three builds at
the same pace produced runs of 156s, 209s and 645s, the longest printing eight
times as many lines as the shortest. The competition caps the video at four
minutes. A recording whose length varies by a factor of four cannot reliably
produce an artifact under that cap, and a submission video that fails to build
on the day is precisely how two previous entries were lost.

The requirement is "Gemini 3.5 or newer accessed through Gemini API or Vertex
AI". A video does not satisfy it in any checkable way: a viewer cannot tell a
real response from a recorded one, so the video was never the evidence. The test
is. It calls the model, asserts on what came back, and goes red when the model
is unreachable.
**Consequence:** the demo in the video shows the deterministic path, which is
the path ADR-003 already says CI and the recording use. The one thing the video
loses is the closing comparison where the model widens the router on `vuln_code`;
that comparison still runs in CI and is described in the README with the test
that proves it. `video.yml` also holds no Google credential now, because it
needed one only for that call.
**Rejected:** a timeout around the model call. It bounds the failure without
removing it, so the build still fails on a slow day, which is the day you are
most likely to be rebuilding.

### ADR-010 — The deployed system is tested as a pyramid, not smoke-tested
**Date:** 2026-08-24 | **Status:** Implemented
**Decision:** `deployed.yml` runs five layers against the live URLs: the
contract each service publishes about itself, the security boundary, the
journeys a judge follows, and the full `judge_uat` suite. It runs on a schedule,
on demand, and after every apply.
**Reason:** everything green in CI proves the code that was pushed. It does not
prove the thing that is running. The gap between them has already produced two
incidents here: `MITOS_WRITER_URL` missing from Terraform, caught by luck before
a rebuild, and `allUsers` on the writer, which no test looked for because every
test read the identity endpoints anonymously and expected them to answer.
**Consequence:** a deployment that drifts is caught by a schedule rather than by
somebody opening the page. The suite runs with no credential by default, so it
is the same thing a judge can run, and the checks that need one announce
themselves as not checked rather than passing quietly.

## Standards compliance

| ORG_STANDARDS | State |
|---|---|
| §1 session rescan | commands above |
| §2 ADRs | this section |
| §3 secret scan first stage | gitleaks v8.18.4 pinned, **no ignore file** |
| §6 OpenAPI at repo root | `openapi.yaml`, generated, drift-checked in CI |
| §7 request lifecycle middleware | `service/main.py`, structured JSON, once not per handler |
| §8 connection reuse | **partly**. One client per process for the ledger, memoised in `ledger()`. The session and workspace handlers still build one per request, 16 sites in `service/main.py`. Stated rather than claimed, per the Never section below |
| §9 hierarchical secret naming | **partly**. The two `settings-*` secrets follow `/Mitos/Prod/settings/{Service}/{Key}`; the three App registration credentials are created under a flat `mitos-{stage}-github-app-*` prefix built at runtime, which drops both segments |

## Never

- Widen a gate to make it pass. Fix reality or state the limitation.
- Claim portability anywhere a judge reads. Internally there are adapters;
  publicly this is the Google Cloud build.
- Write a regex through a shell heredoc. A trailing word-boundary escape once
  landed in the source as a literal backspace byte, so the destructive-migration
  rule could never match, and every test still passed because no test fed it a
  destructive migration. Use an editor, or build the escape explicitly.
