import type { Thread, ThreadEntry } from "../api/types";

// The state of the most recent governed change, read out of the provenance
// thread. Nothing here is computed optimistically: a run is "written" only if
// a `write.executed` entry exists, and the absence of one is reported as
// nothing written rather than as still in progress.

export interface LatestRun {
  runId: string;
  pr: number | null;
  title: string | null;
  at: string;
  kinds: string[];
  planHash: string | null;
  target: string | null;
  findingCount: number | null;
  gatePassed: boolean | null;
  guardDenied: boolean | null;
  state: "written" | "card-produced" | "failed" | "incomplete";
}

// `run_id` is also used for entries that are not governed changes at all:
// "watch" for subscription wakeups and "seed" for the planted history. A run
// counts only if it began with a pull request trigger.
const REAL_RUN = "trigger.pull_request";

function str(payload: Record<string, unknown>, key: string): string | null {
  const raw = payload[key];
  return typeof raw === "string" ? raw : null;
}

export function latestRunFrom(thread: Thread): LatestRun | null {
  const byRun = new Map<string, ThreadEntry[]>();
  for (const entry of thread.entries) {
    const bucket = byRun.get(entry.run_id);
    if (bucket) bucket.push(entry);
    else byRun.set(entry.run_id, [entry]);
  }

  let best: { entries: ThreadEntry[]; at: string } | null = null;
  for (const entries of byRun.values()) {
    if (!entries.some((e) => e.kind === REAL_RUN)) continue;
    const at = entries.reduce(
      (max, e) => (e.recorded_at > max ? e.recorded_at : max),
      entries[0].recorded_at
    );
    if (!best || at > best.at) best = { entries, at };
  }
  if (!best) return null;

  const entries = best.entries;
  const kinds = entries.map((e) => e.kind);
  const trigger = entries.find((e) => e.kind === REAL_RUN);
  const plan = entries.find((e) => e.kind === "plan.proposed");
  const verdict = [...entries].reverse().find((e) => e.kind === "evaluator.verdict");
  const guard = entries.find((e) => e.kind === "guard.exercised");

  const written = kinds.includes("write.executed");
  const failed = kinds.includes("trigger.failed");

  const findings = plan?.payload.findings;

  return {
    runId: entries[0].run_id,
    pr:
      trigger && typeof trigger.payload.pr === "number"
        ? (trigger.payload.pr as number)
        : null,
    title: trigger ? str(trigger.payload, "title") : null,
    at: best.at,
    kinds,
    planHash: plan ? str(plan.payload, "plan_hash") : null,
    target: plan ? str(plan.payload, "path") : null,
    findingCount: Array.isArray(findings) ? findings.length : null,
    gatePassed:
      verdict && typeof verdict.payload.passed === "boolean"
        ? (verdict.payload.passed as boolean)
        : null,
    guardDenied:
      guard && typeof guard.payload.denied === "boolean"
        ? (guard.payload.denied as boolean)
        : null,
    state: failed
      ? "failed"
      : written
        ? "written"
        : plan
          ? "card-produced"
          : "incomplete",
  };
}

export function ago(iso: string, now = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
