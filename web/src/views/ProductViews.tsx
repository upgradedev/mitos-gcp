import { useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  ChevronRight,
  CircleDot,
  Clock3,
  Code2,
  ExternalLink,
  FileCode2,
  Github,
  GitPullRequest,
  KeyRound,
  LockKeyhole,
  Search,
  ServerCog,
  Settings2,
  ShieldCheck,
  Webhook,
} from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { approveSuggestedChange } from "../api/client";
import type { Config, GitHubAppStatus, Identity, Loaded, SessionStatus, Thread, WorkspaceAnalytics } from "../api/types";
import { groupIntoRuns, type RunSummary } from "./thread-model";

interface DataProps {
  thread: Loaded<Thread>;
  config: Loaded<Config>;
  githubApp: Loaded<GitHubAppStatus>;
  session: Loaded<SessionStatus>;
  analytics: Loaded<WorkspaceAnalytics>;
  identity: Loaded<Identity>;
  onNavigate: (route: "dashboard" | "pull-requests" | "repositories" | "activity" | "settings") => void;
}

function realRuns(thread: Loaded<Thread>): RunSummary[] {
  if (thread.status !== "ok") return [];
  return groupIntoRuns(thread.value.entries).filter(
    (run) => run.repository !== null && run.pr !== 4471
  );
}

function PageHeading({ eyebrow, title, detail, action }: { eyebrow: string; title: string; detail: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-5 border-b border-ink-800 pb-7 md:flex-row md:items-end md:justify-between">
      <div className="max-w-2xl">
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-cyan-400">{eyebrow}</p>
        <h1 className="text-balance text-3xl font-semibold tracking-[-0.03em] text-ink-50 md:text-4xl">{title}</h1>
        <p className="mt-3 text-pretty text-sm leading-6 text-ink-400">{detail}</p>
      </div>
      {action}
    </div>
  );
}

function EmptyWorkspace({ onNavigate }: Pick<DataProps, "onNavigate">) {
  return (
    <section className="mt-8 overflow-hidden rounded-2xl border border-cyan-500/25 bg-ink-900">
      <div className="grid lg:grid-cols-[1.1fr_.9fr]">
        <div className="p-7 md:p-10">
          <span className="inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-medium text-cyan-300">
            <CircleDot size={14} /> Ready for a real repository
          </span>
          <h2 className="mt-6 max-w-lg text-balance text-3xl font-semibold tracking-[-0.03em] text-ink-50">
            Turn every pull request into an explainable change decision.
          </h2>
          <p className="mt-4 max-w-xl text-sm leading-6 text-ink-400">
            Install the Mitos GitHub App, select repositories, and choose the schema, API, and security policies your team wants enforced. No sample PRs will be added.
          </p>
          <button onClick={() => onNavigate("repositories")} className="mt-7 inline-flex items-center gap-2 rounded-lg bg-cyan-400 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-cyan-300">
            <Github size={17} /> Connect GitHub <ArrowRight size={16} />
          </button>
        </div>
        <div className="border-t border-ink-800 bg-ink-950/60 p-7 lg:border-l lg:border-t-0">
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-ink-500">First analysis flow</p>
          <ol className="mt-6 flex flex-col gap-5">
            {[
              ["01", "GitHub signs the event", "Webhook identity and delivery are verified."],
              ["02", "Policies inspect the exact SHA", "Schema, API, and security checks run deterministically."],
              ["03", "Your team keeps control", "A suggested PR is created only after approval."],
            ].map(([number, title, detail]) => (
              <li key={number} className="flex gap-4">
                <span className="font-mono text-xs text-cyan-400">{number}</span>
                <div><p className="text-sm font-medium text-ink-100">{title}</p><p className="mt-1 text-xs leading-5 text-ink-500">{detail}</p></div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

export function DashboardView(props: DataProps) {
  const runs = useMemo(() => realRuns(props.thread), [props.thread]);
  const repos = props.config.status === "ok" ? props.config.value.webhook_repositories : [];
  const analytics = props.analytics.status === "ok" ? props.analytics.value : null;
  const summary = analytics?.summary;
  const severityColors = ["#fb7185", "#f59e0b", "#22d3ee", "#64748b"];

  return (
    <div className="page-wrap">
      <PageHeading eyebrow="Workspace intelligence" title="Change governance, at a glance." detail="Repository health, pull request analysis, policy findings, and human approvals from your connected GitHub workspace." action={<button onClick={() => props.onNavigate("repositories")} className="button-secondary"><Github size={16} /> Connect repository</button>} />
      {repos.length === 0 && runs.length === 0 ? <EmptyWorkspace onNavigate={props.onNavigate} /> : (
        <>
          <div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {([
              ["Repositories", summary?.repositories ?? repos.length, "Active webhook coverage", ServerCog],
              ["Analysed PRs", summary?.analysed_prs ?? runs.length, "Workspace-scoped decisions", GitPullRequest],
              ["Policy findings", summary?.findings ?? 0, "Across active repositories", AlertTriangle],
              ["Awaiting approval", summary?.pending_approvals ?? 0, "No autonomous writes", Clock3],
            ] as const).map(([label, value, detail, Icon]) => (
              <article className="metric-card" key={String(label)}><div className="flex items-start justify-between"><p className="text-sm text-ink-400">{String(label)}</p><Icon size={18} className="text-ink-500" /></div><p className="mt-6 text-3xl font-semibold tracking-tight text-ink-50">{String(value)}</p><p className="mt-2 text-xs text-ink-500">{String(detail)}</p></article>
            ))}
          </div>
          {analytics && <div className="mt-4 grid gap-4 xl:grid-cols-[1.45fr_.75fr]">
            <section className="card-dark p-5"><div className="flex items-start justify-between gap-4"><div><h2 className="text-sm font-semibold text-ink-100">Governance activity</h2><p className="mt-1 text-xs text-ink-500">Analyses and interventions over the last 14 days</p></div><span className="status-neutral">Live ledger</span></div><div className="mt-6 h-64" aria-label="Governance activity chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={analytics.trend}><defs><linearGradient id="analysedFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#22d3ee" stopOpacity={0.35}/><stop offset="100%" stopColor="#22d3ee" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#253042" vertical={false}/><XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fill: "#7d899b", fontSize: 11 }} axisLine={false} tickLine={false}/><YAxis allowDecimals={false} tick={{ fill: "#7d899b", fontSize: 11 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: "#111827", border: "1px solid #253042", borderRadius: 10, color: "#e7edf5" }}/><Area type="monotone" dataKey="analysed" stroke="#22d3ee" fill="url(#analysedFill)" strokeWidth={2}/><Area type="monotone" dataKey="attention" stroke="#fb7185" fill="transparent" strokeWidth={2}/></AreaChart></ResponsiveContainer></div></section>
            <section className="card-dark p-5"><h2 className="text-sm font-semibold text-ink-100">Findings by severity</h2><p className="mt-1 text-xs text-ink-500">Current analysed workspace</p><div className="mt-6 h-52" aria-label="Findings by severity chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={analytics.findings_by_severity} layout="vertical" margin={{ left: 4 }}><CartesianGrid stroke="#253042" horizontal={false}/><XAxis type="number" allowDecimals={false} hide/><YAxis type="category" dataKey="severity" width={62} tick={{ fill: "#9aa6b8", fontSize: 11 }} axisLine={false} tickLine={false}/><Tooltip cursor={{ fill: "#182235" }} contentStyle={{ background: "#111827", border: "1px solid #253042", borderRadius: 10, color: "#e7edf5" }}/><Bar dataKey="count" radius={[0, 6, 6, 0]}>{analytics.findings_by_severity.map((item, index) => <Cell key={item.severity} fill={severityColors[index]} />)}</Bar></BarChart></ResponsiveContainer></div><p className="mt-3 text-xs text-ink-500">{summary?.published_suggestions ?? 0} approval-gated suggestions published</p></section>
          </div>}
          {analytics && <div className="mt-4 grid gap-4 xl:grid-cols-[1.1fr_.9fr]"><section className="card-dark overflow-hidden"><div className="border-b border-ink-800 px-5 py-4"><h2 className="text-sm font-semibold text-ink-100">Repository health</h2></div><div className="divide-y divide-ink-800">{analytics.repositories.map((repo) => <div key={repo.repository} className="flex items-center justify-between gap-4 px-5 py-4"><div className="min-w-0"><p className="truncate text-sm font-medium text-ink-200">{repo.repository}</p><p className="mt-1 text-xs text-ink-500">{repo.analyses} analyses · {repo.last_activity ? repo.last_activity.slice(0, 10) : "No activity yet"}</p></div><span className={repo.status === "healthy" ? "status-success" : "status-warning"}>{repo.status === "healthy" ? "Healthy" : `${repo.attention} need attention`}</span></div>)}</div></section><section className="card-dark overflow-hidden"><div className="border-b border-ink-800 px-5 py-4"><h2 className="text-sm font-semibold text-ink-100">Recent activity</h2></div><div className="divide-y divide-ink-800">{analytics.recent_activity.slice(0, 5).map((item) => <div key={item.run_id} className="px-5 py-4"><div className="flex items-center justify-between gap-3"><p className="truncate text-sm text-ink-200">{item.repository} {item.pr ? `#${item.pr}` : ""}</p><span className="text-[11px] text-ink-600">{item.recorded_at.slice(5, 16).replace("T", " ")}</span></div><p className="mt-1 text-xs text-ink-500">{item.event} · {item.actor}</p></div>)}</div></section></div>}
          <RunTable runs={runs.slice(0, 6)} onNavigate={props.onNavigate} />
        </>
      )}
    </div>
  );
}

function RunTable({ runs, onNavigate, onSelect }: { runs: RunSummary[]; onNavigate: DataProps["onNavigate"]; onSelect?: (run: RunSummary) => void }) {
  return (
    <section className="mt-7 card-dark overflow-hidden">
      <div className="flex items-center justify-between border-b border-ink-800 px-5 py-4"><div><h2 className="text-sm font-semibold text-ink-100">Recent pull requests</h2><p className="mt-1 text-xs text-ink-500">Persisted webhook runs, newest first</p></div><button onClick={() => onNavigate("pull-requests")} className="text-xs font-medium text-cyan-400 hover:text-cyan-300">View all</button></div>
      {runs.length === 0 ? <div className="p-8 text-center text-sm text-ink-500">No real GitHub pull request has been analysed yet.</div> : <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left"><thead><tr className="border-b border-ink-800 text-xs text-ink-500"><th className="px-5 py-3 font-medium">Pull request</th><th className="px-5 py-3 font-medium">Repository</th><th className="px-5 py-3 font-medium">Decision</th><th className="px-5 py-3 font-medium">Findings</th><th className="px-5 py-3 font-medium">Updated</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id} className="border-b border-ink-800/70 text-sm last:border-0 hover:bg-ink-800/30"><td className="px-5 py-4"><button onClick={() => onSelect ? onSelect(run) : onNavigate("pull-requests")} className="font-medium text-ink-100 hover:text-cyan-300">#{run.pr} {run.title ?? "Untitled pull request"}</button><p className="mt-1 font-mono text-xs text-ink-600">{run.id.slice(0, 12)}</p></td><td className="px-5 py-4 text-ink-400">{run.repository}</td><td className="px-5 py-4"><Status run={run} /></td><td className="px-5 py-4 text-ink-300">{run.findings}</td><td className="px-5 py-4 text-xs text-ink-500">{relative(run.lastAt)}</td></tr>)}</tbody></table></div>}
    </section>
  );
}

function Status({ run }: { run: RunSummary }) {
  const state = run.write?.published ? ["Published", "status-success"] : run.gatePassed === false ? ["Review", "status-danger"] : run.plans > 0 ? ["Approval", "status-warning"] : ["Analysed", "status-neutral"];
  return <span className={String(state[1])}>{String(state[0])}</span>;
}

function relative(value: string) {
  const ms = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(ms)) return "Unknown";
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return "Less than an hour ago";
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function PullRequestsView(props: DataProps) {
  const runs = useMemo(() => realRuns(props.thread), [props.thread]);
  const [query, setQuery] = useState("");
  const [decision, setDecision] = useState("all");
  const [selected, setSelected] = useState<RunSummary | null>(null);
  const filtered = runs.filter((run) => {
    const matchesQuery = `${run.repository} ${run.title} ${run.pr}`.toLowerCase().includes(query.toLowerCase());
    const state = run.gatePassed === false ? "review" : run.plans > 0 && !run.write ? "approval" : "analysed";
    return matchesQuery && (decision === "all" || decision === state);
  });
  return <div className="page-wrap"><PageHeading eyebrow="Change intelligence" title="Pull requests" detail="Every row is backed by a verified GitHub delivery. Fixture PR 4471 and unscoped demo runs are excluded." />
    <div className="mt-7 flex flex-col gap-3 sm:flex-row"><label className="search-field"><Search size={16} /><span className="sr-only">Search pull requests</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search repository, title, or PR" /></label><label className="search-field sm:max-w-52"><CircleDot size={16} /><span className="sr-only">Filter by decision</span><select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="all">All decisions</option><option value="review">Needs review</option><option value="approval">Awaiting approval</option><option value="analysed">Analysed</option></select></label></div>
    <RunTable runs={filtered} onNavigate={props.onNavigate} onSelect={setSelected} />
    {selected && <RunDetail run={selected} session={props.session} onClose={() => setSelected(null)} />}
  </div>;
}

function RunDetail({ run, session, onClose }: { run: RunSummary; session: Loaded<SessionStatus>; onClose: () => void }) {
  const [approvalState, setApprovalState] = useState<"idle" | "working" | "published" | "error">("idle");
  const [approvalDetail, setApprovalDetail] = useState("");
  const membership = session.status === "ok" ? session.value.memberships[0] : undefined;
  const canApprove = membership?.role === "owner" || membership?.role === "reviewer";
  const approve = async () => {
    setApprovalState("working");
    try {
      const result = await approveSuggestedChange(run.id);
      setApprovalDetail(result.receipt.url ?? "Suggested pull request created.");
      setApprovalState("published");
    } catch (error) {
      setApprovalDetail(error instanceof Error ? error.message : "Approval failed");
      setApprovalState("error");
    }
  };
  const findings = run.entries.filter((entry) => entry.kind === "finding.raised" || entry.kind === "finding.deferred" || entry.kind === "finding.escalated");
  return <section aria-label={`Pull request ${run.pr} details`} className="card-dark mt-6 overflow-hidden"><div className="flex flex-col gap-4 border-b border-ink-800 p-6 md:flex-row md:items-start md:justify-between"><div><div className="flex flex-wrap items-center gap-2"><Status run={run} /><span className="font-mono text-xs text-ink-600">{run.id}</span></div><h2 className="mt-4 text-xl font-semibold text-ink-50">#{run.pr} {run.title ?? "Untitled pull request"}</h2><p className="mt-2 text-sm text-ink-400">{run.repository} · {run.filesChanged ?? "Unknown"} changed files</p></div><button onClick={onClose} className="button-secondary">Close</button></div><div className="grid lg:grid-cols-[1fr_280px]"><div className="p-6"><h3 className="text-xs font-semibold uppercase tracking-[0.15em] text-ink-500">Recorded findings</h3>{findings.length === 0 ? <p className="mt-5 text-sm text-ink-500">No findings were recorded for this run.</p> : <div className="mt-5 flex flex-col gap-3">{findings.map((entry) => <article key={entry.entry_id} className="rounded-lg border border-ink-800 bg-ink-950 p-4"><div className="flex items-center gap-2"><AlertTriangle size={15} className="text-amber-400" /><p className="text-sm font-medium text-ink-200">{entry.subject || "Finding requires review"}</p></div><p className="mt-2 text-xs leading-5 text-ink-500">{typeof entry.payload.reason === "string" ? entry.payload.reason : typeof entry.payload.summary === "string" ? entry.payload.summary : "See the technical trace for the recorded evidence."}</p></article>)}</div>}</div><aside className="border-t border-ink-800 bg-ink-950/50 p-6 lg:border-l lg:border-t-0"><h3 className="text-xs font-semibold uppercase tracking-[0.15em] text-ink-500">Governance</h3>{run.plans > 0 && !run.write && <div className="mt-5"><button type="button" onClick={approve} disabled={!canApprove || approvalState === "working" || approvalState === "published"} className="button-primary w-full disabled:cursor-not-allowed disabled:opacity-50"><GitPullRequest size={16} />{approvalState === "working" ? "Creating PR…" : approvalState === "published" ? "Suggested PR created" : "Approve suggested PR"}</button>{!canApprove && <p className="mt-2 text-xs leading-5 text-ink-600">Reviewer or owner access is required.</p>}{approvalDetail && <p className={`mt-2 break-words text-xs leading-5 ${approvalState === "error" ? "text-red-400" : "text-cyan-400"}`}>{approvalDetail}</p>}</div>}<dl className="mt-5 flex flex-col gap-4 text-xs"><div><dt className="text-ink-600">Policy result</dt><dd className="mt-1 text-ink-300">{run.gatePassed === null ? "Undetermined" : run.gatePassed ? "Passed" : "Review required"}</dd></div><div><dt className="text-ink-600">Suggested plan</dt><dd className="mt-1 text-ink-300">{run.plans > 0 ? "Recorded" : "None"}</dd></div><div><dt className="text-ink-600">Write</dt><dd className="mt-1 text-ink-300">{run.write?.published ? "Published" : "Not published"}</dd></div></dl>{run.plans > 0 && !run.write?.published && <div className="mt-6 rounded-lg border border-amber-500/20 bg-amber-500/5 p-4"><p className="text-xs font-medium text-amber-300">Approval is not yet available in this deployment.</p><p className="mt-2 text-xs leading-5 text-ink-500">No write action is simulated or sent from the browser.</p></div>}</aside></div></section>;
}

export function RepositoriesProductView(props: DataProps) {
  const repos = props.config.status === "ok" ? props.config.value.webhook_repositories : [];
  const runs = useMemo(() => realRuns(props.thread), [props.thread]);
  const setup = props.githubApp.status === "ok" ? props.githubApp.value : null;
  const installReady = setup?.configured === true && setup.install_url !== null;
  const installAction = installReady ? (
    <a href={setup.install_url ?? "/github/app/install"} className="button-primary"><Github size={16} /> Install GitHub App</a>
  ) : setup ? (
    <a href={setup.create_url} className="button-primary"><Github size={16} /> Create GitHub App</a>
  ) : (
    <span className="status-warning"><AlertTriangle size={13} /> Setup required</span>
  );

  return <div className="page-wrap"><PageHeading eyebrow="GitHub connection" title="Repositories" detail="Connect repositories through a GitHub App, receive verified events, and keep every suggested write behind approval." action={installAction} />
    {props.githubApp.status === "loading" ? <section className="card-dark mt-8 p-8 text-sm text-ink-500">Checking GitHub App configuration…</section> : props.githubApp.status === "error" || props.githubApp.status === "absent" ? <section className="card-dark mt-8 border-amber-500/30 p-7"><h2 className="text-base font-semibold text-ink-100">GitHub setup status is unavailable</h2><p className="mt-2 text-sm leading-6 text-ink-400">The product will not pretend a repository is connected. Check the service deployment and reload this page.</p></section> : !installReady ? <section className="mt-8 grid gap-6 lg:grid-cols-[1.1fr_.9fr]"><div className="card-dark p-7"><div className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber-400 text-ink-950"><Settings2 size={22} /></div><h2 className="mt-6 text-xl font-semibold text-ink-50">Create your Mitos GitHub App</h2><p className="mt-3 max-w-lg text-sm leading-6 text-ink-400">Mitos uses GitHub&apos;s official manifest flow to create the App with least-privilege permissions, configure its webhook, and return the generated credentials directly to secure server-side storage.</p><a href={setup?.create_url ?? "/github/app/new"} className="button-primary mt-6"><Github size={16} /> Create on GitHub <ExternalLink size={15} /></a><div className="mt-6 flex flex-wrap gap-2"><span className={setup?.app_slug ? "status-success" : "status-danger"}>App slug {setup?.app_slug ? "ready" : "missing"}</span><span className={setup?.webhook_secret_configured ? "status-success" : "status-danger"}>Webhook secret {setup?.webhook_secret_configured ? "ready" : "missing"}</span></div><p className="mt-5 font-mono text-xs text-ink-600">Webhook endpoint: {setup?.webhook_endpoint ?? "/webhook/github"}</p></div><SetupChecklist /></section> : repos.length === 0 ? <section className="mt-8 grid gap-6 lg:grid-cols-[1.1fr_.9fr]"><div className="card-dark p-7"><div className="flex h-11 w-11 items-center justify-center rounded-xl bg-cyan-400 text-ink-950"><Github size={22} /></div><h2 className="mt-6 text-xl font-semibold text-ink-50">Install Mitos on your repositories</h2><p className="mt-3 max-w-lg text-sm leading-6 text-ink-400">The App configuration is ready. Continue to GitHub to choose exactly which repositories Mitos can access.</p><a href={setup.install_url ?? "/github/app/install"} className="button-primary mt-6"><Github size={16} /> Continue to GitHub <ExternalLink size={15} /></a></div><SetupChecklist /></section> : <><div className="mt-7 grid gap-4">{repos.map((repo) => { const repoRuns = runs.filter((run) => run.repository === repo); return <article key={repo} className="card-dark flex flex-col gap-5 p-5 md:flex-row md:items-center"><div className="flex h-10 w-10 items-center justify-center rounded-lg border border-ink-700 bg-ink-800"><FileCode2 size={19} /></div><div className="min-w-0 flex-1"><h2 className="truncate text-sm font-semibold text-ink-100">{repo}</h2><p className="mt-1 text-xs text-ink-500">{repoRuns.length} verified analyses · schema, API, security</p></div><span className="status-success"><Webhook size={13} /> Accepting signed events</span></article>; })}</div><SetupChecklist compact /></>}
  </div>;
}

function SetupChecklist({ compact = false }: { compact?: boolean }) {
  const items = [["Install GitHub App", "Choose only the repositories Mitos should access.", Github], ["Verify webhook delivery", "Signatures and delivery IDs are checked server-side.", Webhook], ["Enable policies", "Schema, API, and security ship as the first policy pack.", ShieldCheck], ["Review before write", "Suggested changes require an authorised reviewer.", KeyRound]] as const;
  return <section className={`${compact ? "mt-7" : ""} card-dark p-6`}><p className="text-xs font-semibold uppercase tracking-[0.15em] text-ink-500">Connection checklist</p><div className="mt-5 flex flex-col gap-5">{items.map(([title, detail, Icon], index) => <div key={title} className="flex gap-4"><div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink-700 bg-ink-800 text-cyan-400"><Icon size={16} /></div><div><p className="text-sm font-medium text-ink-200">{index + 1}. {title}</p><p className="mt-1 text-xs leading-5 text-ink-500">{detail}</p></div></div>)}</div></section>;
}

export function ActivityView(props: DataProps) {
  const runs = useMemo(() => realRuns(props.thread), [props.thread]);
  return <div className="page-wrap"><PageHeading eyebrow="Accountability" title="Activity & audit" detail="A human-readable timeline grouped by pull request. The raw provenance graph remains available only as a technical trace." />
    <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_320px]"><section className="card-dark p-2">{runs.length === 0 ? <div className="p-8 text-center text-sm text-ink-500">No repository activity yet. Demo and unscoped entries are hidden.</div> : <ol>{runs.map((run) => <li key={run.id} className="relative flex gap-4 border-b border-ink-800 p-5 last:border-0"><div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-ink-700 bg-ink-800 text-cyan-400"><GitPullRequest size={15} /></div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-medium text-ink-100">PR #{run.pr} analysed</p><Status run={run} /></div><p className="mt-2 text-sm text-ink-400">{run.repository} · {run.title ?? "Untitled pull request"}</p><p className="mt-2 text-xs text-ink-600">{run.entries.length} recorded steps · {run.findings} findings · {relative(run.lastAt)}</p><details className="mt-4"><summary className="cursor-pointer text-xs font-medium text-cyan-400">Technical trace</summary><div className="mt-3 flex flex-col gap-2 border-l border-ink-700 pl-4">{run.entries.map((entry) => <div key={entry.entry_id} className="flex items-center gap-2 text-xs text-ink-500"><Code2 size={13} /><span className="font-mono">{entry.kind}</span><ChevronRight size={12} /><span className="truncate">{entry.actor}</span></div>)}</div></details></div></li>)}</ol>}</section><aside className="card-dark h-fit p-5"><LockKeyhole size={19} className="text-cyan-400" /><h2 className="mt-4 text-sm font-semibold text-ink-100">What is recorded</h2><ul className="mt-4 flex flex-col gap-3 text-xs leading-5 text-ink-500"><li>Webhook delivery and repository</li><li>Exact pull request and run identity</li><li>Policy decisions and evidence</li><li>Human approvals and write receipts</li></ul><p className="mt-5 border-t border-ink-800 pt-4 text-xs leading-5 text-ink-600">Raw demo, seed, and unrelated deferral records are intentionally excluded from this product view.</p></aside></div>
  </div>;
}

export function SettingsView(props: DataProps) {
  const identity = props.identity.status === "ok" ? props.identity.value : null;
  const session = props.session.status === "ok" ? props.session.value : null;
  const membership = session?.memberships[0];
  return <div className="page-wrap"><PageHeading eyebrow="Workspace administration" title="Settings" detail="Inspect team access, GitHub installation state, and the deployed runtime boundary." action={!session?.authenticated ? <a href="/github/auth/login" className="button-primary"><Github size={16} /> Sign in with GitHub</a> : undefined} />
    <div className="mt-8 grid gap-6 lg:grid-cols-[220px_1fr]"><nav className="flex flex-col gap-1">{["General", "Members & roles", "GitHub App", "Policies", "Audit retention"].map((item, index) => <button key={item} className={`rounded-lg px-3 py-2 text-left text-sm ${index === 0 ? "bg-ink-800 text-ink-100" : "text-ink-500 hover:bg-ink-900 hover:text-ink-300"}`}>{item}</button>)}</nav><section className="card-dark divide-y divide-ink-800"><div className="p-6"><h2 className="text-base font-semibold text-ink-100">Team access</h2>{session?.authenticated && session.user ? <div className="mt-5 flex flex-col gap-4 sm:flex-row sm:items-center"><img src={session.user.avatar_url} alt="" className="h-12 w-12 rounded-full border border-ink-700" /><div className="min-w-0 flex-1"><p className="text-sm font-medium text-ink-100">{session.user.name}</p><p className="mt-1 text-xs text-ink-500">@{session.user.login} · {membership?.workspace_id ?? "No installed workspace"}</p></div><span className={membership?.role === "owner" ? "status-success" : "status-warning"}>{membership?.role ?? "No role"}</span></div> : <p className="mt-4 text-sm leading-6 text-ink-500">Sign in through the installed GitHub App to resolve your real workspace membership and role.</p>}</div><div className="p-6"><h2 className="text-base font-semibold text-ink-100">Runtime boundary</h2><div className="mt-5 grid gap-3 sm:grid-cols-3">{[["Service role", identity?.role], ["GCP project", identity?.project], ["Build", identity?.build_sha]].map(([label, value]) => <div key={label} className="rounded-lg border border-ink-800 bg-ink-950 p-4"><p className="text-xs text-ink-600">{label}</p><p className="mt-2 truncate font-mono text-xs text-ink-300">{value ?? "Unavailable"}</p></div>)}</div></div><div className="flex items-center justify-between gap-4 p-6"><p className="text-xs text-ink-500">Policy mutations require an owner role and are enforced by the service.</p><button disabled={!membership || membership.role !== "owner"} className="button-secondary disabled:cursor-not-allowed disabled:opacity-50"><LockKeyhole size={16} /> {membership?.role === "owner" ? "Owner access" : "Read only"}</button></div></section></div>
  </div>;
}
