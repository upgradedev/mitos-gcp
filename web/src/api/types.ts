// Shapes transcribed from live responses and from openapi.yaml, not guessed.
// Anything the server may legitimately omit is optional here, so a missing
// field has to be handled at the point of use rather than defaulting to a
// number that was never measured.

export interface Identity {
  role: string;
  running_as: string;
  project: string;
  model: string;
  build_sha: string;
  may_call_write_tools: Record<string, boolean>;
  spec_repo_write_credential: {
    reachable: boolean;
    detail?: string;
    message?: string;
  };
  note?: string;
}

export interface Config {
  read_scope: string[];
  webhook_repositories: string[];
  max_reads_per_run: number;
  max_bytes_per_read: number;
}

export interface SessionUser {
  github_user_id: number;
  login: string;
  name: string;
  avatar_url: string;
}

export interface WorkspaceMembership {
  workspace_id: string;
  user_id: string;
  github_user_id: number;
  role: "owner" | "reviewer" | "viewer";
  installation_id: number;
}

export interface SessionStatus {
  authenticated: boolean;
  user: SessionUser | null;
  memberships: WorkspaceMembership[];
}

export interface GitHubAppStatus {
  configured: boolean;
  app_slug: string | null;
  install_url: string | null;
  create_url: string;
  webhook_endpoint: string;
  webhook_secret_configured: boolean;
  accepted_repositories: string[];
  events: string[];
  write_mode: "approval_required";
}

export interface Companion {
  name: string;
  department: string;
  role: string;
  wakes_on: string[];
  reads: string[];
  writes: string[];
}

export interface Catalog {
  companions: Companion[];
}

export interface ThreadEntry {
  kind: string;
  actor: string;
  subject: string;
  payload: Record<string, unknown>;
  parent_id: string | null;
  run_id: string;
  entry_id: string;
  recorded_at: string;
  digest: string;
}

export interface Thread {
  count: number;
  entries: ThreadEntry[];
}

export interface WorkspaceAnalytics {
  summary: {
    repositories: number;
    analysed_prs: number;
    findings: number;
    pending_approvals: number;
    published_suggestions: number;
  };
  trend: { date: string; analysed: number; attention: number; published: number }[];
  findings_by_severity: { severity: "critical" | "high" | "medium" | "low"; count: number }[];
  repositories: { repository: string; analyses: number; attention: number; last_activity: string | null; status: "healthy" | "attention" }[];
  recent_activity: { run_id: string; repository: string; pr: number | null; event: string; actor: string; recorded_at: string }[];
}

// GET /standards.json. Transcribed from live responses for the demo corpus and
// for a public repository, and from the vocabulary declared in
// src/mitos/standards.py.
//
// Seven verdicts, and only three of them settle a rule. The other four all mean
// the rule could not be determined, they are counted separately in the summary,
// and none of them is ever folded into the pass count. This client keeps them
// apart for the same reason the server does.
export interface StandardsFinding {
  rule: string;
  severity: string;
  // Deliberately a string rather than a union of the seven known verdicts. A
  // verdict this build has not seen is shown under its own name and treated as
  // undecided, which is what the rest of this app does with an unknown kind.
  verdict: string;
  looked_for: string;
  looked_at: string[];
  found: string;
  limitation: string;
}

// Every one of these is counted by the server. `could_not_be_determined` is its
// own field in the response and is read rather than recomputed here, because a
// client that adds up its own headline number is a second implementation that
// will eventually disagree with the first.
export interface StandardsSummary {
  rules: number;
  checked: number;
  passed: number;
  failed: number;
  suspected: number;
  not_applicable: number;
  undetermined: number;
  needs_judgement: number;
  not_checkable: number;
  could_not_be_determined: number;
}

// `summary` is only ever empty on the path that answers 400, and that path
// throws before a body is parsed, so a 200 always carries a counted summary.
export interface Standards {
  repository: string | null;
  summary: StandardsSummary;
  findings: StandardsFinding[];
  // What the server itself says about the limit it read under. Present only
  // when a repository was named; empty for the built-in demo corpus.
  note: string;
  agentic_pass: string;
}

export interface Watch {
  subscribed: boolean;
  mechanism: string;
  watching: string;
  wakeups: number;
  detail: { reason: string; matched: number; at: string }[];
}

// One line of the server-sent stream from POST /run/stream. `kind` is an open
// set: the server adds beats as the chore grows, and an unrecognised kind is
// rendered plainly rather than dropped.
export interface Beat {
  kind: string;
  text: string;
  // Present only on the final `done` beat.
  written?: boolean;
  published?: boolean;
  plan_hash?: string | null;
  parked_by?: string | null;
}

// What the UI is allowed to say about a run. Each of these corresponds to
// something the server actually reports; none of them is inferred optimism.
export type RunState =
  | "idle"
  | "connecting"
  | "running"
  | "card-produced"
  | "written"
  | "halted"
  | "failed"
  | "rate-limited";

export class RateLimited extends Error {
  retryAfterSeconds: number | null;
  constructor(retryAfterSeconds: number | null) {
    super("rate limited");
    this.name = "RateLimited";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

// Distinguishes "the server does not offer this" from "the server said zero".
// /metrics.json is specified in openapi.yaml but is absent from the deployed
// build, so this is not hypothetical.
export type Loaded<T> =
  | { status: "loading" }
  | { status: "ok"; value: T }
  | { status: "absent"; detail: string }
  | { status: "error"; detail: string };
