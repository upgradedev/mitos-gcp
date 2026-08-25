import { Loader2, Play, Square } from "lucide-react";
import type { Identity, Loaded, Thread } from "../api/types";
import Outcome from "./Outcome";
import RunStream from "./RunStream";
import LatestChange from "./LatestChange";
import type { RunController } from "./useRun";

interface Props {
  run: RunController;
  thread: Loaded<Thread>;
  identity: Loaded<Identity>;
}

// Said before the button is pressed, not discovered afterwards. Each line is
// something the run demonstrably does; none of it is a promise this build
// cannot keep. In particular there is no claim that the gate will reject,
// because on this pull request it frequently passes on the first draft.
const WHAT_HAPPENS = [
  "Only the specialists the change concerns are woken. The rest stay asleep.",
  "Each one reads the repository under a budget and reports. It may refuse.",
  "A deterministic gate checks the draft before a human ever sees it.",
  "You get one approval card naming the exact bytes, and nothing is written.",
];

export default function Hero({ run, thread, identity }: Props) {
  const busy = run.state === "connecting" || run.state === "running";
  const finished =
    run.state === "card-produced" ||
    run.state === "written" ||
    run.state === "halted" ||
    run.state === "failed" ||
    run.state === "rate-limited";

  return (
    <section className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <p className="text-xs font-medium uppercase tracking-widest text-sky-700 dark:text-sky-400">
        Governed change for AI-authored code
      </p>

      <h1 className="mt-2 text-balance text-2xl font-semibold leading-snug tracking-tight sm:text-3xl">
        Mitos stops an AI-written change from becoming permanent until the right
        specialists have reviewed it and one person has approved the exact bytes.
      </h1>

      <p className="mt-3 max-w-2xl text-sm text-ink-600 dark:text-ink-300">
        Press the button. It runs a real change through the whole process on the
        live service and shows you every step as it happens. It takes about a
        minute.
      </p>

      <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {/* DOM order is the mobile order on purpose: the action and the four
            lines saying what it will do must both sit above the fold at 375,
            and the most recent change follows them. On a wide screen the grid
            puts them side by side instead. */}
        <div>
          <div className="flex flex-wrap items-center gap-3">
            {!busy ? (
              <button
                type="button"
                onClick={() => run.start(4471)}
                className="inline-flex items-center gap-2 rounded-lg bg-sky-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-sky-800 dark:bg-sky-600 dark:hover:bg-sky-500"
              >
                <Play size={16} aria-hidden="true" />
                {finished ? "Run it again" : "Run a governed change"}
              </button>
            ) : (
              <button
                type="button"
                onClick={run.cancel}
                className="inline-flex items-center gap-2 rounded-lg border border-ink-300 px-4 py-2.5 text-sm font-semibold dark:border-ink-700"
              >
                <Square size={16} aria-hidden="true" />
                Stop watching
              </button>
            )}

            {busy && (
              <span
                className="inline-flex items-center gap-2 text-sm text-ink-600 dark:text-ink-300"
                role="status"
              >
                <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                {run.beats.length === 0
                  ? "asking the service to start"
                  : `working, ${run.beats.length} steps so far`}
                <span className="tabular-nums">{run.elapsedSeconds}s</span>
              </span>
            )}
          </div>

          <p className="mt-2 text-xs text-ink-500 dark:text-ink-400">
            {busy
              ? "The long pause is the specialists reading files. That is the real work."
              : "Nothing you do on this page can write anything."}{" "}
            {!busy && (
              <button
                type="button"
                onClick={() => run.start(4472)}
                className="rounded underline underline-offset-2 hover:text-ink-800 dark:hover:text-ink-200"
              >
                Or run the other example instead
              </button>
            )}
          </p>

          <ol className="mt-4 space-y-1.5">
            {WHAT_HAPPENS.map((line, i) => (
              <li key={line} className="flex gap-2.5 text-sm">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ink-200 text-[11px] font-semibold tabular-nums dark:bg-ink-800">
                  {i + 1}
                </span>
                <span className="text-ink-700 dark:text-ink-300">{line}</span>
              </li>
            ))}
          </ol>
        </div>

        <div>
          <LatestChange thread={thread} running={busy} />
        </div>
      </div>

      {(busy || run.beats.length > 0 || finished) && (
        <div className="mt-6 space-y-4">
          <Outcome
            state={run.state}
            beats={run.beats}
            retryAfterSeconds={run.retryAfterSeconds}
            failure={run.failure}
          />
          {run.beats.length > 0 && (
            <div className="card p-4">
              <div className="mb-3 flex items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold">
                  {busy ? "Happening now" : "What happened"}
                </h2>
                <span className="text-xs text-ink-500 dark:text-ink-400">
                  {run.pr ? `pull request ${run.pr}` : ""}
                </span>
              </div>
              <RunStream beats={run.beats} live={busy} />
            </div>
          )}
        </div>
      )}

      <IdentityFootnote identity={identity} />
    </section>
  );
}

// The claim in the headline is that the page cannot write. This is the evidence
// for it, taken from /identity rather than asserted.
function IdentityFootnote({ identity }: { identity: Loaded<Identity> }) {
  if (identity.status !== "ok") return null;
  const { role, spec_repo_write_credential: cred } = identity.value;
  return (
    <p className="mt-6 border-t border-ink-200 pt-4 text-xs text-ink-500 dark:border-ink-800 dark:text-ink-400">
      This service runs as <span className="font-mono">{role}</span>. Its access
      to the write credential is{" "}
      <strong>{cred.reachable ? "reachable" : "refused"}</strong>
      {cred.detail ? ` (${cred.detail})` : ""}. That refusal is enforced by
      Google IAM, outside this process.
    </p>
  );
}
