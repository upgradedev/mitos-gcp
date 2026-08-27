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

### ADR-005 — The spec repository write credential is a repository-scoped deploy key
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
**Amended 2026-08-27:** this said "THE write credential", singular, and there are
two. The second is a GitHub App installation token, minted per request in the
reader, which is what creates check runs and opens suggested pull requests. The
two have different blast radii and different homes, and conflating them made the
boundary sound simpler than it is. ADR-013 covers the second.

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

### ADR-011 — Sign-in is GitHub OAuth, and no session ever holds a GitHub token
**Date:** 2026-08-27 | **Status:** Implemented
**Decision:** a person signs in through the GitHub App's OAuth flow. The user
access token is used once, inside the callback, to read the profile and the
installation list, and is then discarded. What persists is a random session id
in an `HttpOnly` cookie and a Firestore `sessions` document; the browser never
receives a GitHub token and neither does any later request.
**Reason:** the product's own standard requires the ADR set to decide
"authentication and token storage", and `standards.py` scores that rule `high`.
Running Mitos against Mitos, the absence of this decision is a finding. More
importantly a stored user token is a credential with the user's whole account
behind it, sitting in a datastore this fleet reads on every request, and the
entry's argument is that the reading identity holds nothing that can change
anything.
**Consequence:** an expired or unreachable session store means signed out rather
than an error, which is fail-closed and is what `_session_user` does. There is
no refresh: a session outlives the token it was created from, and any GitHub
call made later is made with an **installation** token minted fresh, never with
the person's. The cost is that Mitos cannot act as the signed-in user, which is
deliberate.
**Documented late, which is the point of writing it down.** This shipped in #68
and the ADR set did not mention it for nine days.

### ADR-012 — A workspace is a GitHub account, and the first member owns it
**Date:** 2026-08-27 | **Status:** Implemented
**Decision:** tenancy is derived, never chosen. `workspace_id` is
`github-{account_id}` taken from the App installation, and membership is written
at sign-in from the installations GitHub reports for that user. The first member
of a workspace becomes `owner` and everyone after is `reviewer`; an existing
membership keeps whatever role it already had.
**Reason:** any workspace a user could name is a workspace a user could claim.
Deriving it from the installation means the boundary is GitHub's answer to "what
may this person see", not ours, and it cannot be forged by a request body.
**Consequence:** `_require_role` is the single gate, and approving a write needs
`owner`. A person who signs in with no installation gets no workspace and sees
nothing, which reads as an empty product and is correct. Two people installing
on the same account race for `owner`, and the loser is a `reviewer` until
somebody changes it by hand; there is no invitation flow and that is a gap
rather than a decision.

### ADR-013 — Writing back to GitHub uses a per-request installation token
**Date:** 2026-08-27 | **Status:** Implemented
**Decision:** check runs, and the branch-file-pull-request sequence behind an
approval, are made with a GitHub App installation token minted per request from
the App private key. Nothing is stored: the token is created, used and dropped
inside the handler.
**Reason:** ADR-005 covers a deploy key scoped to one repository we own. This is
the opposite direction, into a repository somebody else owns, and a deploy key
cannot express it. An installation token can, is scoped to the repositories that
person chose when installing, expires in an hour, and is revoked by uninstalling
the App, which is an action the owner can take without us.
**Consequence:** there are now two write credentials with different blast radii,
which ADR-005 hid by saying "the". The private key that mints these tokens lives
in Secret Manager and the reader holds `secretVersionAdder` and `secretAccessor`
on it, so the reader is not credential-free in the absolute sense the README
once claimed; it is unable to reach the **specification repository** credential,
which is what `/identity` proves live. A failure to mint a token is a failure to
report, never a failure to analyse: `_safe_github_check` swallows it and returns
the existing check run id, because a GitHub outage must not stop a run.

### ADR-014 — The standards auditor reports what it could not decide
**Date:** 2026-08-27 | **Status:** Implemented
**Decision:** `check_repository` returns a verdict per rule across five states,
and the two that matter are `needs_judgement`, for a rule no pattern can settle,
and `could_not_be_determined`, for a rule whose evidence was not readable. A
rule is never counted as passed because nothing was found.
**Reason:** the failure mode of every compliance tool is silence read as
compliance. A rule that could not be evaluated and a rule that passed look
identical in a count, and the count is what anybody actually reads.
**Consequence:** the summary always carries a non-zero
`could_not_be_determined` on a real repository, and `deployed.yml` asserts that
rather than a pass rate. It also means the auditor cannot be scored as a
percentage without lying, which is a feature and reads as a weakness.

The reading is bounded by the same rate limit as everything else here: one audit
costs about 31 requests to the public GitHub API, unauthenticated callers get 60
an hour per address, and Cloud Run egresses from a shared one. Results are held
for ten minutes so that looking twice costs once. That is mitigation, not a fix;
the fix is reading under the installation token from ADR-013, which is not
built.


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
- Write a credential shape as a literal, including inside a comment explaining
  the shape. `tests/synthetic_secrets.py` assembles every one at import time,
  and the reason is that gitleaks is the first stage of CI with no ignore file.
  This was learned twice in one day, in a fixture and then in the comment
  written to explain widening the detector, which is the easiest place to
  forget it. Repairing the working tree does not repair the history: CI checks
  out with `fetch-depth: 0` and scans commits, so a later fix leaves the scan
  red and the branch has to be rebuilt. Run it before pushing:

  ```bash
  gitleaks detect --source . --log-opts="origin/main..HEAD"
  ```
