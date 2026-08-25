import { Loader2, Play, Square } from "lucide-react";
import type { Config, Loaded } from "../api/types";
import Outcome from "../hero/Outcome";
import RunStream from "../hero/RunStream";
import type { RunController } from "../hero/useRun";

// The same run, with the bounds it runs under shown alongside it. The run
// controller is owned by the app, so a run started on the overview is still
// streaming when you arrive here.
export default function Run({
  run,
  config,
}: {
  run: RunController;
  config: Loaded<Config>;
}) {
  const busy = run.state === "connecting" || run.state === "running";

  return (
    <section className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
      <h1 className="text-xl font-semibold tracking-tight">Run a change</h1>
      <p className="mt-2 max-w-2xl text-sm text-ink-600 dark:text-ink-300">
        Two example pull requests are wired up. Both run the real process
        against the live service and neither can write anything.
      </p>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {!busy ? (
          <>
            <button
              type="button"
              onClick={() => run.start(4471)}
              className="inline-flex items-center gap-2 rounded-lg bg-sky-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-sky-800 dark:bg-sky-600 dark:hover:bg-sky-500"
            >
              <Play size={16} aria-hidden="true" />
              Run 4471, a new personal data field
            </button>
            <button
              type="button"
              onClick={() => run.start(4472)}
              className="inline-flex items-center gap-2 rounded-lg border border-ink-300 px-4 py-2.5 text-sm font-medium dark:border-ink-700"
            >
              Run 4472, a schema change
            </button>
          </>
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
          <span className="inline-flex items-center gap-2 text-sm" role="status">
            <Loader2 size={15} className="animate-spin" aria-hidden="true" />
            <span className="tabular-nums">{run.elapsedSeconds}s</span>
          </span>
        )}
      </div>

      <Budget config={config} />

      <div className="mt-6 space-y-4">
        <Outcome
          state={run.state}
          beats={run.beats}
          retryAfterSeconds={run.retryAfterSeconds}
          failure={run.failure}
        />
        {run.beats.length > 0 && (
          <div className="card p-4">
            <RunStream beats={run.beats} live={busy} />
          </div>
        )}
      </div>
    </section>
  );
}

// Real bounds from /config. If the call failed these say unknown, because a
// budget nobody fetched is not a budget.
function Budget({ config }: { config: Loaded<Config> }) {
  if (config.status === "loading") return null;

  if (config.status !== "ok") {
    return (
      <p className="mt-4 text-xs text-ink-500 dark:text-ink-400">
        The read budget is unknown: /config could not be read.
      </p>
    );
  }

  const { max_reads_per_run, max_bytes_per_read, read_scope } = config.value;
  return (
    <div className="card mt-5 p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-500 dark:text-ink-400">
        What a specialist may do
      </h2>
      <dl className="mt-2 grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-ink-500 dark:text-ink-400">files per run</dt>
          <dd className="font-medium tabular-nums">{max_reads_per_run}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-500 dark:text-ink-400">bytes per file</dt>
          <dd className="font-medium tabular-nums">
            {max_bytes_per_read.toLocaleString()}
          </dd>
        </div>
        <div className="sm:col-span-1">
          <dt className="text-xs text-ink-500 dark:text-ink-400">may open only</dt>
          <dd className="scroll-x font-mono text-xs">{read_scope.join(" ")}</dd>
        </div>
      </dl>
    </div>
  );
}
