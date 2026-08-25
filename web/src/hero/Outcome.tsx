import { AlertTriangle, Clock, FileCheck2, ShieldCheck, XCircle } from "lucide-react";
import type { Beat, RunState } from "../api/types";
import { summarise } from "./stages";

interface Props {
  state: RunState;
  beats: Beat[];
  retryAfterSeconds: number | null;
  failure: string | null;
}

function Panel({
  tone,
  icon,
  title,
  children,
}: {
  tone: "good" | "warn" | "bad";
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  const ring =
    tone === "good"
      ? "border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950"
      : tone === "warn"
        ? "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950"
        : "border-rose-300 bg-rose-50 dark:border-rose-900 dark:bg-rose-950";
  return (
    <section className={`rounded-xl border p-4 ${ring}`}>
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        {icon}
        {title}
      </h3>
      <div className="mt-2 space-y-2 text-sm">{children}</div>
    </section>
  );
}

export default function Outcome({
  state,
  beats,
  retryAfterSeconds,
  failure,
}: Props) {
  if (state === "rate-limited") {
    return (
      <Panel
        tone="warn"
        icon={<Clock size={16} aria-hidden="true" />}
        title="Too many runs for now"
      >
        <p>
          A real chore costs model calls, so this endpoint allows ten runs per
          visitor every ten minutes. You have used them.
        </p>
        <p>
          {retryAfterSeconds === null
            ? "The server did not say how long to wait. Try again in a few minutes."
            : `The server asked you to wait ${retryAfterSeconds} second${
                retryAfterSeconds === 1 ? "" : "s"
              }.`}
        </p>
      </Panel>
    );
  }

  if (state === "failed") {
    const errorBeat = beats.find((b) => b.kind === "error");
    return (
      <Panel
        tone="bad"
        icon={<XCircle size={16} aria-hidden="true" />}
        title="The run did not finish"
      >
        <p className="font-mono text-xs break-words">
          {errorBeat?.text ?? failure ?? "unknown"}
        </p>
        <p>Nothing was written. A run that fails writes nothing by design.</p>
      </Panel>
    );
  }

  const done = beats.find((b) => b.kind === "done");
  if (!done) return null;

  const { planHash, written, published, parkedBy, target } = summarise(beats);
  const guardDenied = beats.some(
    (b) => b.kind === "guard" && b.text.includes("ADK refused")
  );

  if (written === true) {
    return (
      <Panel
        tone="good"
        icon={<FileCheck2 size={16} aria-hidden="true" />}
        title="Approved and written"
      >
        <Bytes target={target} planHash={planHash} />
        <p>
          Published to the specification repository:{" "}
          <strong>{published === true ? "yes" : "no"}</strong>. This interface
          keeps the write and the publish apart because the service does.
        </p>
      </Panel>
    );
  }

  // The common outcome, and the one worth watching: a card exists, and nothing
  // was written because nobody approved it.
  return (
    <Panel
      tone="warn"
      icon={<ShieldCheck size={16} aria-hidden="true" />}
      title="A change is proposed. Nothing was written."
    >
      <Bytes target={target} planHash={planHash} />
      {guardDenied && (
        <p className="flex items-start gap-2">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            During the run a specialist was handed the write tool on purpose and
            was refused it. That refusal is enforced outside this service, by
            Google IAM, so the service cannot grant it to itself.
          </span>
        </p>
      )}
      {parkedBy && (
        <p>
          Parked by <strong>{parkedBy}</strong>. A specialist is allowed to
          refuse, and this one did.
        </p>
      )}
      <p className="text-ink-700 dark:text-ink-300">
        Approving is a separate act by a person, and it names this exact hash.
        This page cannot approve on your behalf: the running service holds no
        write credential at all.
      </p>
    </Panel>
  );
}

function Bytes({
  target,
  planHash,
}: {
  target: string | null;
  planHash: string | null;
}) {
  return (
    <dl className="space-y-1">
      <div className="scroll-x">
        <dt className="text-xs text-ink-600 dark:text-ink-400">
          The one file it would change
        </dt>
        <dd className="font-mono text-xs">{target ?? "unknown"}</dd>
      </div>
      <div className="scroll-x">
        <dt className="text-xs text-ink-600 dark:text-ink-400">
          sha256 of the exact bytes
        </dt>
        <dd className="font-mono text-xs break-all">{planHash ?? "unknown"}</dd>
      </div>
    </dl>
  );
}
