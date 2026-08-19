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

import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
from dataclasses import dataclass  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from mitos.chore import run_chore  # noqa: E402
from mitos.fixtures import PR_4471, PR_4472, SEEDED_HISTORY  # noqa: E402
from mitos.fleet import CATALOG  # noqa: E402
from mitos.guard import ROLE_READER, WRITE_TOOLS, is_allowed  # noqa: E402
from mitos.gemini import build_analyst, build_critic  # noqa: E402
from mitos.ledger import Entry, build_ledger  # noqa: E402
from mitos.spec_repo import build_spec_repo  # noqa: E402

ROLE = os.environ.get("MITOS_ROLE", ROLE_READER)
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "mitos-fleet")
SECRET = "spec-repo-write-token"  # nosec B105 - the secret's NAME, not its value
METADATA = "http://metadata.google.internal/computeMetadata/v1"

app = FastAPI(title=f"Mitos · {ROLE}")


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
    ledger = build_ledger()
    entries = ledger.all()[-limit:]
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

    ledger = build_ledger()
    if req.seed:
        for item in SEEDED_HISTORY:
            ledger.append(
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
        ledger,
        run_id=uuid.uuid4().hex[:8],
        emit=lambda kind, text: transcript.append({"kind": kind, "text": text}),
        approve=(lambda card: req.approve),
        analyst=build_analyst(PROJECT),
        critic=build_critic(PROJECT),
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
