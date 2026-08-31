// The thread, as the thing a regulator reads.
//
// The claim this page has to make good on is that an AI-authored change can be
// walked backwards: from whatever came out, through every specialist and every
// refusal, to the pull request or the parked finding that caused it. So the
// thread is drawn as the graph it is, one run at a time, and selecting an
// entry lights the path back and names each step.
//
// Nothing here is computed optimism. Where the API distinguishes zero from
// unknown, so does this page; where a walk runs out of loaded entries rather
// than reaching a beginning, it says which of the two happened.

import { useEffect, useMemo, useState } from "react";
import { load, threadSourceFor } from "../api/client";
import type { Loaded, SessionStatus, Thread, ThreadEntry } from "../api/types";
import {
  ancestryOf,
  groupIntoRuns,
  indexById,
  layoutRun,
  shortId,
  stampOf,
  timeOf,
  outcomeOf,
  vocabularyFor,
} from "./thread-model";
import type { RunSummary, WriteOutcome } from "./thread-model";
import "./thread-view.css";

// The thread is walked backwards across runs: an escalation names a deferral
// recorded weeks earlier, so a small window leaves those links dangling. This
// asks for more than the thread currently holds, and the header says plainly
// when the window turned out to be the limit.
const DEFAULT_LIMIT = 2000;

// One run in this thread holds hundreds of escalations. They are all real and
// none is hidden, but they are drawn a page at a time so the view stays usable.
const NODES_PER_PAGE = 150;

const NO_ENTRIES: ThreadEntry[] = [];

export interface ThreadViewProps {
  limit?: number;
  session?: Loaded<SessionStatus>;
}

export function ThreadView({ limit = DEFAULT_LIMIT, session }: ThreadViewProps) {
  const [thread, setThread] = useState<Loaded<Thread>>({ status: "loading" });
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [shown, setShown] = useState(NODES_PER_PAGE);

  // Which corpus this is, decided once and shown on the page.
  const source = threadSourceFor(
    session && session.status === "ok" ? session.value : null
  );

  useEffect(() => {
    // Wait for the session rather than guessing. Fetching the public corpus
    // first and swapping it later shows a signed-in user somebody else's demo
    // data for as long as the round trip takes, and there is no honest way to
    // label that half-second.
    if (session && session.status !== "ok") return;
    let live = true;
    load(() => source.fetch(limit)).then((result) => {
      if (live) setThread(result);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [limit, source.synthetic, session?.status]);

  // Held stable across renders. A fresh [] here would give every render a new
  // array identity, so grouping a thousand entries would rerun on every click.
  const entries = useMemo<ThreadEntry[]>(
    () => (thread.status === "ok" ? thread.value.entries : NO_ENTRIES),
    [thread]
  );

  const index = useMemo(() => indexById(entries), [entries]);
  const runs = useMemo(() => groupIntoRuns(entries), [entries]);

  const pullRequestRuns = runs.filter((run) => run.shape === "pull-request");
  const otherRuns = runs.filter((run) => run.shape !== "pull-request");

  // Opens on the most recent pull request run, because that is the shape of
  // the thing the product is about. Falls back to whatever is newest.
  const openRun =
    runs.find((run) => run.id === openRunId) ?? pullRequestRuns[0] ?? runs[0] ?? null;

  const laidOut = useMemo(
    () => (openRun === null ? [] : layoutRun(openRun)),
    [openRun]
  );

  // The outcome is the interesting end of a run, so the page opens there and
  // the reader walks back from it rather than forward to it.
  const defaultSelected = useMemo(() => {
    if (openRun === null) return null;
    const outcome =
      openRun.entries.find((entry) => entry.kind === "write.executed") ??
      openRun.entries.find((entry) => entry.kind === "plan.proposed") ??
      openRun.entries[openRun.entries.length - 1];
    return outcome?.entry_id ?? null;
  }, [openRun]);

  const activeId =
    selectedId !== null && index.has(selectedId) ? selectedId : defaultSelected;

  const ancestry = useMemo(
    () => (activeId === null ? null : ancestryOf(activeId, index)),
    [activeId, index]
  );

  const onPath = useMemo(
    () => new Set(ancestry === null ? [] : ancestry.path.map((e) => e.entry_id)),
    [ancestry]
  );

  const openThisRun = (runId: string) => {
    setOpenRunId(runId);
    setSelectedId(null);
    setShown(NODES_PER_PAGE);
  };

  // Walking back into another run has to take the reader with it, or the path
  // is named but not shown.
  const selectAnywhere = (entryId: string) => {
    const entry = index.get(entryId);
    if (entry === undefined) return;
    if (openRun === null || entry.run_id !== openRun.id) {
      setOpenRunId(entry.run_id);
      setShown(NODES_PER_PAGE);
    }
    setSelectedId(entryId);
  };

  if (thread.status === "loading") {
    return (
      <section className="mitos-thread">
        <Head synthetic={source.synthetic} />
        <div className="mitos-state">
          <div className="mitos-state__title">Reading the thread.</div>
          <div className="mitos-state__body">
            Asking GET /thread for the most recent {limit} entries.
          </div>
        </div>
      </section>
    );
  }

  if (thread.status !== "ok") {
    return (
      <section className="mitos-thread">
        <Head synthetic={source.synthetic} />
        <Failure
          absent={thread.status === "absent"}
          detail={thread.detail}
        />
      </section>
    );
  }

  const count = thread.value.count;
  const windowIsFull = count >= limit;
  const visible = laidOut.slice(0, shown);

  return (
    <section className="mitos-thread">
      <Head synthetic={source.synthetic} scope={thread.status === "ok" ? thread.value.scope : undefined} />

      <div className="mitos-thread__facts">
        <span>
          <b>{count}</b> entries loaded
        </span>
        <span>
          <b>{runs.length}</b> runs
        </span>
        <span>
          <b>{pullRequestRuns.length}</b> started by a pull request
        </span>
      </div>

      {windowIsFull ? (
        <p className="mitos-thread__caveat">
          These are the most recent {count} entries. GET /thread reports how
          many it returned, not how many exist, so there may be older ones this
          page has not seen.
        </p>
      ) : null}

      <div className="mitos-thread__layout">
        <nav className="mitos-thread__pane mitos-thread__pane--runs" aria-label="Runs">
          <div className="mitos-thread__pane-head">1 &middot; pick a run</div>
          <div className="mitos-runs">
            {pullRequestRuns.length > 0 ? (
              <div className="mitos-runs__group">Started by a pull request</div>
            ) : null}
            {pullRequestRuns.map((run) => (
              <RunButton
                key={run.id}
                run={run}
                open={openRun !== null && run.id === openRun.id}
                onOpen={openThisRun}
              />
            ))}
            {otherRuns.length > 0 ? (
              <div className="mitos-runs__group">
                Not a run: recorded outside a pull request
              </div>
            ) : null}
            {otherRuns.map((run) => (
              <RunButton
                key={run.id}
                run={run}
                open={openRun !== null && run.id === openRun.id}
                onOpen={openThisRun}
              />
            ))}
          </div>
        </nav>

        <div className="mitos-thread__pane mitos-thread__pane--graph">
          <div className="mitos-thread__pane-head">
            2 &middot; click an entry
          </div>
          {openRun === null ? (
            <div className="mitos-state__body mitos-graph__intro">
              The thread returned no entries.
            </div>
          ) : (
            <div className="mitos-graph">
              <p className="mitos-graph__intro">
                <RunHeadline run={openRun} /> Indentation is the parent link:
                each entry sits under the entry that caused it. Clicking one
                lights its path back and dims everything that had nothing to do
                with it.
              </p>
              {visible.map((node) => (
                <EntryNode
                  key={node.entry.entry_id}
                  entry={node.entry}
                  depth={node.depth}
                  enteredFromElsewhere={node.enteredFromElsewhere}
                  selected={node.entry.entry_id === activeId}
                  onPath={onPath.has(node.entry.entry_id)}
                  dimmed={onPath.size > 0 && !onPath.has(node.entry.entry_id)}
                  onSelect={selectAnywhere}
                />
              ))}
              {laidOut.length > visible.length ? (
                <button
                  type="button"
                  className="mitos-node depth-0"
                  onClick={() => setShown(shown + NODES_PER_PAGE)}
                >
                  <span className="mitos-node__body">
                    <span className="mitos-node__actor">
                      Showing {visible.length} of {laidOut.length} entries in
                      this run. Show more.
                    </span>
                  </span>
                </button>
              ) : null}
            </div>
          )}
        </div>

        <aside className="mitos-thread__pane" aria-label="Selected entry">
          <div className="mitos-thread__pane-head">
            3 &middot; what it holds, and what caused it
          </div>
          <div className="mitos-detail">
            {activeId === null || ancestry === null ? (
              <p className="mitos-detail__hint">Select an entry.</p>
            ) : (
              <Detail
                entry={index.get(activeId) as ThreadEntry}
                ancestry={ancestry}
                onSelect={selectAnywhere}
              />
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}

function Head({ synthetic, scope }: { synthetic: boolean; scope?: string }) {
  return (
    <header className="mitos-thread__head">
      <h1 className="mitos-thread__title">The thread</h1>
      <p className="mitos-thread__lede">
        Everything the fleet did is recorded here, and every entry names the
        entry it came from. Pick a run, then click any entry to see what it
        actually holds and to walk back to the pull request that caused it.
      </p>
      {/*
        Which corpus this is, said on the page rather than in a field nobody
        renders. The server has always answered with a `scope` describing
        itself; the interface showed neither it nor any other sign, so a
        signed-out reader had no way to tell demonstration data from a tenant's
        real runs, and a signed-in one was being shown the demonstration data.
      */}
      <p
        className={
          synthetic ? "mitos-thread__scope is-synthetic" : "mitos-thread__scope"
        }
      >
        <span className="mitos-thread__scope-badge">
          {synthetic ? "Synthetic demo data" : "Your workspace"}
        </span>
        {synthetic
          ? " Nobody's real repository is shown here. This is the built-in demo corpus, the same one the recorded demo replays. Sign in and install the GitHub App to see your own pull requests."
          : " Runs from repositories in your workspace, scoped by the server. The demo corpus is not mixed in."}
        {scope ? <span className="mitos-thread__scope-detail"> {scope}</span> : null}
      </p>
    </header>
  );
}

function Failure({ absent, detail }: { absent: boolean; detail: string }) {
  return (
    <div className="mitos-state">
      <div className="mitos-state__title">
        {absent
          ? "This build does not serve GET /thread."
          : "The thread could not be read."}
      </div>
      <div className="mitos-state__body">
        {absent ? (
          <>
            The endpoint answered 404, so there is nothing to show. That is a
            different fact from an empty thread, and this page will not show
            one as the other.
          </>
        ) : (
          <>
            The request to GET /thread did not come back. Nothing on this page
            is filled in from memory, so it stays empty. One known cause: the
            deployed content policy sets default-src to none and declares no
            connect-src, and a browser refuses that request before it is sent.
            Reload once; if it repeats, the policy or the service needs
            attention rather than the page.
          </>
        )}
      </div>
      <div className="mitos-state__detail">{detail}</div>
    </div>
  );
}

function RunHeadline({ run }: { run: RunSummary }) {
  if (run.shape === "escalations") {
    return (
      <>
        This is not a pull request run. It holds {run.entries.length}{" "}
        escalations, each one a finding that was parked and whose deferral then
        expired. Every one of them names the deferral it came from.{" "}
      </>
    );
  }
  if (run.shape === "parked") {
    return (
      <>
        This is not a pull request run. It holds {run.entries.length} findings
        that a person parked, each with the date the deferral expires.{" "}
      </>
    );
  }
  return (
    <>
      {run.pr === null ? "This run" : `Pull request ${run.pr}`} produced{" "}
      {run.entries.length} entries in this window.{" "}
    </>
  );
}

function RunButton({
  run,
  open,
  onOpen,
}: {
  run: RunSummary;
  open: boolean;
  onOpen: (id: string) => void;
}) {
  const name =
    run.shape === "escalations"
      ? "Expired deferrals, escalated"
      : run.shape === "parked"
      ? "Findings a person parked"
      : run.pr !== null
      ? `PR ${run.pr}`
      : `run ${run.id}`;

  return (
    <button
      type="button"
      className={open ? "mitos-run is-open" : "mitos-run"}
      onClick={() => onOpen(run.id)}
      aria-current={open ? "true" : undefined}
      title={run.title ?? undefined}
    >
      <span className="mitos-run__top">
        <span className="mitos-run__name">{name}</span>
        <span className="mitos-run__when">
          {run.lastAt.slice(5, 16).replace("T", " ")}
        </span>
      </span>
      {/* The demo corpus replays one pull request, so every run carries the
          same title and a list of them tells the reader nothing. What differs
          between runs is how each one ended, so that is what is shown. */}
      <span className="mitos-run__sub">{outcomeOf(run)}</span>
      <span className="mitos-run__dots">
        {run.kindCounts.map(({ kind, count }) => {
          const vocab = vocabularyFor(kind);
          return (
            <span
              key={kind}
              className={`mitos-dot tone-${vocab.tone}`}
              title={`${count} ${vocab.label}`}
            />
          );
        })}
      </span>
    </button>
  );
}

function EntryNode({
  entry,
  depth,
  enteredFromElsewhere,
  selected,
  onPath,
  dimmed,
  onSelect,
}: {
  entry: ThreadEntry;
  depth: number;
  enteredFromElsewhere: boolean;
  selected: boolean;
  onPath: boolean;
  dimmed: boolean;
  onSelect: (id: string) => void;
}) {
  const vocab = vocabularyFor(entry.kind);
  const classes = ["mitos-node", `depth-${Math.min(depth, 8)}`];
  if (selected) classes.push("is-selected");
  else if (onPath) classes.push("on-path");
  if (dimmed) classes.push("is-dimmed");

  return (
    <button
      type="button"
      className={classes.join(" ")}
      onClick={() => onSelect(entry.entry_id)}
    >
      <span className={`mitos-dot mitos-node__dot tone-${vocab.tone}`} />
      <span className="mitos-node__body">
        <span className="mitos-node__line">
          <span className={`mitos-kind tone-${vocab.tone}`}>{vocab.label}</span>
          <span className="mitos-node__actor">{entry.actor}</span>
          <span className="mitos-node__time">{timeOf(entry.recorded_at)}</span>
        </span>
        <span className="mitos-node__gloss">{glossOf(entry)}</span>
        {enteredFromElsewhere ? (
          <span className="mitos-node__from">
            caused by an entry outside this run
          </span>
        ) : null}
      </span>
    </button>
  );
}

function Detail({
  entry,
  ancestry,
  onSelect,
}: {
  entry: ThreadEntry;
  ancestry: ReturnType<typeof ancestryOf>;
  onSelect: (id: string) => void;
}) {
  const vocab = vocabularyFor(entry.kind);
  // The path is walked backwards and shown forwards, so the reader reads the
  // cause before the consequence.
  const forwards = [...ancestry.path].reverse();
  const crossed = ancestry.runsCrossed.length > 1;

  return (
    <>
      <div className={`mitos-detail__kind tone-${vocab.tone}`}>{entry.kind}</div>
      <div className="mitos-detail__meta">
        {entry.actor}
        {entry.subject ? ` on ${entry.subject}` : ""}
      </div>
      <div className="mitos-detail__meta">
        {stampOf(entry.recorded_at)} &middot; entry {shortId(entry.entry_id)}{" "}
        &middot; run {entry.run_id}
      </div>

      {entry.kind === "write.executed" ? (
        <WritePanel outcome={writeOutcomeFrom(entry)} />
      ) : null}

      <div className="mitos-detail__section">
        <div className="mitos-detail__label">
          The walk back, {forwards.length}{" "}
          {forwards.length === 1 ? "step" : "steps"}
        </div>
        <div className="mitos-retrace">
          {forwards.map((step, position) => {
            const stepVocab = vocabularyFor(step.kind);
            return (
              <div key={step.entry_id}>
                {position > 0 ? (
                  <div className="mitos-retrace__arrow">&darr;</div>
                ) : null}
                <button
                  type="button"
                  className="mitos-retrace__step"
                  onClick={() => onSelect(step.entry_id)}
                >
                  <span className={`mitos-dot tone-${stepVocab.tone}`} />
                  <span className={`mitos-kind tone-${stepVocab.tone}`}>
                    {stepVocab.label}
                  </span>
                  <span className="mitos-retrace__who">{step.actor}</span>
                </button>
              </div>
            );
          })}
        </div>

        {crossed ? (
          <p className="mitos-retrace__cross">
            This walk leaves the run it started in. It passes through{" "}
            {ancestry.runsCrossed.length} runs, which is how a finding parked
            weeks ago reaches the companion that escalated it. Clicking a step
            above opens the run it belongs to.
          </p>
        ) : null}

        {ancestry.reachedRoot ? (
          <p className="mitos-retrace__truncated">
            The walk ends at an entry with no parent, so this is the beginning
            of the thread for this outcome.
          </p>
        ) : ancestry.missingParent !== null ? (
          <p className="mitos-retrace__truncated">
            The walk stops here because the entry it names,{" "}
            {shortId(ancestry.missingParent)}, is older than the window this
            page loaded. It is not the beginning of the thread, only the
            beginning of what was fetched.
          </p>
        ) : null}
      </div>

      <div className="mitos-detail__section">
        <div className="mitos-detail__label">What this entry holds</div>
        <div className="mitos-payload">
          <PayloadNode value={entry.payload} depth={0} />
        </div>
      </div>

      <div className="mitos-detail__section">
        <div className="mitos-detail__label">Digest</div>
        <div className="mitos-payload__row">{entry.digest}</div>
      </div>
    </>
  );
}

// The one place where the difference between approved and published is the
// whole point. The thread records a write that a human approved and that put
// no bytes anywhere, and showing that as published would be the exact claim
// this project removed.
function WritePanel({ outcome }: { outcome: WriteOutcome }) {
  return (
    <div className="mitos-detail__section">
      <div className="mitos-detail__label">The governed write</div>
      <div className="mitos-write">
        <div className="mitos-write__row">
          <span className="mitos-write__key">Approved by a human</span>
          <span className="mitos-write__val">
            {outcome.approved === null ? (
              "not recorded"
            ) : outcome.approved ? (
              <span className="mitos-badge tone-write">yes</span>
            ) : (
              <span className="mitos-badge tone-refusal">no</span>
            )}
          </span>
        </div>
        <div className="mitos-write__row">
          <span className="mitos-write__key">Bytes published</span>
          <span className="mitos-write__val">
            {outcome.published === null ? (
              "not recorded"
            ) : outcome.published ? (
              <span className="mitos-badge tone-write">yes</span>
            ) : (
              <span className="mitos-badge tone-parked">no</span>
            )}
          </span>
        </div>
        <div className="mitos-write__row">
          <span className="mitos-write__key">Path</span>
          <span className="mitos-write__val">{outcome.path ?? "not recorded"}</span>
        </div>
        <div className="mitos-write__row">
          <span className="mitos-write__key">Branch</span>
          <span className="mitos-write__val">
            {outcome.branch ?? "not recorded"}
          </span>
        </div>
        <div className="mitos-write__row">
          <span className="mitos-write__key">Bytes in the plan</span>
          <span className="mitos-write__val">
            {outcome.bytes === null ? "not recorded" : outcome.bytes}
          </span>
        </div>
        <div className="mitos-write__row">
          <span className="mitos-write__key">Plan hash</span>
          <span className="mitos-write__val">
            {outcome.planHash ?? "not recorded"}
          </span>
        </div>
      </div>
      {outcome.approved === true && outcome.published === false ? (
        <p className="mitos-write__note">
          Approved and not published are two separate facts and the thread keeps
          them apart. A person approved these exact bytes and nothing was
          written.
          {outcome.reason === null
            ? ""
            : ` The recorded reason: ${outcome.reason}.`}
        </p>
      ) : outcome.reason !== null ? (
        <p className="mitos-write__note">Recorded reason: {outcome.reason}.</p>
      ) : null}
    </div>
  );
}

function writeOutcomeFrom(entry: ThreadEntry): WriteOutcome {
  const payload = entry.payload;
  const asString = (key: string) =>
    typeof payload[key] === "string" ? (payload[key] as string) : null;
  const asBool = (key: string) =>
    typeof payload[key] === "boolean" ? (payload[key] as boolean) : null;
  return {
    entryId: entry.entry_id,
    approved: asBool("approved"),
    published: asBool("published"),
    reason: asString("reason"),
    planHash: asString("plan_hash"),
    path: asString("path"),
    branch: asString("branch"),
    bytes: typeof payload.bytes === "number" ? payload.bytes : null,
  };
}

// A one-line reading of an entry, built only from fields these kinds are
// actually observed to carry. An unrecognised kind gets no gloss rather than
// a guessed one.
function glossOf(entry: ThreadEntry): string {
  const payload = entry.payload;
  const text = (key: string): string | null =>
    typeof payload[key] === "string" ? (payload[key] as string) : null;
  const list = (key: string): unknown[] | null =>
    Array.isArray(payload[key]) ? (payload[key] as unknown[]) : null;

  switch (entry.kind) {
    case "trigger.pull_request":
    case "trigger.webhook": {
      const title = text("title");
      const files = list("files");
      if (title !== null && files !== null) {
        return `${title} (${files.length} files)`;
      }
      return title ?? "a pull request arrived";
    }
    case "trigger.failed": {
      const error = text("error");
      return error === null ? "the trigger failed" : error.split("\n")[0];
    }
    case "fleet.dispatch": {
      const woken = list("woken");
      const signals = list("signals");
      const parts: string[] = [];
      if (signals !== null) parts.push(`${signals.length} signals`);
      if (woken !== null) {
        parts.push(
          woken.length === 0
            ? "woke nobody"
            : `woke ${woken.map(String).join(", ")}`
        );
      }
      return parts.join(", ");
    }
    case "specialist.response": {
      const findings = list("findings");
      const status = text("status");
      const parts: string[] = [];
      if (status !== null) parts.push(status);
      if (findings !== null) {
        parts.push(
          findings.length === 1 ? "1 finding" : `${findings.length} findings`
        );
      }
      return parts.join(", ");
    }
    case "evaluator.verdict": {
      const passed = payload.passed;
      const checks = list("checks_run");
      const injection = payload.injection_attempt;
      const parts: string[] = [];
      if (typeof passed === "boolean") {
        parts.push(passed ? "the draft passed" : "the draft was refused");
      }
      if (checks !== null) parts.push(`${checks.length} checks`);
      if (injection === true) parts.push("injection seen");
      return parts.join(", ");
    }
    case "guard.exercised": {
      if (payload.denied === true) {
        return text("detail") ?? "the write was refused";
      }
      const error = text("error");
      if (error !== null) {
        return `not exercised: ${error.split("\n")[0]}`;
      }
      return "no write was attempted";
    }
    case "injection.detected": {
      const count = payload.count;
      const note = text("note");
      const head =
        typeof count === "number"
          ? count === 1
            ? "1 injection attempt"
            : `${count} injection attempts`
          : "an injection attempt";
      return note === null ? head : `${head}, ${note}`;
    }
    case "plan.proposed": {
      const path = text("path");
      const findings = list("findings");
      const parts: string[] = [];
      if (path !== null) parts.push(path);
      if (findings !== null) parts.push(`${findings.length} findings answered`);
      return parts.join(", ");
    }
    case "write.executed": {
      const approved = payload.approved;
      const published = payload.published;
      const approvedText =
        approved === true
          ? "approved"
          : approved === false
          ? "not approved"
          : "approval not recorded";
      const publishedText =
        published === true
          ? "bytes published"
          : published === false
          ? "no bytes written"
          : "publication not recorded";
      return `${approvedText}, ${publishedText}`;
    }
    case "finding.raised":
      return text("finding") ?? "";
    case "finding.deferred": {
      const finding = text("finding");
      const expires = text("expires_on");
      if (finding === null) return "";
      return expires === null ? finding : `${finding} (expires ${expires})`;
    }
    case "finding.escalated": {
      const finding = text("finding");
      if (finding !== null) return finding;
      return text("reason") ?? "";
    }
    default:
      return "";
  }
}

// Renders a payload as itself. Every value passes through React as a text
// child, so a pull request title written by whoever opened it stays text.
function PayloadNode({ value, depth }: { value: unknown; depth: number }) {
  if (depth > 6) {
    return <span className="mitos-payload__empty">nested further</span>;
  }
  if (value === null) return <span className="mitos-payload__empty">null</span>;

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="mitos-payload__empty">empty list</span>;
    }
    return (
      <div className="mitos-payload__nest">
        {value.map((item, position) => (
          <div className="mitos-payload__row" key={position}>
            <PayloadNode value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length === 0) {
      return <span className="mitos-payload__empty">nothing recorded</span>;
    }
    return (
      <div className={depth === 0 ? undefined : "mitos-payload__nest"}>
        {keys.map((key) => (
          <div className="mitos-payload__row" key={key}>
            <span className="mitos-payload__key">{key}: </span>
            <PayloadNode value={record[key]} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  return <>{String(value)}</>;
}

export default ThreadView;
