import { CircleDashed, FileText, ShieldAlert } from "lucide-react";
import type { Loaded, Thread } from "../api/types";
import { ago, latestRunFrom } from "./latestRun";

// Four words for four states, chosen so that none of them can be mistaken for
// "published". The service keeps proposed, written and published apart, and so
// does this.
const STATE_COPY: Record<string, { label: string; detail: string }> = {
  "card-produced": {
    label: "Waiting on a person",
    detail: "A card was produced naming exact bytes. Nothing was written.",
  },
  written: {
    label: "Approved and written",
    detail: "A person approved the bytes and the writer service wrote them.",
  },
  failed: {
    label: "Did not finish",
    detail: "The run stopped before proposing anything. Nothing was written.",
  },
  incomplete: {
    label: "No card produced",
    detail: "The run ended without proposing a change. Nothing was written.",
  },
};

export default function LatestChange({
  thread,
  running,
}: {
  thread: Loaded<Thread>;
  running: boolean;
}) {
  if (thread.status === "loading") {
    return (
      <Shell title="Most recent change">
        <p className="text-sm text-ink-500 dark:text-ink-400">Reading the thread.</p>
      </Shell>
    );
  }

  if (thread.status !== "ok") {
    return (
      <Shell title="Most recent change">
        <p className="text-sm text-ink-600 dark:text-ink-300">
          The thread could not be read, so the most recent change is unknown.
        </p>
        <p className="mt-1 font-mono text-xs text-ink-500 dark:text-ink-400">
          {thread.detail}
        </p>
      </Shell>
    );
  }

  const latest = latestRunFrom(thread.value);

  // The invitation. Not a zero, not an empty chart: nothing has run yet, so the
  // only useful thing to say is what to do about it.
  if (!latest) {
    return (
      <Shell title="Most recent change">
        <p className="flex items-start gap-2 text-sm text-ink-700 dark:text-ink-300">
          <CircleDashed size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            Nothing has been through the process yet. Press{" "}
            <strong>Run a governed change</strong> and this will fill in.
          </span>
        </p>
      </Shell>
    );
  }

  const copy = STATE_COPY[latest.state];

  return (
    <Shell title="Most recent change">
      <p className="text-xs text-ink-500 dark:text-ink-400">
        {running ? "before this run" : ago(latest.at)}
        {latest.pr !== null ? ` · pull request ${latest.pr}` : ""}
      </p>

      {latest.title && (
        <p className="mt-1 text-sm font-medium leading-snug">{latest.title}</p>
      )}

      <p className="mt-3 flex items-center gap-2 text-sm font-semibold">
        <ShieldAlert size={16} aria-hidden="true" />
        {copy.label}
      </p>
      <p className="mt-1 text-xs text-ink-600 dark:text-ink-300">{copy.detail}</p>

      {latest.target && (
        <p className="mt-3 flex items-start gap-2 text-xs">
          <FileText size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span className="scroll-x font-mono">{latest.target}</span>
        </p>
      )}

      {latest.planHash && (
        <p className="mt-1 break-all font-mono text-[11px] text-ink-500 dark:text-ink-400">
          {latest.planHash.slice(0, 32)}...
        </p>
      )}

      <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <Pair
          term="gate"
          value={
            latest.gatePassed === null
              ? "unknown"
              : latest.gatePassed
                ? "passed"
                : "refused"
          }
        />
        <Pair
          term="write tool"
          value={
            latest.guardDenied === null
              ? "unknown"
              : latest.guardDenied
                ? "refused"
                : "reachable"
          }
        />
        <Pair
          term="findings"
          value={
            latest.findingCount === null ? "unknown" : String(latest.findingCount)
          }
        />
      </dl>
    </Shell>
  );
}

function Pair({ term, value }: { term: string; value: string }) {
  return (
    <div>
      <dt className="inline text-ink-500 dark:text-ink-400">{term} </dt>
      <dd
        className={`inline font-medium ${
          value === "unknown" ? "italic text-ink-400 dark:text-ink-500" : ""
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

function Shell({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="card p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-500 dark:text-ink-400">
        {title}
      </h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}
