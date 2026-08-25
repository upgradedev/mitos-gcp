// Repositories, as the thread and the configuration actually describe them.
//
// Two separate facts are joined here and neither is allowed to stand in for
// the other. GET /config lists the repositories a webhook delivery is accepted
// from, which is what "connected" means and is deployment configuration. GET
// /thread records what was actually run, and a run only names a repository
// when a delivery brought one. A repository can be on the allowlist with no
// runs, and a run can exist with no repository, so this module produces four
// buckets rather than one list, and says which is which.
//
// No React and no DOM, so the grouping stays testable and stays put if the
// render layer changes. Same rule as thread-model.ts, which this builds on
// rather than duplicating.

import type { StandardsFinding } from "../api/types";
import type { RunSummary, Tone } from "./thread-model";

// Where a run belongs.
//
//   allowlisted   a repository GET /config accepts deliveries from
//   unlisted      a repository the thread names that the allowlist does not.
//                 Configuration changes and the thread does not, so a run
//                 recorded before a repository was removed is still real
//   demo          a run whose triggers carry no repository at all. Not a
//                 missing name: it means the specialists read the built-in
//                 sample rather than a checkout
//   outside       a run with no trigger entry, so nothing started it from a
//                 repository. The parked findings and the escalations are here
export type SubjectKind = "allowlisted" | "unlisted" | "demo" | "outside";

export interface Subject {
  id: string;
  kind: SubjectKind;
  // The repository name, or null where there is not one. Null is a fact here,
  // not an absence to be filled in.
  name: string | null;
  runs: RunSummary[];
  lastAt: string | null;
  firstAt: string | null;
}

// Prefixed so a repository name can never collide with the two fixed buckets.
// The server accepts letters, digits, hyphen, underscore, dot and one slash in
// a name, so a colon cannot appear in one.
export function subjectId(kind: SubjectKind, name: string | null): string {
  return name === null ? kind : kind + ":" + name;
}

function bounds(runs: RunSummary[]): {
  lastAt: string | null;
  firstAt: string | null;
} {
  if (runs.length === 0) return { lastAt: null, firstAt: null };
  let lastAt = runs[0].lastAt;
  let firstAt = runs[0].firstAt;
  for (const run of runs) {
    if (run.lastAt.localeCompare(lastAt) > 0) lastAt = run.lastAt;
    if (run.firstAt.localeCompare(firstAt) < 0) firstAt = run.firstAt;
  }
  return { lastAt, firstAt };
}

// Runs arrive newest first from groupIntoRuns and every bucket keeps that
// order, so a history reads the way a person expects to read one.
export function subjectsOf(runs: RunSummary[], allowlist: string[]): Subject[] {
  const named = new Map<string, RunSummary[]>();
  const demo: RunSummary[] = [];
  const outside: RunSummary[] = [];

  for (const run of runs) {
    if (run.trigger === null) {
      outside.push(run);
      continue;
    }
    if (run.repository === null) {
      demo.push(run);
      continue;
    }
    const bucket = named.get(run.repository);
    if (bucket === undefined) named.set(run.repository, [run]);
    else bucket.push(run);
  }

  const subjects: Subject[] = [];

  // Allowlist order is the service's order and is kept. A repository with no
  // runs still gets a row: zero is an answer, and dropping the row would make
  // a connected repository look as though it does not exist.
  for (const name of allowlist) {
    const bucket = named.get(name) ?? [];
    named.delete(name);
    subjects.push({
      id: subjectId("allowlisted", name),
      kind: "allowlisted",
      name,
      runs: bucket,
      ...bounds(bucket),
    });
  }

  for (const name of [...named.keys()].sort((a, b) => a.localeCompare(b))) {
    const bucket = named.get(name) as RunSummary[];
    subjects.push({
      id: subjectId("unlisted", name),
      kind: "unlisted",
      name,
      runs: bucket,
      ...bounds(bucket),
    });
  }

  subjects.push({
    id: subjectId("demo", null),
    kind: "demo",
    name: null,
    runs: demo,
    ...bounds(demo),
  });

  subjects.push({
    id: subjectId("outside", null),
    kind: "outside",
    name: null,
    runs: outside,
    ...bounds(outside),
  });

  return subjects;
}

// How a run was started, said once per run. A delivery id is evidence that a
// signed webhook actually arrived and is worth showing. A run without one is
// not given an invented id.
//
// `sentence` is a whole clause rather than a noun, because the fourth case is
// an absence and "started by no trigger entry" is not a sentence anybody
// wants to read.
export interface Provenance {
  sentence: string;
  deliveryId: string | null;
}

export function provenanceOf(run: RunSummary): Provenance {
  const webhook = run.entries.find((entry) => entry.kind === "trigger.webhook");
  if (webhook !== undefined) {
    const raw = webhook.payload["delivery_id"];
    const deliveryId = typeof raw === "string" && raw.length > 0 ? raw : null;
    return { sentence: "Started by a signed webhook delivery", deliveryId };
  }
  if (run.entries.some((entry) => entry.kind === "trigger.pull_request")) {
    return {
      sentence: "Started by a pull request handed to the fleet",
      deliveryId: null,
    };
  }
  if (run.entries.some((entry) => entry.kind === "trigger.failed")) {
    return {
      sentence: "Started by a trigger that failed before the fleet ran",
      deliveryId: null,
    };
  }
  return {
    sentence: "No trigger is recorded for this run",
    deliveryId: null,
  };
}

// ---- the audit ----------------------------------------------------------
//
// The verdict vocabulary is the server's, transcribed from the docstring in
// src/mitos/standards.py rather than reworded here. Three verdicts settle a
// rule. The other four mean it could not be determined, and this page draws
// those differently from a pass, because the argument that module makes is
// that counting silence as compliance is worse than counting nothing.

export interface VerdictStyle {
  label: string;
  tone: Tone;
  // True for every verdict that leaves the rule open. Drives the dashed
  // outline, so an undecided rule never reads as settled at a glance.
  undecided: boolean;
  meaning: string;
}

const VERDICT_STYLE: Record<string, VerdictStyle> = {
  failed: {
    label: "failed",
    tone: "refusal",
    undecided: false,
    meaning: "the check ran and found something wrong",
  },
  suspected: {
    label: "suspected",
    tone: "finding",
    undecided: true,
    meaning:
      "a pattern with known false positives matched, so this is a raised hand rather than an answer",
  },
  undetermined: {
    label: "could not be determined",
    tone: "parked",
    undecided: true,
    meaning: "the check ran and could not decide",
  },
  needs_judgement: {
    label: "could not be determined",
    tone: "parked",
    undecided: true,
    meaning:
      "decidable, but not by a pattern, so it waits for a person who can read the code",
  },
  not_checkable: {
    label: "could not be determined",
    tone: "parked",
    undecided: true,
    meaning:
      "nothing inside a repository records this, so no audit of one can settle it",
  },
  not_applicable: {
    label: "not applicable",
    tone: "quiet",
    undecided: false,
    meaning: "the rule has a precondition this repository does not meet",
  },
  passed: {
    label: "passed",
    tone: "write",
    undecided: false,
    meaning: "the check ran and found nothing wrong",
  },
};

// A verdict this build has not seen is shown under its own name and treated as
// undecided, never as a pass. Same stance as vocabularyFor in thread-model.ts.
export function verdictStyleFor(verdict: string): VerdictStyle {
  return (
    VERDICT_STYLE[verdict] ?? {
      label: verdict,
      tone: "quiet",
      undecided: true,
      meaning: "a verdict this page does not recognise, shown under its own name",
    }
  );
}

// Worst first. A failure is the thing to act on, a raised hand is next, then
// everything still open, then the rules that do not apply, and a pass last
// because a pass asks nothing of the reader. Severity breaks ties inside a
// group and the rule id breaks the rest, so the order cannot move between
// renders.
const VERDICT_RANK: Record<string, number> = {
  failed: 0,
  suspected: 1,
  undetermined: 3,
  needs_judgement: 4,
  not_checkable: 5,
  not_applicable: 6,
  passed: 7,
};

const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

// An unrecognised verdict sorts just under suspected rather than at the
// bottom, so a change in the server vocabulary surfaces instead of hiding
// among the passes.
const UNKNOWN_VERDICT_RANK = 2;
const UNKNOWN_SEVERITY_RANK = 4;

export function worstFirst(findings: StandardsFinding[]): StandardsFinding[] {
  return [...findings].sort((a, b) => {
    const verdicts =
      (VERDICT_RANK[a.verdict] ?? UNKNOWN_VERDICT_RANK) -
      (VERDICT_RANK[b.verdict] ?? UNKNOWN_VERDICT_RANK);
    if (verdicts !== 0) return verdicts;
    const severities =
      (SEVERITY_RANK[a.severity] ?? UNKNOWN_SEVERITY_RANK) -
      (SEVERITY_RANK[b.severity] ?? UNKNOWN_SEVERITY_RANK);
    if (severities !== 0) return severities;
    return a.rule.localeCompare(b.rule);
  });
}
