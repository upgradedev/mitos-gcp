import type { Beat, RunState } from "../api/types";

// A phase exists in the interface only once a beat belonging to it has
// arrived. There is deliberately no fixed list of steps drawn ahead of time.
//
// The reason is specific rather than stylistic. A run on the demo pull request
// can pass the gate on the first draft, in which case no repair happens. A
// timeline that draws "refused" and "repaired" slots in advance would be
// showing two states that did not occur, which is the failure this project
// exists to avoid. So the phases below are buckets, and a bucket is rendered
// only when something lands in it.

export type PhaseId =
  | "trigger"
  | "untrusted"
  | "signals"
  | "dispatch"
  | "recall"
  | "specialists"
  | "gate"
  | "guard"
  | "approval"
  | "outcome"
  | "other";

export interface Phase {
  id: PhaseId;
  title: string;
  // What this phase means, in plain words, for somebody who has never seen it.
  blurb: string;
}

export const PHASES: Record<PhaseId, Phase> = {
  trigger: {
    id: "trigger",
    title: "The change arrives",
    blurb: "A pull request is picked up and recorded in the thread.",
  },
  untrusted: {
    id: "untrusted",
    title: "The diff is read as untrusted input",
    blurb:
      "Instructions planted inside the change are recorded and carried to the approval. They are never acted on.",
  },
  signals: {
    id: "signals",
    title: "What the change touches",
    blurb:
      "The diff is scanned for the things that decide who needs to look at it.",
  },
  dispatch: {
    id: "dispatch",
    title: "Specialists woken",
    blurb:
      "Only the specialists the change concerns are woken. A model may widen this list and can never narrow it.",
  },
  recall: {
    id: "recall",
    title: "What we already knew",
    blurb:
      "The thread is searched for this subject, so a finding raised before is not raised twice.",
  },
  specialists: {
    id: "specialists",
    title: "Specialists read and report",
    blurb:
      "Each one opens files under a bounded budget and returns a report it is allowed to refuse.",
  },
  gate: {
    id: "gate",
    title: "The gate",
    blurb:
      "A deterministic check on the draft. If it refuses, the draft is stripped and re-submitted.",
  },
  guard: {
    id: "guard",
    title: "The write guard",
    blurb:
      "The write tool is offered to a specialist on purpose, to show it is refused.",
  },
  approval: {
    id: "approval",
    title: "The approval card",
    blurb: "One write proposed, named by the exact bytes it would produce.",
  },
  outcome: {
    id: "outcome",
    title: "Outcome",
    blurb: "What was and was not written.",
  },
  other: {
    id: "other",
    title: "Other events",
    blurb: "Beats this interface does not have a place for yet.",
  },
};

const PHASE_OF: Record<string, PhaseId> = {
  trigger: "trigger",
  signal: "signals",
  divergence: "signals",
  dispatch: "dispatch",
  recall: "recall",
  escalate: "recall",
  engine: "specialists",
  reads: "specialists",
  specialist: "specialists",
  evaluate: "gate",
  finding: "gate",
  repair: "gate",
  guard: "guard",
  approval: "approval",
  identity: "approval",
  halt: "outcome",
  done: "outcome",
  error: "outcome",
};

// `guard` is emitted for two different things, and calling both of them "the
// write guard" would mislabel the first. One reports instructions planted in
// the diff, which happens near the start; the other reports the write tool
// being offered to a specialist and refused, which happens near the end.
export function phaseOf(kind: string, text = ""): PhaseId {
  if (kind === "guard") {
    return /planted in the diff/.test(text) ? "untrusted" : "guard";
  }
  return PHASE_OF[kind] ?? "other";
}

export interface GroupedBeat {
  beat: Beat;
  // Consecutive identical beats are collapsed. A live run emits around fifty
  // near-identical `recall` lines out of seventy-five beats, and left as they
  // are they bury the guard, the approval card and the halt, which are the
  // whole point of watching.
  repeats: number;
  index: number;
}

export interface PhaseGroup {
  phase: Phase;
  beats: GroupedBeat[];
}

export function groupBeats(beats: Beat[]): PhaseGroup[] {
  const order: PhaseId[] = [];
  const byPhase = new Map<PhaseId, GroupedBeat[]>();

  beats.forEach((beat, index) => {
    const id = phaseOf(beat.kind, beat.text);
    if (!byPhase.has(id)) {
      byPhase.set(id, []);
      order.push(id);
    }
    const bucket = byPhase.get(id)!;
    const last = bucket[bucket.length - 1];
    if (last && last.beat.kind === beat.kind && last.beat.text === beat.text) {
      last.repeats += 1;
      return;
    }
    bucket.push({ beat, repeats: 1, index });
  });

  return order.map((id) => ({ phase: PHASES[id], beats: byPhase.get(id)! }));
}

// The state is read from the beats that arrived, never assumed. `done` carries
// the only authoritative answer about whether anything was written.
export function stateFromBeats(beats: Beat[], streaming: boolean): RunState {
  const done = beats.find((b) => b.kind === "done");
  if (beats.some((b) => b.kind === "error")) return "failed";
  if (done) {
    if (done.written === true) return "written";
    if (beats.some((b) => b.kind === "approval")) return "card-produced";
    return "halted";
  }
  if (streaming) return beats.length === 0 ? "connecting" : "running";
  return "idle";
}

export interface RunSummary {
  planHash: string | null;
  written: boolean | null;
  published: boolean | null;
  parkedBy: string | null;
  target: string | null;
}

export function summarise(beats: Beat[]): RunSummary {
  const done = beats.find((b) => b.kind === "done");
  const approval = beats.find((b) => b.kind === "approval");
  // The approval beat prints "target   <path>" on its own line. Reading it back
  // out is not ideal, but the alternative is inventing a path, and the beat is
  // the only place the stream carries it.
  let target: string | null = null;
  if (approval) {
    const match = approval.text.match(/target\s+(\S+)/);
    target = match ? match[1] : null;
  }
  return {
    planHash: done?.plan_hash ?? null,
    written: done ? done.written ?? null : null,
    published: done ? done.published ?? null : null,
    parkedBy: done?.parked_by ?? null,
    target,
  };
}
