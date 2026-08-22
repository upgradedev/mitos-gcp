"""One image, three deployments, three identities.

The same code runs as the reader, the evaluator and the writer. What differs is
the service account Cloud Run starts it with and the `MITOS_ROLE` it boots into,
and neither is model-supplied, so nothing the fleet says can change which one it
is.

`/identity` is the endpoint that makes the architecture inspectable rather than
described: it reports the role, the service account Google says it is running as,
and whether it can actually reach the spec-repo write credential. On the reader
and the evaluator that last answer is a live 403 from Secret Manager, not a
config flag we set ourselves.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
from dataclasses import dataclass  # noqa: E402

import threading  # noqa: E402

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import (  # noqa: E402
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from pydantic import BaseModel  # noqa: E402

from mitos.chore import run_chore  # noqa: E402
from mitos.fixtures import PR_4471, PR_4472, SEEDED_HISTORY  # noqa: E402
from mitos.fleet import CATALOG  # noqa: E402
from mitos.guard import ROLE_READER, WRITE_TOOLS, is_allowed  # noqa: E402
from mitos.gemini import (  # noqa: E402
    build_agentic_analyst,
    build_classifier,
    build_critic,
    build_doc_agent,
)
from mitos.ledger import Entry, build_ledger  # noqa: E402
from mitos.chore import escalate_on_wake  # noqa: E402
from mitos.spec_repo import build_spec_repo  # noqa: E402
from mitos.watcher import build_watcher  # noqa: E402
from mitos import webhook as wh  # noqa: E402

from .thread_view import render as render_thread  # noqa: E402

ROLE = os.environ.get("MITOS_ROLE", ROLE_READER)
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "upgradegr-mitos")
SECRET = os.environ.get(
    "MITOS_WRITE_SECRET", "mitos-prod-settings-writer-spec-repo-deploy-key"
)  # nosec B105 - the secret's NAME, not its value
METADATA = "http://metadata.google.internal/computeMetadata/v1"

app = FastAPI(title=f"Mitos · {ROLE}")

# ORG_STANDARDS #7, request lifecycle observability. Instrumented as middleware,
# once, rather than per handler: a handler that has to remember to log is a
# handler that will forget. Structured JSON so Cloud Logging parses it into
# fields, which is also what makes "visible proof it runs on Google Cloud"
# something a judge can go and look at rather than take on trust.
@app.middleware("http")
async def request_lifecycle(request, call_next):
    started = time.monotonic()
    ctx = {
        "operationName": request.url.path,
        "method": request.method,
        "path": request.url.path,
        "role": ROLE,
        "project": PROJECT,
        "trace": request.headers.get("X-Cloud-Trace-Context", "").split("/")[0],
    }
    print(json.dumps({"event": "request.start", **ctx}), flush=True)
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        print(
            json.dumps(
                {
                    "event": "request.end",
                    **ctx,
                    "severity": "ERROR" if status >= 500 else "INFO",
                    "httpRequest": {"status": status},
                    "durationMs": round((time.monotonic() - started) * 1000, 1),
                }
            ),
            flush=True,
        )


# The control plane. Only the reader holds it: the writer must never act on an
# unattended wake, because waking is cheap and nobody is watching.
_WATCHER = None

# ORG_STANDARDS #8, connection reuse. Every request was building a fresh
# Firestore client, which opens a new gRPC channel and re-does discovery each
# time. Declaring a client per request is always wrong; it is built once per
# process and shared.
_LEDGER = None
_WEBHOOK_SECRET: Optional[str] = None


def ledger():
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = build_ledger()
    return _LEDGER


@app.on_event("startup")
def _open_the_subscription() -> None:
    """Hold a Firestore query subscription open for the life of the service.

    This is the thing the entry leads with, so it is worth being precise: no
    scheduler is started here and nothing is enqueued. The query is the trigger.

    Requires CPU to be allocated outside requests. Cloud Run throttles to zero
    between requests by default, which would suspend the subscription silently,
    so the service is deployed with --no-cpu-throttling. A listener that only
    runs while someone is calling you is a poller with extra steps.
    """
    global _WATCHER
    if ROLE != ROLE_READER:
        return
    try:
        led = ledger()
        _WATCHER = build_watcher(led, PROJECT)
        _WATCHER.start(lambda expired: escalate_on_wake(led, expired))
    except Exception as exc:  # pragma: no cover - reported, never swallowed
        app.state.watch_error = f"{type(exc).__name__}: {str(exc)[:200]}"


@app.get("/watch")
def watch() -> dict[str, Any]:
    """Proof the subscription is real, and countable.

    `wakeups` only increments when Firestore delivered a snapshot in which a
    deferral had expired. Nothing in this codebase calls the fleet to produce
    one.
    """
    err = getattr(app.state, "watch_error", None)
    if _WATCHER is None:
        return {
            "subscribed": False,
            "reason": err or f"the {ROLE} service does not hold the subscription",
        }
    wakeups = _WATCHER.wakeups
    return {
        "subscribed": True,
        "mechanism": "firestore query subscription (on_snapshot), no scheduler, no queue",
        "watching": "kind == finding.deferred, escalated once its expiry passes",
        "wakeups": len(wakeups),
        "detail": [
            {"reason": w.reason, "matched": w.matched, "at": w.at} for w in wakeups
        ],
    }


def _running_as() -> Optional[str]:
    """Ask the metadata server who we are. Not a value we chose."""
    try:
        r = httpx.get(
            f"{METADATA}/instance/service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"},
            timeout=2.0,
        )
        return r.text if r.status_code == 200 else None
    except Exception:  # pragma: no cover - only outside GCP
        return None


def _can_reach_write_credential() -> dict[str, Any]:
    """Actually try. A claim about IAM that is not exercised is a claim about a
    config file."""
    try:
        from google.cloud import secretmanager  # noqa: PLC0415

        client = secretmanager.SecretManagerServiceClient()
        client.access_secret_version(
            name=f"projects/{PROJECT}/secrets/{SECRET}/versions/latest"
        )
        return {"reachable": True, "detail": "secret accessed"}
    except Exception as exc:
        return {
            "reachable": False,
            "detail": type(exc).__name__,
            "message": str(exc)[:200],
        }


@app.get("/identity")
def identity() -> dict[str, Any]:
    write_checks = {
        tool: is_allowed(tool, ROLE)[0] for tool in sorted(WRITE_TOOLS)
    }
    return {
        "role": ROLE,
        "running_as": _running_as(),
        "project": PROJECT,
        "may_call_write_tools": write_checks,
        "spec_repo_write_credential": _can_reach_write_credential(),
        "model": os.environ.get("MITOS_MODEL", "stub"),
        "note": (
            "may_call_write_tools is enforced in ADK's before_tool_callback. "
            "spec_repo_write_credential is enforced by Google IAM, outside this "
            "process. The second one is the load-bearing control: this service "
            "cannot grant itself the credential no matter what it decides."
        ),
    }


@app.get("/catalog")
def catalog() -> dict[str, Any]:
    return {"companions": [c.as_dict() for c in CATALOG]}


@app.get("/thread")
def thread(limit: int = 100) -> dict[str, Any]:
    entries = ledger().all()[-limit:]
    return {
        "count": len(entries),
        "entries": [e.to_doc() for e in entries],
    }


def _publisher():
    """Only the writer service holds a credential that can publish.

    The reader orchestrates the chore and then has to ask, because IAM will not
    give it the deploy key. `MITOS_WRITER_URL` is that request; without it the
    reader falls back to recording the plan and publishing nothing, which is
    what the offline demo does.
    """
    if ROLE == "writer":
        return build_spec_repo(PROJECT)
    url = os.environ.get("MITOS_WRITER_URL")
    return _RemoteWriter(url) if url else None


@dataclass
class _RemoteWriter:
    """Delegates the write to the writer service over an authenticated call.

    This is the privilege boundary made concrete: the reader cannot perform the
    write, so it asks the one identity that can, and the writer re-checks the
    plan hash itself rather than trusting the caller.
    """

    url: str

    def publish(self, *, path: str, body: str, message: str, branch: str) -> dict:
        import google.auth.transport.requests  # noqa: PLC0415
        import google.oauth2.id_token  # noqa: PLC0415

        try:
            token = google.oauth2.id_token.fetch_id_token(
                google.auth.transport.requests.Request(), self.url
            )
            r = httpx.post(
                f"{self.url}/execute",
                json={"path": path, "body": body, "message": message, "branch": branch},
                headers={"Authorization": f"Bearer {token}"},
                timeout=120.0,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {
                "published": False,
                "reason": f"the writer service refused or was unreachable: "
                f"{type(exc).__name__}",
            }


class ExecuteRequest(BaseModel):
    path: str
    body: str
    message: str
    branch: str


@app.post("/execute")
def execute(req: ExecuteRequest) -> dict:
    """Perform the governed write. Writer service only.

    The role check here is not decoration: this endpoint exists on all three
    deployments because they share one image, and on two of them it refuses.
    Even if it did not, those two cannot read the deploy key.
    """
    if ROLE != "writer":
        raise HTTPException(
            status_code=403,
            detail=f"the {ROLE} service holds no credential that can write",
        )
    return build_spec_repo(PROJECT).publish(
        path=req.path, body=req.body, message=req.message, branch=req.branch
    )


class RunRequest(BaseModel):
    pr: int = 4471
    approve: bool = False
    seed: bool = False


ALLOWED_REPOS = frozenset(
    r.strip()
    for r in os.environ.get("MITOS_WEBHOOK_REPOS", "upgradedev/mitos-spec").split(",")
    if r.strip()
)


# Not a secret. The deliberate absence of one, which is a distinct state worth
# naming: while this is the value every delivery is refused with 503. Bandit
# flags a bare "" here as a hardcoded password, which is a fair rule and a wrong
# reading, so the intent is in the name rather than in a suppression nobody
# reads.
NO_SECRET_CONFIGURED = ""  # nosec B105 - the absence of a secret, not a secret


def _webhook_secret() -> str:
    """Read once, cached on the module, like every other client here."""
    global _WEBHOOK_SECRET
    if _WEBHOOK_SECRET is None:
        try:
            from google.cloud import secretmanager  # noqa: PLC0415

            client = secretmanager.SecretManagerServiceClient()
            name = (
                f"projects/{PROJECT}/secrets/"
                f"mitos-prod-settings-reader-github-webhook-secret/versions/latest"
            )
            _WEBHOOK_SECRET = client.access_secret_version(
                name=name
            ).payload.data.decode("utf-8").strip()
        except Exception:
            # Empty means every delivery is refused with 503, which is the only
            # safe behaviour: an endpoint that accepts everything when
            # misconfigured fails invisibly.
            _WEBHOOK_SECRET = NO_SECRET_CONFIGURED
    return _WEBHOOK_SECRET


def _fetch_diff(repository: str, number: int) -> list[dict]:
    """The files in the pull request, from the public GitHub API.

    No credential: the specification repository is public, and a read path that
    needs a token is a read path that can be used to write.
    """
    r = httpx.get(
        f"https://api.github.com/repos/{repository}/pulls/{number}/files",
        headers={"Accept": "application/vnd.github+json"},
        timeout=30.0,
    )
    r.raise_for_status()
    return [
        {"path": f["filename"], "patch": f.get("patch", "")}
        for f in r.json()
        if f.get("patch")
    ]


@app.post("/webhook/github")
async def github_webhook(request: Request) -> JSONResponse:
    """The trigger. Nobody opens Mitos; this does.

    Answers immediately and works afterwards. GitHub gives a webhook ten seconds
    and disables one that keeps timing out, and this chore takes minutes with a
    model reading the repository, so doing the work inside the request would
    guarantee the trigger stops firing.
    """
    body = await request.body()
    try:
        delivery = wh.parse(
            body,
            dict(request.headers),
            secret=_webhook_secret(),
            allowed_repositories=ALLOWED_REPOS,
        )
    except wh.Rejected as rej:
        # 202 for "understood, not acting" so GitHub does not disable the hook
        # over deliveries we simply do not care about.
        return JSONResponse({"accepted": False, "reason": str(rej)}, status_code=rej.status)

    led = ledger()
    entry = led.append(
        Entry(
            kind="trigger.webhook",
            actor="github",
            subject=f"{delivery.repository}#{delivery.number}",
            payload=delivery.as_dict(),
            run_id=delivery.delivery_id,
        )
    )

    def work() -> None:
        try:
            files = _fetch_diff(delivery.repository, delivery.number)
            if not files:
                led.append(
                    Entry(
                        kind="trigger.ignored", actor="github",
                        subject=entry.subject, parent_id=entry.entry_id,
                        payload={"reason": "no readable patch in this pull request"},
                        run_id=delivery.delivery_id,
                    )
                )
                return
            run_chore(
                wh.to_pull_request(delivery, files), led,
                run_id=delivery.delivery_id,
                approve=lambda card: False,  # a webhook never approves a write
                analyst=build_agentic_analyst(PROJECT, role=ROLE),
                critic=build_critic(PROJECT),
                classifier=build_classifier(PROJECT),
                doc_agent=build_doc_agent(PROJECT, role=ROLE),
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            led.append(
                Entry(
                    kind="trigger.failed", actor="github", subject=entry.subject,
                    parent_id=entry.entry_id, run_id=delivery.delivery_id,
                    payload={"error": f"{type(exc).__name__}: {str(exc)[:300]}"},
                )
            )

    threading.Thread(target=work, daemon=True).start()

    return JSONResponse(
        {
            "accepted": True,
            "delivery": delivery.delivery_id,
            "pr": delivery.number,
            "thread": entry.entry_id,
            "note": (
                "the fleet is working. A webhook never approves a write: it "
                "produces a plan and stops at the approval."
            ),
        },
        status_code=202,
    )


@app.post("/run/stream")
def run_stream(req: RunRequest) -> StreamingResponse:
    """The chore, streamed as it happens.

    `/run` returns when the whole thing is finished, and with a model in the
    loop that is minutes rather than seconds: the specialists read the
    repository, and each read is a round trip. A judge who posts to it waits,
    sees nothing, and concludes it is broken. The UAT caught exactly that.

    So this streams. Every beat is flushed the moment it occurs, which is both
    the honest fix for the timeout and a better thing to watch: the reads appear
    one at a time, in the order the agent chose them.
    """
    if ROLE != ROLE_READER:
        raise HTTPException(status_code=403, detail=f"the {ROLE} service does not orchestrate chores")
    pr = {4471: PR_4471, 4472: PR_4472}.get(req.pr)
    if pr is None:
        raise HTTPException(status_code=404, detail=f"no fixture for PR {req.pr}")

    def beats():
        import queue as _queue  # noqa: PLC0415
        import threading as _threading  # noqa: PLC0415

        q: "_queue.Queue[Optional[dict]]" = _queue.Queue()

        def work():
            led = ledger()
            if req.seed:
                for item in SEEDED_HISTORY:
                    led.append(
                        Entry(
                            kind=item["kind"], actor=item["actor"],
                            subject=item["subject"], payload=item["payload"],
                            run_id="seed",
                        )
                    )
            try:
                result = run_chore(
                    pr, led, run_id=uuid.uuid4().hex[:8],
                    emit=lambda kind, text: q.put({"kind": kind, "text": text}),
                    approve=(lambda card: req.approve),
                    analyst=build_agentic_analyst(PROJECT, role=ROLE),
                    critic=build_critic(PROJECT),
                    classifier=build_classifier(PROJECT),
                    doc_agent=build_doc_agent(PROJECT, role=ROLE),
                    publisher=_publisher(),
                )
                q.put({
                    "kind": "done",
                    "text": "",
                    "written": result.written,
                    "published": result.published,
                    "plan_hash": result.card.plan_hash if result.card else None,
                    "parked_by": result.parked_by,
                })
            except Exception as exc:  # noqa: BLE001 - reported to the client
                q.put({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
            finally:
                q.put(None)

        _threading.Thread(target=work, daemon=True).start()
        # A comment frame immediately, so a proxy cannot sit on the response
        # waiting for the first byte and reintroduce the very problem this
        # endpoint exists to solve.
        yield ": mitos\n\n"
        while True:
            beat = q.get()
            if beat is None:
                return
            yield f"data: {json.dumps(beat)}\n\n"

    return StreamingResponse(
        beats(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/run")
def run(req: RunRequest) -> JSONResponse:
    if ROLE not in (ROLE_READER,):
        raise HTTPException(
            status_code=403,
            detail=f"the {ROLE} service does not orchestrate chores",
        )
    pr = {4471: PR_4471, 4472: PR_4472}.get(req.pr)
    if pr is None:
        raise HTTPException(status_code=404, detail=f"no fixture for PR {req.pr}")

    led = ledger()
    if req.seed:
        for item in SEEDED_HISTORY:
            led.append(
                Entry(
                    kind=item["kind"],
                    actor=item["actor"],
                    subject=item["subject"],
                    payload=item["payload"],
                    run_id="seed",
                )
            )

    transcript: list[dict[str, str]] = []
    result = run_chore(
        pr,
        led,
        run_id=uuid.uuid4().hex[:8],
        emit=lambda kind, text: transcript.append({"kind": kind, "text": text}),
        approve=(lambda card: req.approve),
        # The agentic specialist reads the repository itself and may refuse on
        # what it finds. The classifier can widen the dispatch and never narrow
        # it. The doc agent exercises the interceptor in the product path.
        analyst=build_agentic_analyst(PROJECT, role=ROLE),
        critic=build_critic(PROJECT),
        classifier=build_classifier(PROJECT),
        doc_agent=build_doc_agent(PROJECT, role=ROLE),
        publisher=_publisher(),
    )
    return JSONResponse(
        {
            "run_id": result.run_id,
            "pr": result.pr_number,
            "dispatch": result.dispatch.as_dict(),
            "recalled": len(result.recalled),
            "escalated": result.escalated,
            "first_verdict": result.first_verdict.as_dict(),
            "final_verdict": (
                result.final_verdict.as_dict() if result.final_verdict else None
            ),
            "plan_hash": result.card.plan_hash if result.card else None,
            "written": result.written,
            "published": result.published,
            "receipt": result.receipt,
            "transcript": transcript,
        }
    )


@app.get("/thread/view", response_class=HTMLResponse)
def thread_view(limit: int = 300) -> str:
    """The thread as the graph it is.

    The product is named for a thread you can follow back, and rendering it as
    a list asks the reader to do the walking in their head. Here a click lights
    the whole path from an outcome to the pull request that caused it.
    """
    entries = [e.to_doc() for e in ledger().all()[-limit:]]
    wakeups = len(_WATCHER.wakeups) if _WATCHER is not None else 0
    return render_thread(entries, ROLE, wakeups)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    ident = identity()
    reachable = ident["spec_repo_write_credential"]["reachable"]
    colour = "#b3261e" if reachable and ROLE != "writer" else "#146c2e"
    return f"""<!doctype html><meta charset=utf-8>
<title>Mitos · {ROLE}</title>
<style>
 body{{font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;max-width:52rem;
 margin:3rem auto;padding:0 1.25rem;color:#1b1b1f;background:#fff}}
 @media(prefers-color-scheme:dark){{body{{background:#131316;color:#e5e2e6}}}}
 h1{{font-size:1.35rem;margin:0 0 .25rem}} .r{{color:#666}}
 table{{border-collapse:collapse;width:100%;margin:1.25rem 0}}
 td,th{{text-align:left;padding:.4rem .6rem;border-bottom:1px solid #8883}}
 code{{background:#8881;padding:.1rem .3rem;border-radius:3px}}
 .v{{color:{colour};font-weight:600}}
</style>
<h1>Mitos · <span class=r>{ROLE}</span></h1>
<p class=r>One of three services. Same image, different identity.</p>
<table>
<tr><th>running as</th><td><code>{ident["running_as"]}</code></td></tr>
<tr><th>may call <code>write_spec_repo</code></th>
    <td class=v>{ident["may_call_write_tools"].get("write_spec_repo")}</td></tr>
<tr><th>can reach the spec-repo write credential</th>
    <td class=v>{reachable}</td></tr>
</table>
<p>Endpoints: <code>/identity</code> · <code>/catalog</code> · <code>/thread</code>
 · <code>POST /run</code></p>
<p class=r>The first row is enforced inside ADK's tool interceptor. The second is
enforced by Google IAM, outside this process, which is why this service cannot
grant it to itself.</p>
"""
