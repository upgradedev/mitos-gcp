// Repositories, as the four questions a visitor actually arrives with.
//
//   How do I connect one?      the three steps, and the limit on step two
//   How do I see the connected ones?   the allowlist GET /config publishes
//   How do I see the history of each?  the runs, grouped out of the thread
//   How do I audit one?        the form, calling GET /standards.json
//
// Two of those already worked and were unreachable: the server rendered
// /connect page and /standards?repository= both still answer, and nothing
// linked to them. The wording on the honest parts of /connect is reused here
// rather than rewritten, because it was reviewed and it is true.
//
// Three things this page is careful about.
//
// A run whose triggers carry no repository is not missing data. It means the
// specialists read the built-in demo corpus rather than a checkout, most runs
// in this thread are like that, and they are shown under their own heading
// rather than filtered out or dressed up as a repository.
//
// The allowlist is deployment configuration. Nobody can add a repository from
// this page and no amount of interface would change that, so it is said next
// to the instructions as a limit rather than as a coming-soon.
//
// The audit reads over the public GitHub API with no credential. That is 60
// requests an hour shared by everybody using this page, and one audit opens up
// to 300 files, so a large repository exhausts it and the remaining rules come
// back as could not be determined rather than as passes. That is on the form,
// not in a footnote.

import { useEffect, useMemo, useRef, useState } from "react";
import { HttpError, getConfig, getStandards, getThread, load } from "../api/client";
import type { Config, Loaded, Standards, StandardsFinding, Thread } from "../api/types";
import { groupIntoRuns, outcomeOf, stampOf, vocabularyFor } from "./thread-model";
import type { RunSummary } from "./thread-model";
import {
  provenanceOf,
  subjectsOf,
  verdictStyleFor,
  worstFirst,
} from "./repositories-model";
import type { Subject } from "./repositories-model";
import "./repositories-view.css";

// The same window the thread view asks for. Counting how many runs a
// repository has from a truncated window would produce a number that is wrong
// rather than merely partial, so this asks for more than the thread holds and
// says so when it turns out not to have been enough.
const DEFAULT_LIMIT = 2000;

// One repository in this thread has fifty runs. None is hidden; they are drawn
// a page at a time so the pane stays usable.
const RUNS_PER_PAGE = 12;

const NO_ENTRIES: Thread["entries"] = [];

export interface RepositoriesViewProps {
  limit?: number;
}

// What the audit is doing, as a set of states that are told apart because the
// reader has to do something different about each one.
type Audit =
  | { status: "idle" }
  | { status: "reading"; what: string }
  | { status: "ok"; value: Standards }
  | { status: "bad-name"; detail: string }
  | { status: "rate-limited"; detail: string }
  | { status: "failed"; detail: string }
  | { status: "empty" };

export function RepositoriesView({ limit = DEFAULT_LIMIT }: RepositoriesViewProps) {
  const [thread, setThread] = useState<Loaded<Thread>>({ status: "loading" });
  const [config, setConfig] = useState<Loaded<Config>>({ status: "loading" });
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [shown, setShown] = useState(RUNS_PER_PAGE);

  const [typed, setTyped] = useState("");
  const [audit, setAudit] = useState<Audit>({ status: "idle" });
  // Only the most recent request may write a result. Without this, a slow
  // audit landing after a fast one would replace an answer the reader is
  // already reading with an older one.
  const attempt = useRef(0);

  useEffect(() => {
    let live = true;
    load(() => getThread(limit)).then((r) => live && setThread(r));
    load(getConfig).then((r) => live && setConfig(r));
    return () => {
      live = false;
    };
  }, [limit]);

  const entries = useMemo(
    () => (thread.status === "ok" ? thread.value.entries : NO_ENTRIES),
    [thread]
  );
  const runs = useMemo(() => groupIntoRuns(entries), [entries]);
  const allowlist = useMemo(
    () => (config.status === "ok" ? config.value.webhook_repositories : []),
    [config]
  );
  const subjects = useMemo(
    () => subjectsOf(runs, allowlist),
    [runs, allowlist]
  );

  // Opens on the first connected repository that has actually run, because
  // that is the thing the page is about. Falls back to whatever has runs, then
  // to the first row, so the pane is never blank while rows exist.
  const fallback =
    subjects.find((s) => s.kind === "allowlisted" && s.runs.length > 0) ??
    subjects.find((s) => s.runs.length > 0) ??
    subjects[0] ??
    null;
  const chosen = subjects.find((s) => s.id === chosenId) ?? fallback;

  const choose = (id: string) => {
    setChosenId(id);
    setShown(RUNS_PER_PAGE);
  };

  const runAudit = async (repository: string | null) => {
    const mine = attempt.current + 1;
    attempt.current = mine;
    setAudit({
      status: "reading",
      what: repository ?? "the built-in demo corpus",
    });
    try {
      const value = await getStandards(repository);
      if (attempt.current === mine) setAudit({ status: "ok", value });
    } catch (err) {
      if (attempt.current !== mine) return;
      if (err instanceof HttpError && err.status === 400) {
        setAudit({ status: "bad-name", detail: err.detail });
      } else if (err instanceof HttpError && err.status === 429) {
        setAudit({ status: "rate-limited", detail: err.detail });
      } else {
        setAudit({
          status: "failed",
          detail: err instanceof Error ? err.message : String(err),
        });
      }
    }
  };

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = typed.trim();
    if (name.length === 0) {
      setAudit({ status: "empty" });
      return;
    }
    void runAudit(name);
  };

  const allowlisted = subjects.filter((s) => s.kind === "allowlisted");
  const unlisted = subjects.filter((s) => s.kind === "unlisted");
  const demo = subjects.find((s) => s.kind === "demo") ?? null;
  const outside = subjects.find((s) => s.kind === "outside") ?? null;

  return (
    <section className="mitos-repos">
      <header className="mitos-repos__head">
        <h1 className="mitos-repos__title">Repositories</h1>
        <p className="mitos-repos__lede">
          Which repositories this deployment accepts work from, what it has
          already done for each of them, how to audit any public repository
          against the engineering standard, and what connecting a new one
          involves. Everything counted here is counted from responses this page
          fetched. Where a number was not in one, it is not here.
        </p>
        <ul className="mitos-repos__questions">
          <li>which ones are connected</li>
          <li>what each one has run</li>
          <li>audit any public repository</li>
          <li>connect a new one</li>
        </ul>
      </header>

      <div className="mitos-repos__body">
        <section className="mitos-repos__section">
          <h2 className="mitos-repos__label">Connected repositories</h2>
          <Connected
            config={config}
            thread={thread}
            limit={limit}
            runs={runs}
            allowlisted={allowlisted}
            unlisted={unlisted}
            demo={demo}
            outside={outside}
            chosen={chosen}
            onChoose={choose}
            shown={shown}
            onShowMore={() => setShown(shown + RUNS_PER_PAGE)}
          />
        </section>

        <section className="mitos-repos__section">
          <h2 className="mitos-repos__label">Audit any public repository</h2>
          <p className="mitos-repos__note">
            Twenty four rules, decided from the contents of the repository. This
            needs no account and nothing installed, and it works on a repository
            that is not connected: auditing reads, it does not subscribe.
          </p>
          <form className="mitos-form" onSubmit={onSubmit}>
            <div className="mitos-form__field">
              <label className="mitos-form__label" htmlFor="mitos-audit-name">
                Repository to audit
              </label>
              <span className="mitos-form__hint" id="mitos-audit-hint">
                Written as owner/name. It has to be public, because this reads
                with no credential.
              </span>
              <input
                className="mitos-form__input"
                id="mitos-audit-name"
                name="repository"
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="owner/name"
                value={typed}
                aria-invalid={audit.status === "bad-name" ? "true" : undefined}
                aria-describedby={
                  audit.status === "bad-name"
                    ? "mitos-audit-hint mitos-audit-error"
                    : "mitos-audit-hint"
                }
                onChange={(event) => setTyped(event.target.value)}
              />
            </div>

            <div className="mitos-form__buttons">
              <button className="mitos-button" type="submit">
                Audit it
              </button>
              <button
                className="mitos-button is-secondary"
                type="button"
                onClick={() => void runAudit(null)}
              >
                Audit the built-in demo corpus instead
              </button>
            </div>

            <AuditProblem audit={audit} />

            <p className="mitos-form__limit">
              <b>What this costs, before you press it.</b> The audit reads over
              the public GitHub API with no credential. That allows 60 requests
              an hour and the allowance is shared by everybody using this page,
              not per visitor. One audit opens up to 300 files. A repository
              large enough to exhaust the allowance does not fail: the rules
              whose files were refused come back as could not be determined
              rather than as passes, and they are drawn differently below so you
              can see which ones those were. The demo corpus button reads
              nothing over the network and spends none of it.
            </p>
          </form>

          <AuditResult audit={audit} />
        </section>

        <section className="mitos-repos__section">
          <h2 className="mitos-repos__label">Connect a repository</h2>
          <Connect config={config} />
        </section>
      </div>
    </section>
  );
}

// ---- connected repositories and their history ---------------------------

function Connected({
  config,
  thread,
  limit,
  runs,
  allowlisted,
  unlisted,
  demo,
  outside,
  chosen,
  onChoose,
  shown,
  onShowMore,
}: {
  config: Loaded<Config>;
  thread: Loaded<Thread>;
  limit: number;
  runs: RunSummary[];
  allowlisted: Subject[];
  unlisted: Subject[];
  demo: Subject | null;
  outside: Subject | null;
  chosen: Subject | null;
  onChoose: (id: string) => void;
  shown: number;
  onShowMore: () => void;
}) {
  if (config.status === "loading" || thread.status === "loading") {
    return (
      <div className="mitos-state">
        <div className="mitos-state__title">Reading the allowlist and the thread.</div>
        <div className="mitos-state__body">
          Asking GET /config for the repositories deliveries are accepted from,
          and GET /thread for the most recent {limit} entries.
        </div>
      </div>
    );
  }

  if (config.status !== "ok") {
    return (
      <Unreadable
        what="GET /config"
        absent={config.status === "absent"}
        detail={config.detail}
        why="That response is the only place the allowlist is published, so with it missing this page cannot say which repositories are connected. It will not guess from the thread: a repository that ran once is not evidence that deliveries from it are still accepted."
      />
    );
  }

  if (thread.status !== "ok") {
    return (
      <>
        <p className="mitos-repos__note">
          The allowlist was read. The history was not, so every run count below
          would be a number nobody counted and none is shown.
        </p>
        <div className="mitos-evidence">
          {config.value.webhook_repositories.map((name) => (
            <div className="mitos-evidence__row" key={name}>
              <span className="mitos-evidence__key">accepts deliveries from</span>
              <span className="mitos-evidence__val">{name}</span>
            </div>
          ))}
        </div>
        <Unreadable
          what="GET /thread"
          absent={thread.status === "absent"}
          detail={thread.detail}
          why="Every run count and every date on this page is counted from that response."
        />
      </>
    );
  }

  const count = thread.value.count;
  const windowIsFull = count >= limit;
  const list = config.value.webhook_repositories;

  return (
    <>
      <div className="mitos-repos__facts">
        <span>
          <b>{list.length}</b>{" "}
          {list.length === 1 ? "repository is" : "repositories are"} on the
          allowlist
        </span>
        <span>
          <b>{runs.length}</b> runs in the thread
        </span>
        <span>
          <b>{count}</b> entries loaded
        </span>
      </div>

      <p className="mitos-repos__note">
        The allowlist is the set of repositories a webhook delivery is accepted
        from, read from GET /config. The run counts beside them are counted from
        GET /thread, which is a different response about a different thing: a
        repository can be connected and never have run, and a run can exist with
        no repository at all.
      </p>

      {windowIsFull ? (
        <p className="mitos-repos__caveat">
          These counts are over the most recent {count} entries. GET /thread
          reports how many it returned, not how many exist, so there may be
          older runs this page has not seen and every count below is at least
          this rather than exactly this.
        </p>
      ) : null}

      <div className="mitos-repos__layout">
        <nav className="mitos-repos__pane" aria-label="Repositories">
          <div className="mitos-repos__pane-head">1 &middot; pick one</div>
          <div className="mitos-subjects">
            <div className="mitos-subjects__group">
              On the allowlist, connected
            </div>
            {allowlisted.length === 0 ? (
              <p className="mitos-subject__sub">
                GET /config returned an empty allowlist. This deployment accepts
                a webhook delivery from no repository at all.
              </p>
            ) : (
              allowlisted.map((subject) => (
                <SubjectButton
                  key={subject.id}
                  subject={subject}
                  open={chosen !== null && chosen.id === subject.id}
                  onChoose={onChoose}
                />
              ))
            )}

            {unlisted.length > 0 ? (
              <>
                <div className="mitos-subjects__group">
                  Named by the thread, not on the allowlist now
                </div>
                {unlisted.map((subject) => (
                  <SubjectButton
                    key={subject.id}
                    subject={subject}
                    open={chosen !== null && chosen.id === subject.id}
                    onChoose={onChoose}
                  />
                ))}
              </>
            ) : null}

            <div className="mitos-subjects__group">Not a repository</div>
            {demo === null ? null : (
              <SubjectButton
                subject={demo}
                open={chosen !== null && chosen.id === demo.id}
                onChoose={onChoose}
              />
            )}
            {outside === null ? null : (
              <SubjectButton
                subject={outside}
                open={chosen !== null && chosen.id === outside.id}
                onChoose={onChoose}
              />
            )}
          </div>
        </nav>

        <div className="mitos-repos__pane">
          <div className="mitos-repos__pane-head">
            2 &middot; what it has run, newest first
          </div>
          <div className="mitos-history">
            {chosen === null ? (
              <p className="mitos-state__body">
                There is nothing to select: the allowlist is empty and the
                thread returned no runs.
              </p>
            ) : (
              <History subject={chosen} shown={shown} onShowMore={onShowMore} />
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function titleOf(subject: Subject): string {
  if (subject.kind === "demo") return "the built-in demo corpus";
  if (subject.kind === "outside") return "recorded outside a pull request";
  return subject.name ?? "unnamed";
}

function SubjectButton({
  subject,
  open,
  onChoose,
}: {
  subject: Subject;
  open: boolean;
  onChoose: (id: string) => void;
}) {
  // Every kind of run in this bucket, once each, so the shape of what happened
  // is visible before anything is opened. Same vocabulary and the same eight
  // colours as the thread screen.
  const kinds: string[] = [];
  for (const run of subject.runs) {
    for (const { kind } of run.kindCounts) {
      if (!kinds.includes(kind)) kinds.push(kind);
    }
  }

  return (
    <button
      type="button"
      className={open ? "mitos-subject is-open" : "mitos-subject"}
      onClick={() => onChoose(subject.id)}
      aria-current={open ? "true" : undefined}
    >
      <span className="mitos-subject__top">
        <span className="mitos-subject__name">{titleOf(subject)}</span>
      </span>
      <span className="mitos-subject__sub">
        {/* Zero is written out in the same shape as any other count, so a
            repository that has run nothing reads as an answer rather than as a
            row that failed to load. */}
        {subject.runs.length === 0
          ? "no runs in this window"
          : (subject.runs.length === 1 ? "1 run" : subject.runs.length + " runs") +
            ", last on " +
            (subject.lastAt ?? "").slice(0, 10)}
      </span>
      <span className="mitos-subject__dots">
        {kinds.map((kind) => {
          const vocab = vocabularyFor(kind);
          return (
            <span
              key={kind}
              className={`mitos-dot tone-${vocab.tone}`}
              title={vocab.label}
            />
          );
        })}
      </span>
    </button>
  );
}

function History({
  subject,
  shown,
  onShowMore,
}: {
  subject: Subject;
  shown: number;
  onShowMore: () => void;
}) {
  // Zero is an answer, and each of the four buckets means something different
  // by it. None of them is a failure and none of them is drawn as one.
  if (subject.runs.length === 0) {
    return <NoRuns subject={subject} />;
  }

  const visible = subject.runs.slice(0, shown);

  return (
    <>
      <p className="mitos-history__intro">
        <Preamble subject={subject} /> Each row says where that run stopped,
        counted from the entries it recorded. To walk any of them backwards
        entry by entry, open <a href="#/thread">the thread</a>.
      </p>
      {visible.map((run) => (
        <RunRow key={run.id} run={run} />
      ))}
      {subject.runs.length > visible.length ? (
        <button className="mitos-button is-secondary" type="button" onClick={onShowMore}>
          Showing {visible.length} of {subject.runs.length}. Show more.
        </button>
      ) : null}
    </>
  );
}

function NoRuns({ subject }: { subject: Subject }) {
  if (subject.kind === "demo") {
    return (
      <div className="mitos-empty">
        <b>No run in this window read the built-in demo corpus.</b> Every run
        the thread holds names a repository. Nothing is missing here.
      </div>
    );
  }
  if (subject.kind === "outside") {
    return (
      <div className="mitos-empty">
        <b>Every run in this window was started by a trigger.</b> Nothing was
        recorded outside a pull request, so there are no parked findings and no
        escalations to show.
      </div>
    );
  }
  if (subject.kind === "unlisted") {
    return (
      <div className="mitos-empty">
        <b>{subject.name} has no runs in this window.</b> It appears here
        because the thread names it somewhere the loaded window does not reach.
      </div>
    );
  }
  return (
    <div className="mitos-empty">
      <b>{subject.name} has run nothing in this window.</b> That is an answer
      rather than a failure, and nothing here is broken. It is on the allowlist,
      so a signed delivery from it would be accepted; none has arrived, or none
      has arrived recently enough to be among the entries this page loaded. The
      way to give it something to do is a pull request in that repository, with
      the webhook set up as step 2 below describes.
    </div>
  );
}

function Preamble({ subject }: { subject: Subject }) {
  const n = subject.runs.length;
  if (subject.kind === "demo") {
    return (
      <>
        <b>
          {n} {n === 1 ? "run" : "runs"} against the built-in demo corpus.
        </b>{" "}
        No repository is recorded for these, so the specialists read the sample
        that ships with the service rather than a checkout. That is what the
        absence of a repository name means here, and it is most of this thread.
      </>
    );
  }
  if (subject.kind === "outside") {
    return (
      <>
        <b>
          {n} {n === 1 ? "run" : "runs"} with no trigger at all.
        </b>{" "}
        Nothing started these from a repository. They are the findings a person
        parked and the deferrals that later expired and were escalated, which
        the fleet records against no pull request.
      </>
    );
  }
  if (subject.kind === "unlisted") {
    return (
      <>
        <b>
          {n} {n === 1 ? "run" : "runs"} for {subject.name}.
        </b>{" "}
        These are real and recorded, but this repository is not on the allowlist
        that GET /config publishes now, so a delivery from it would not be
        accepted today. The thread keeps what happened; the configuration
        changed after it.
      </>
    );
  }
  return (
    <>
      <b>
        {n} {n === 1 ? "run" : "runs"} for {subject.name}.
      </b>{" "}
    </>
  );
}

function RunRow({ run }: { run: RunSummary }) {
  const provenance = provenanceOf(run);
  return (
    <article className="mitos-run">
      <div className="mitos-run__top">
        <span className="mitos-run__name">
          {run.pr === null ? "run " + run.id : "Pull request " + run.pr}
        </span>
        <span className="mitos-run__when">{stampOf(run.lastAt)}</span>
      </div>
      {run.title === null ? null : (
        <div className="mitos-run__title">{run.title}</div>
      )}
      <div className="mitos-run__outcome">
        Where it stopped: <b>{outcomeOf(run)}</b>
      </div>
      <div className="mitos-run__meta">
        {provenance.sentence}
        {provenance.deliveryId === null ? (
          ""
        ) : (
          <>
            , delivery id: <code>{provenance.deliveryId}</code>
          </>
        )}
        . {run.entries.length}{" "}
        {run.entries.length === 1 ? "entry" : "entries"} recorded
        {run.filesChanged === null
          ? ""
          : ", " +
            run.filesChanged +
            (run.filesChanged === 1 ? " file changed" : " files changed")}
        .
      </div>
      <div className="mitos-run__dots">
        {run.kindCounts.map(({ kind, count }) => {
          const vocab = vocabularyFor(kind);
          return (
            <span
              key={kind}
              className={`mitos-dot tone-${vocab.tone}`}
              title={`${count} ${vocab.label}`}
            />
          );
        })}
      </div>
    </article>
  );
}

// ---- the audit ----------------------------------------------------------

function AuditProblem({ audit }: { audit: Audit }) {
  if (audit.status === "empty") {
    return (
      <div className="mitos-form__error" id="mitos-audit-error">
        The box is empty. Type a repository as owner/name, or use the demo
        corpus button beside it, which needs no name and no network request.
      </div>
    );
  }

  if (audit.status === "bad-name") {
    return (
      <div className="mitos-form__error" id="mitos-audit-error">
        That is not a repository name, so nothing was read and nothing was
        spent. What you typed is still in the box: correct it rather than
        starting again. The service said why:
        <span className="mitos-form__error-detail">{audit.detail}</span>
      </div>
    );
  }

  if (audit.status === "rate-limited") {
    return (
      <div className="mitos-form__error" id="mitos-audit-error">
        This service rationed the request before it reached GitHub, so nothing
        was read. This is the service protecting its own budget, not GitHub
        refusing. Wait for the time it names and press the button again, or use
        the demo corpus button, which is local work and is not rationed. The
        detail mentions /runs; that address now redirects to the thread screen,
        and the history above is the same record.
        <span className="mitos-form__error-detail">{audit.detail}</span>
      </div>
    );
  }

  if (audit.status === "failed") {
    return (
      <div className="mitos-form__error" id="mitos-audit-error">
        The request to GET /standards.json did not come back, so there is
        nothing to show and this page will not fill it in from memory. One known
        cause: the deployed content policy sets default-src to none and declares
        no connect-src, and a browser refuses that request before it is sent.
        Try once more; if it repeats, the service or the policy needs attention
        rather than the name you typed.
        <span className="mitos-form__error-detail">{audit.detail}</span>
      </div>
    );
  }

  return null;
}

function AuditResult({ audit }: { audit: Audit }) {
  if (audit.status === "reading") {
    return (
      <div className="mitos-state">
        <div className="mitos-state__title">Auditing {audit.what}.</div>
        <div className="mitos-state__body">
          Asking GET /standards.json. A named repository is read file by file
          over the public GitHub API, so this takes a few seconds rather than an
          instant.
        </div>
      </div>
    );
  }

  if (audit.status !== "ok") return null;

  const { repository, summary, findings, note, agentic_pass } = audit.value;
  const ordered = worstFirst(findings);

  return (
    <div className="mitos-audit">
      <p className="mitos-audit__what">
        {repository === null ? (
          <>
            Audited the <b>built-in demo corpus</b>, the sample that ships with
            the service. No repository was read and no GitHub request was spent.
          </>
        ) : (
          <>
            Audited <code>{repository}</code> over the public GitHub API.
          </>
        )}
      </p>

      <div className="mitos-tiles">
        <Tile n={summary.rules} k="rules in the standard" />
        <Tile n={summary.checked} k="checks that ran" />
        <Tile n={summary.passed} k="passed" tone="write" />
        <Tile n={summary.failed} k="failed" tone="refusal" />
        <Tile n={summary.suspected} k="suspected" />
        <Tile n={summary.not_applicable} k="not applicable" />
        <Tile
          n={summary.could_not_be_determined}
          k="could not be determined"
          tone="parked"
        />
      </div>

      <p className="mitos-repos__note">
        Every one of those is counted by the service, including the last, which
        is its own field in the response rather than a sum computed here. It
        holds the rules that could not be settled, and none of them is folded
        into the pass count. The findings below are worst first: what failed,
        then what raised a hand, then everything still open, then what does not
        apply, then what passed. Undecided rules are drawn as dashed outlines so
        that they cannot be read as passes at a glance.
      </p>

      {note.length > 0 ? (
        <div className="mitos-quote">
          {note}
          <span className="mitos-quote__source">
            GET /standards.json, note. The service wrote that sentence about the
            limit it read under, not this page.
          </span>
        </div>
      ) : null}

      {agentic_pass.length > 0 ? (
        <div className="mitos-quote">
          {agentic_pass}
          <span className="mitos-quote__source">
            GET /standards.json, agentic_pass. What this audit deliberately did
            not do.
          </span>
        </div>
      ) : null}

      <div className="mitos-repos__section">
        {ordered.map((finding) => (
          <Finding key={finding.rule} finding={finding} />
        ))}
      </div>
    </div>
  );
}

function Tile({ n, k, tone }: { n: number; k: string; tone?: string }) {
  return (
    <div className={tone === undefined ? "mitos-tile" : `mitos-tile tone-${tone}`}>
      <div className="mitos-tile__n">{n}</div>
      <div className="mitos-tile__k">{k}</div>
    </div>
  );
}

function Finding({ finding }: { finding: StandardsFinding }) {
  const style = verdictStyleFor(finding.verdict);
  const classes = ["mitos-finding", `tone-${style.tone}`];
  if (style.undecided) classes.push("is-undecided");

  return (
    <article className={classes.join(" ")}>
      <div className="mitos-finding__top">
        <span className="mitos-finding__rule">{finding.rule}</span>
        <span className={`mitos-badge tone-${style.tone}`}>{style.label}</span>
        <span className="mitos-badge tone-quiet">{finding.severity}</span>
      </div>
      <div className="mitos-finding__meaning">
        {style.meaning}
        {style.label === "could not be determined"
          ? ` (${finding.verdict})`
          : ""}
      </div>
      <div className="mitos-finding__rows">
        <Line k="what it looked for" v={finding.looked_for} />
        <Line k="what it found" v={finding.found} />
        <div className="mitos-finding__row">
          <span className="mitos-finding__key">what it opened</span>
          <span className="mitos-finding__val">
            {finding.looked_at.length === 0 ? (
              "nothing. This rule was not decided from any file, which is why it is undecided above."
            ) : (
              // Keyed by position as well as path: a rule may open the same
              // file twice and two identical keys would be a React warning
              // rather than a second row.
              finding.looked_at.map((path, i) => (
                <span key={path + String(i)}>
                  {i === 0 ? "" : ", "}
                  <code>{path}</code>
                </span>
              ))
            )}
          </span>
        </div>
        {finding.limitation.length === 0 ? null : (
          <Line k="what it could miss" v={finding.limitation} />
        )}
      </div>
    </article>
  );
}

function Line({ k, v }: { k: string; v: string }) {
  if (v.length === 0) return null;
  return (
    <div className="mitos-finding__row">
      <span className="mitos-finding__key">{k}</span>
      <span className="mitos-finding__val">{v}</span>
    </div>
  );
}

// ---- connecting one -----------------------------------------------------

function Connect({ config }: { config: Loaded<Config> }) {
  // Taken from the address bar rather than from a constant, so the endpoint
  // printed here belongs to whichever deployment is being read. The old server
  // rendered page does the same thing for the same reason: it once printed an
  // http:// address for a signed request, handed to somebody following
  // instructions who had no reason to doubt it.
  const endpoint = window.location.origin + "/webhook/github";

  return (
    <>
      <p className="mitos-repos__note">
        Three steps, and the second one has a limit that no interface can lift.
      </p>

      <div className="mitos-steps">
        <div className="mitos-step">
          <div className="mitos-step__rank">step 1</div>
          <div className="mitos-step__name">Audit it, now</div>
          <div className="mitos-step__body">
            Type a public repository into the form above and get a real answer in
            about a second. Thirteen rules are decided from the contents, five
            are handed to an agent that opens files and decides, and six cannot
            be answered from a repository at all and say so. Nothing to install,
            no account.
          </div>
        </div>

        <div className="mitos-step is-limited">
          <div className="mitos-step__rank">step 2</div>
          <div className="mitos-step__name">Wake it on a pull request</div>
          <div className="mitos-step__body">
            Add a webhook to your repository pointing at POST /webhook/github,
            content type application/json, event: pull requests. The signature is
            verified with HMAC-SHA256 over the raw body before anything is
            parsed.
          </div>
          <div className="mitos-step__limit">
            <b>You cannot do this part from this page.</b> The repository also
            has to be on the allowlist, which is deployment configuration rather
            than something any page can grant, so this step needs somebody with
            access to the service. That is a real limit and not a coming-soon.
            There is no button here that would help, and adding one that queued a
            request would only move the same wait somewhere less visible.
          </div>
        </div>

        <div className="mitos-step">
          <div className="mitos-step__rank">step 3</div>
          <div className="mitos-step__name">Read what it did</div>
          <div className="mitos-step__body">
            Every run appears above with where it stopped. Open{" "}
            <a href="#/thread">the thread</a> and click any outcome to light up
            the path back to the pull request that caused it. Nothing is written
            to your repository: the one write this fleet can make goes to a
            separate specification repository and needs a person to approve the
            exact bytes.
          </div>
        </div>
      </div>

      <div className="mitos-repos__section">
        <h3 className="mitos-repos__label">What you would be pointing at</h3>
        <div className="mitos-evidence">
          <Row k="endpoint" v={endpoint} />
          <Row
            k="signature"
            v="HMAC-SHA256 over the raw body, checked before parsing"
          />
          <Row k="events" v="pull_request, opened and synchronize" />
          <Row k="what it may do to your repository" v="nothing. It reads." />
        </div>
      </div>

      <div className="mitos-repos__section">
        <h3 className="mitos-repos__label">
          What a specialist is permitted to open
        </h3>
        {config.status === "ok" ? (
          <>
            <p className="mitos-repos__note">
              Bounded in code rather than asked for in a prompt, and published by
              GET /config so it can be checked rather than believed.
            </p>
            <div className="mitos-evidence">
              <Row
                k="paths it may read"
                v={
                  config.value.read_scope.length === 0
                    ? "none listed"
                    : config.value.read_scope.join("  ")
                }
              />
              <Row
                k="reads per run"
                v={`at most ${config.value.max_reads_per_run}`}
              />
              <Row
                k="bytes per read"
                v={`at most ${config.value.max_bytes_per_read}`}
              />
            </div>
          </>
        ) : (
          <Unreadable
            what="GET /config"
            absent={config.status === "absent"}
            detail={config.status === "loading" ? "still loading" : config.detail}
            why="These bounds are published there and nowhere else, so this section is unknown rather than empty."
          />
        )}
      </div>
    </>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="mitos-evidence__row">
      <span className="mitos-evidence__key">{k}</span>
      <span className="mitos-evidence__val">{v}</span>
    </div>
  );
}

function Unreadable({
  what,
  absent,
  detail,
  why,
}: {
  what: string;
  absent: boolean;
  detail: string;
  why: string;
}) {
  return (
    <div className="mitos-state">
      <div className="mitos-state__title">
        {absent
          ? `This build does not serve ${what}.`
          : `${what} could not be read.`}
      </div>
      <div className="mitos-state__body">
        {why}{" "}
        {absent
          ? "A 404 is a different fact from an empty answer, and this page will not show one as the other."
          : "One known cause: the deployed content policy sets default-src to none and declares no connect-src, and a browser refuses that request before it is sent."}
      </div>
      <div className="mitos-state__detail">{detail}</div>
    </div>
  );
}

export default RepositoriesView;
