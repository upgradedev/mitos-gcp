import { Construction } from "lucide-react";

// A route that exists in the navigation but is not built yet. It says so.
//
// The alternative is a screen of plausible placeholder content, which is the
// specific failure this project has spent effort removing: a number nobody
// measured is worse than an admission that the screen is not finished.
//
// Currently unreferenced. The boundary and thread routes are wired to
// ./views/BoundaryView and ./views/ThreadView, which another agent owns and was
// still editing when they were adopted. This is kept as the fallback: if that
// work is not ready at deploy time, swap either route back to
//
//   <Seam title="The thread" purpose="..." sources={["GET /thread?limit="]} />
//
// and the build is green again without touching a file this agent does not own.
export default function Seam({
  title,
  purpose,
  sources,
}: {
  title: string;
  purpose: string;
  sources: string[];
}) {
  return (
    <section className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 max-w-2xl text-sm text-ink-600 dark:text-ink-300">
        {purpose}
      </p>

      <div className="card mt-5 p-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Construction size={16} aria-hidden="true" />
          This screen is not built yet
        </h2>
        <p className="mt-2 text-sm text-ink-600 dark:text-ink-300">
          Rather than show numbers nobody has measured, it says nothing. It will
          be filled from these endpoints, which are live now:
        </p>
        <ul className="mt-2 space-y-1">
          {sources.map((source) => (
            <li key={source} className="font-mono text-xs">
              {source}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
