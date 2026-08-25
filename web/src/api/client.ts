import type {
  Beat,
  Catalog,
  Config,
  Identity,
  Loaded,
  Standards,
  Thread,
  Watch,
} from "./types";
import { RateLimited } from "./types";

// Every request is same-origin and relative. The deployed policy has no
// `connect-src`, which falls back to `default-src 'none'`, so an absolute URL
// to another host is refused by the browser before it is sent. In development
// the Vite proxy makes these same paths reach the live service.

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { accept: "application/json" } });
  if (!res.ok) {
    throw new HttpError(res.status, await safeDetail(res));
  }
  return (await res.json()) as T;
}

export class HttpError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`${status} ${detail}`);
    this.name = "HttpError";
    this.status = status;
    this.detail = detail;
  }
}

async function safeDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return body.detail;
    return res.statusText || String(res.status);
  } catch {
    return res.statusText || String(res.status);
  }
}

// Wraps a fetch so a 404 becomes "absent" rather than "error". The difference
// is shown to the user, because an endpoint this build does not serve is a
// different fact from an endpoint that failed.
export async function load<T>(fn: () => Promise<T>): Promise<Loaded<T>> {
  try {
    return { status: "ok", value: await fn() };
  } catch (err) {
    if (err instanceof HttpError && err.status === 404) {
      return { status: "absent", detail: err.detail };
    }
    return {
      status: "error",
      detail: err instanceof Error ? err.message : String(err),
    };
  }
}

export const getIdentity = () => getJson<Identity>("/identity");
export const getConfig = () => getJson<Config>("/config");
export const getCatalog = () => getJson<Catalog>("/catalog");
export const getWatch = () => getJson<Watch>("/watch");
export const getThread = (limit = 80) =>
  getJson<Thread>(`/thread?limit=${limit}`);

// The audit. Passing null audits the built-in demo corpus, which is local work
// and costs no GitHub request; passing a name reaches out over the public
// GitHub API and is rate limited by the service.
//
// Deliberately not wrapped in `load`. That helper flattens every non-404 into
// one error string, and this endpoint has three answers a reader needs told
// apart: 400 means the name is not owner/name, 429 means the shared budget for
// this demo is spent, and anything else is a failure. The status is the only
// thing that distinguishes them, so callers catch HttpError themselves.
export const getStandards = (repository: string | null) =>
  getJson<Standards>(
    repository === null
      ? "/standards.json"
      : `/standards.json?repository=${encodeURIComponent(repository)}`
  );

// Specified in openapi.yaml, absent from the deployed build at the time of
// writing. Callers must handle "absent" and show unknown.
export const getMetrics = () =>
  getJson<Record<string, unknown>>("/metrics.json");

export interface RunOptions {
  pr: number;
  approve: boolean;
  seed: boolean;
  signal: AbortSignal;
  onBeat: (beat: Beat) => void;
}

// POST returning server-sent events, so EventSource cannot be used: it only
// issues GET. This reads the body stream directly and splits on the SSE frame
// separator.
export async function runStream(opts: RunOptions): Promise<void> {
  const res = await fetch("/run/stream", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({
      pr: opts.pr,
      approve: opts.approve,
      seed: opts.seed,
    }),
    signal: opts.signal,
  });

  if (res.status === 429) {
    const header = res.headers.get("retry-after");
    const seconds = header === null ? null : Number.parseInt(header, 10);
    throw new RateLimited(
      seconds === null || Number.isNaN(seconds) ? null : seconds
    );
  }
  if (!res.ok) {
    throw new HttpError(res.status, await safeDetail(res));
  }
  if (!res.body) {
    throw new Error("the response carried no stream");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line. The server sends a `: mitos`
    // comment frame first so that no proxy can sit waiting for a first byte.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      emit(frame, opts.onBeat);
      split = buffer.indexOf("\n\n");
    }
  }
}

function emit(frame: string, onBeat: (beat: Beat) => void): void {
  for (const line of frame.split("\n")) {
    if (!line.startsWith("data:")) continue; // comment frames and padding
    const raw = line.slice(5).trim();
    if (!raw) continue;
    try {
      onBeat(JSON.parse(raw) as Beat);
    } catch {
      // A frame we cannot parse is reported as itself rather than discarded,
      // so a server change shows up instead of silently going missing.
      onBeat({ kind: "unparsed", text: raw });
    }
  }
}
