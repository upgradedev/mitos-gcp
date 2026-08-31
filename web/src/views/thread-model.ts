// The thread is a graph, not a list. Every entry names the entry it came from,
// so any outcome walks back to the pull request or the parked finding that
// caused it. This module holds that walk and nothing else: no React, no DOM,
// so the hard part stays testable and stays put if the render layer changes.

import type { ThreadEntry } from "../api/types";

// Ported from service/thread_view.py KIND_STYLE, kind for kind. The server is
// the authority on this vocabulary and a second palette invented here would
// drift from it. Each tone below is the hex that page already uses:
//
//   trigger  #5fd7d7    route   #7aa2f7    quiet   #8a8790
//   refusal  #ff6b6b    parked  #ffd75f    plan    #ffffff
//   write    #5fd75f    finding #ff9e64
//
// Red is a refusal or a finding, green is the one governed write, yellow is
// parked or deferred. The colours live in thread-view.css as classes, because
// the content policy refuses a style attribute.
export type Tone =
  | "trigger"
  | "route"
  | "quiet"
  | "refusal"
  | "parked"
  | "plan"
  | "write"
  | "finding";

export interface Vocab {
  label: string;
  tone: Tone;
}

export const KIND_VOCABULARY: Record<string, Vocab> = {
  "trigger.pull_request": { label: "trigger", tone: "trigger" },
  "trigger.webhook": { label: "trigger", tone: "trigger" },
  "trigger.ignored": { label: "ignored", tone: "quiet" },
  "trigger.failed": { label: "failed", tone: "refusal" },
  "fleet.dispatch": { label: "dispatch", tone: "route" },
  "specialist.response": { label: "specialist", tone: "quiet" },
  "evaluator.verdict": { label: "gate", tone: "route" },
  "guard.exercised": { label: "guard", tone: "refusal" },
  "injection.detected": { label: "injection", tone: "refusal" },
  "item.parked": { label: "parked", tone: "parked" },
  "finding.deferred": { label: "deferred", tone: "parked" },
  "finding.escalated": { label: "escalated", tone: "parked" },
  "finding.raised": { label: "finding", tone: "finding" },
  "plan.proposed": { label: "plan", tone: "plan" },
  // A run that found things worth saying and no document it could
  // honestly propose editing. Toned as a finding rather than a plan,
  // because nothing is being offered for approval.
  "plan.review_only": { label: "review", tone: "finding" },
  "run.nothing_to_govern": { label: "nothing to govern", tone: "quiet" },
  "gate.delegated": { label: "gate delegated", tone: "route" },
  "critic.independent_review": { label: "independent review", tone: "finding" },
  "write.executed": { label: "write", tone: "write" },
};

// A kind this build has not seen is shown under its own name in the neutral
// tone, which is what the server-rendered page does. Silently dropping it
// would hide a step the fleet actually recorded.
export function vocabularyFor(kind: string): Vocab {
  return KIND_VOCABULARY[kind] ?? { label: kind, tone: "quiet" };
}

// Payload readers that refuse to guess. A field that is absent, null, or of
// the wrong type comes back as null and is rendered as unknown, never as a
// zero or a false that nobody measured.
function readString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" ? value : null;
}

function readNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readBoolean(payload: Record<string, unknown>, key: string): boolean | null {
  const value = payload[key];
  return typeof value === "boolean" ? value : null;
}

function readArrayLength(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return Array.isArray(value) ? value.length : null;
}

export function indexById(entries: ThreadEntry[]): Map<string, ThreadEntry> {
  return new Map(entries.map((entry) => [entry.entry_id, entry]));
}

// The result of walking parent_id backwards.
//
// reachedRoot and missingParent are kept apart on purpose. An entry whose
// parent_id is null genuinely starts the thread. An entry whose parent is not
// in the loaded window is a different fact: the walk ran out of page, not out
// of graph, and calling that the beginning would be a claim the data does not
// support.
export interface Ancestry {
  path: ThreadEntry[];
  reachedRoot: boolean;
  missingParent: string | null;
  runsCrossed: string[];
}

export function ancestryOf(
  entryId: string,
  index: Map<string, ThreadEntry>
): Ancestry {
  const path: ThreadEntry[] = [];
  const seen = new Set<string>();
  let cursor: string | null = entryId;
  let reachedRoot = false;
  let missingParent: string | null = null;

  while (cursor !== null && !seen.has(cursor)) {
    const entry: ThreadEntry | undefined = index.get(cursor);
    if (entry === undefined) {
      missingParent = cursor;
      break;
    }
    seen.add(cursor);
    path.push(entry);
    if (entry.parent_id === null) {
      reachedRoot = true;
      break;
    }
    cursor = entry.parent_id;
  }

  const runsCrossed: string[] = [];
  for (const entry of path) {
    if (!runsCrossed.includes(entry.run_id)) runsCrossed.push(entry.run_id);
  }

  return { path, reachedRoot, missingParent, runsCrossed };
}

export type RunShape = "pull-request" | "escalations" | "parked" | "other";

export interface WriteOutcome {
  entryId: string;
  // approved and published are two separate facts and the API keeps them
  // apart. A write can be approved by a human and still put no bytes anywhere,
  // which is exactly what the one write in this thread records.
  approved: boolean | null;
  published: boolean | null;
  reason: string | null;
  planHash: string | null;
  path: string | null;
  branch: string | null;
  bytes: number | null;
}

export interface RunSummary {
  id: string;
  shape: RunShape;
  entries: ThreadEntry[];
  kindCounts: { kind: string; count: number }[];
  firstAt: string;
  lastAt: string;
  trigger: ThreadEntry | null;
  title: string | null;
  pr: number | null;
  repository: string | null;
  filesChanged: number | null;
  subject: string | null;
  injections: number;
  guardRefusals: number;
  findings: number;
  plans: number;
  write: WriteOutcome | null;
  gatePassed: boolean | null;
}

function compareByTime(a: ThreadEntry, b: ThreadEntry): number {
  return (
    a.recorded_at.localeCompare(b.recorded_at) ||
    a.entry_id.localeCompare(b.entry_id)
  );
}

// Classified by what the entries are, not by matching the run id against a
// known name. The subscription bucket and the seeded deferrals are recognised
// because of the kinds they hold, so a renamed run keeps being described
// correctly.
function shapeOf(entries: ThreadEntry[]): RunShape {
  const kinds = new Set(entries.map((entry) => entry.kind));
  if (kinds.size === 1 && kinds.has("finding.escalated")) return "escalations";
  if (kinds.size === 1 && kinds.has("finding.deferred")) return "parked";
  for (const kind of kinds) {
    if (kind.startsWith("trigger.")) return "pull-request";
  }
  return "other";
}

function writeOutcomeOf(entry: ThreadEntry): WriteOutcome {
  return {
    entryId: entry.entry_id,
    approved: readBoolean(entry.payload, "approved"),
    published: readBoolean(entry.payload, "published"),
    reason: readString(entry.payload, "reason"),
    planHash: readString(entry.payload, "plan_hash"),
    path: readString(entry.payload, "path"),
    branch: readString(entry.payload, "branch"),
    bytes: readNumber(entry.payload, "bytes"),
  };
}

export function groupIntoRuns(entries: ThreadEntry[]): RunSummary[] {
  const buckets = new Map<string, ThreadEntry[]>();
  for (const entry of entries) {
    const bucket = buckets.get(entry.run_id);
    if (bucket === undefined) buckets.set(entry.run_id, [entry]);
    else bucket.push(entry);
  }

  const runs: RunSummary[] = [];
  for (const [id, bucket] of buckets) {
    const ordered = [...bucket].sort(compareByTime);
    const counts = new Map<string, number>();
    for (const entry of ordered) {
      counts.set(entry.kind, (counts.get(entry.kind) ?? 0) + 1);
    }

    // A run can hold more than one trigger entry, and the useful field is not
    // always on the first of them. A delivered webhook is recorded as
    // `trigger.webhook` carrying the repository, and the `trigger.pull_request`
    // that follows it does not repeat that name; the two are sometimes stamped
    // with the same second, so "first by time" is decided by an id comparison.
    // Reading one entry therefore loses the repository at random. These read
    // every trigger in the run and take the first value any of them holds.
    const triggers = ordered.filter((entry) => entry.kind.startsWith("trigger."));
    const trigger = triggers[0] ?? null;

    const firstString = (key: string): string | null => {
      for (const entry of triggers) {
        const value = readString(entry.payload, key);
        if (value !== null) return value;
      }
      return null;
    };
    const firstNumber = (key: string): number | null => {
      for (const entry of triggers) {
        const value = readNumber(entry.payload, key);
        if (value !== null) return value;
      }
      return null;
    };
    const firstArrayLength = (key: string): number | null => {
      for (const entry of triggers) {
        const value = readArrayLength(entry.payload, key);
        if (value !== null) return value;
      }
      return null;
    };

    const writeEntry =
      ordered.find((entry) => entry.kind === "write.executed") ?? null;
    const gate = ordered.find((entry) => entry.kind === "evaluator.verdict");
    const subjectHolder = ordered.find((entry) => entry.subject.length > 0);

    runs.push({
      id,
      shape: shapeOf(ordered),
      entries: ordered,
      kindCounts: [...counts]
        .map(([kind, count]) => ({ kind, count }))
        .sort((a, b) => b.count - a.count || a.kind.localeCompare(b.kind)),
      firstAt: ordered[0].recorded_at,
      lastAt: ordered[ordered.length - 1].recorded_at,
      trigger,
      title: firstString("title"),
      pr: firstNumber("pr"),
      repository: firstString("repository"),
      filesChanged: firstArrayLength("files"),
      subject: subjectHolder === undefined ? null : subjectHolder.subject,
      injections: counts.get("injection.detected") ?? 0,
      guardRefusals: ordered.filter(
        (entry) =>
          entry.kind === "guard.exercised" &&
          readBoolean(entry.payload, "denied") === true
      ).length,
      findings:
        (counts.get("finding.raised") ?? 0) +
        (counts.get("finding.escalated") ?? 0),
      plans: counts.get("plan.proposed") ?? 0,
      write: writeEntry === null ? null : writeOutcomeOf(writeEntry),
      gatePassed:
        gate === undefined ? null : readBoolean(gate.payload, "passed"),
    });
  }

  return runs.sort((a, b) => b.lastAt.localeCompare(a.lastAt));
}

export interface LaidOut {
  entry: ThreadEntry;
  depth: number;
  // True when this entry names a cause that is not in this run, which is how
  // an expired deferral reaches the companion that escalates it.
  enteredFromElsewhere: boolean;
}

// Lays a run out as the tree its parent links describe, rather than as the
// flat list that makes the reader do the walking in their head.
export function layoutRun(run: RunSummary): LaidOut[] {
  const inRun = new Set(run.entries.map((entry) => entry.entry_id));
  const children = new Map<string, ThreadEntry[]>();
  const roots: ThreadEntry[] = [];

  for (const entry of run.entries) {
    const parent = entry.parent_id;
    if (parent !== null && inRun.has(parent)) {
      const siblings = children.get(parent);
      if (siblings === undefined) children.set(parent, [entry]);
      else siblings.push(entry);
    } else {
      roots.push(entry);
    }
  }

  for (const siblings of children.values()) siblings.sort(compareByTime);
  roots.sort(compareByTime);

  const out: LaidOut[] = [];
  const visited = new Set<string>();

  const walk = (entry: ThreadEntry, depth: number): void => {
    if (visited.has(entry.entry_id)) return;
    visited.add(entry.entry_id);
    out.push({
      entry,
      depth,
      enteredFromElsewhere:
        depth === 0 && entry.parent_id !== null && !inRun.has(entry.parent_id),
    });
    for (const kid of children.get(entry.entry_id) ?? []) walk(kid, depth + 1);
  };

  for (const root of roots) walk(root, 0);

  // A cycle would leave entries unvisited. They are appended rather than lost,
  // because an entry the fleet recorded has to appear somewhere on this page.
  for (const entry of run.entries) {
    if (!visited.has(entry.entry_id)) {
      out.push({ entry, depth: 0, enteredFromElsewhere: false });
    }
  }

  return out;
}

// How a run ended, in the fewest words that stay true. Every clause is a
// counted field, and a run whose outcome none of these describe says how many
// entries it holds rather than inventing a conclusion for it.
//
// It lives in this module rather than in the thread view because the
// repositories view answers the same question about the same runs, and two
// answers to one question drift apart.
export function outcomeOf(run: RunSummary): string {
  if (run.shape === "escalations" || run.shape === "parked") {
    return `${run.entries.length} entries`;
  }

  const parts: string[] = [];

  if (run.injections > 0) {
    parts.push(
      run.injections === 1
        ? "1 injection detected"
        : `${run.injections} injections detected`
    );
  }

  if (run.entries.some((entry) => entry.kind === "trigger.failed")) {
    parts.push("the trigger failed");
  } else if (run.write !== null) {
    parts.push(
      run.write.published === true
        ? "bytes published"
        : run.write.approved === true
        ? "approved, nothing written"
        : "a write was recorded"
    );
  } else if (run.gatePassed === false) {
    parts.push("the gate refused the draft");
  } else if (run.plans > 0) {
    parts.push("plan proposed");
  } else if (run.guardRefusals > 0) {
    parts.push("the write was refused");
  }

  if (parts.length === 0) parts.push(`${run.entries.length} entries`);
  return parts.join(", ");
}

// Formatting. Every timestamp seen so far carries +00:00, but the UTC label is
// only added when the string itself says so.
export function timeOf(recordedAt: string): string {
  return recordedAt.slice(11, 19);
}

export function dateOf(recordedAt: string): string {
  return recordedAt.slice(0, 10);
}

export function stampOf(recordedAt: string): string {
  const utc = recordedAt.endsWith("+00:00") || recordedAt.endsWith("Z");
  return dateOf(recordedAt) + " " + timeOf(recordedAt) + (utc ? " UTC" : "");
}

export function shortId(entryId: string): string {
  return entryId.slice(0, 8);
}
