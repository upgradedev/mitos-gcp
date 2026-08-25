import { useEffect, useRef } from "react";
import type { Beat } from "../api/types";
import { groupBeats } from "./stages";

// Beats whose meaning is a refusal or a stop. Coloured so that the two things
// a visitor is here to see are not the same grey as everything else.
const REFUSAL = new Set(["guard", "halt", "finding", "repair", "escalate", "error"]);
const NOTABLE = new Set(["approval", "identity", "divergence"]);

function toneOf(kind: string): string {
  if (kind === "error") return "text-rose-700 dark:text-rose-300";
  if (REFUSAL.has(kind)) return "text-amber-700 dark:text-amber-300";
  if (NOTABLE.has(kind)) return "text-sky-800 dark:text-sky-200";
  return "text-ink-700 dark:text-ink-300";
}

export default function RunStream({
  beats,
  live,
}: {
  beats: Beat[];
  live: boolean;
}) {
  const groups = groupBeats(beats);
  const foot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!live) return;
    // Only follow along while it is actually streaming, so a visitor reading
    // back through a finished run is not dragged to the bottom.
    foot.current?.scrollIntoView({ block: "end" });
  }, [beats.length, live]);

  if (beats.length === 0) return null;

  return (
    <ol className="space-y-4">
      {groups.map((group) => {
        // Recall is by far the loudest phase: a live run recites more than
        // fifty recollections out of seventy-five beats. It matters that it
        // happened, and it does not need to be read line by line, so it is
        // folded away with its real count rather than trimmed or dropped.
        const fold = group.phase.id === "recall" && group.beats.length > 4;
        const beatCount = group.beats.reduce((n, b) => n + b.repeats, 0);

        const rows = (
          <ul className="mt-1.5 space-y-1 border-l-2 border-ink-200 pl-3 dark:border-ink-800">
            {group.beats.map((item) => (
              <li key={item.index} className="scroll-x">
                <div className="flex items-start gap-2">
                  <span className="mt-px shrink-0 font-mono text-[11px] uppercase tracking-wide text-ink-400 dark:text-ink-500">
                    {item.beat.kind}
                  </span>
                  {item.repeats > 1 && (
                    <span className="shrink-0 rounded bg-ink-100 px-1.5 text-[11px] text-ink-600 dark:bg-ink-800 dark:text-ink-300">
                      {item.repeats}&times;
                    </span>
                  )}
                </div>
                <pre
                  className={`whitespace-pre-wrap break-words font-mono text-xs ${toneOf(
                    item.beat.kind
                  )}`}
                >
                  {item.beat.text}
                </pre>
              </li>
            ))}
          </ul>
        );

        return (
          <li key={group.phase.id}>
            <div className="flex flex-wrap items-baseline gap-x-2">
              <h3 className="text-sm font-semibold">{group.phase.title}</h3>
              <span className="text-xs text-ink-500 dark:text-ink-400">
                {group.phase.blurb}
              </span>
            </div>

            {fold ? (
              <details className="mt-1.5">
                <summary className="cursor-pointer rounded text-xs text-ink-600 dark:text-ink-300">
                  {beatCount} recollections from the thread. Show them.
                </summary>
                {rows}
              </details>
            ) : (
              rows
            )}
          </li>
        );
      })}
      <div ref={foot} />
    </ol>
  );
}
