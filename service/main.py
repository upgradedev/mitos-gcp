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

import base64
import html
import json
import os
import secrets
import sys
import time
import urllib.parse
import uuid
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
import jwt  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import threading  # noqa: E402

from fastapi import FastAPI, HTTPException, Request, Response  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from mitos.approval import (  # noqa: E402
    Approval,
    Expired,
    Mismatch,
    Replayed,
    body_digest,
    build_approval_store,
    verify_and_consume,
)
from mitos.chore import run_chore  # noqa: E402
from mitos.once import AlreadySeen, build_claims  # noqa: E402
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

from mitos.tools import MAX_BYTES_PER_READ, MAX_READS_PER_RUN  # noqa: E402

from mitos.standards import AUDIT_SCOPE, check_repository  # noqa: E402
from mitos.tools import build_corpus  # noqa: E402

from .budget import RateLimiter, client_of  # noqa: E402
from .metrics import summarise  # noqa: E402

from .manifest import problems as manifest_problems  # noqa: E402
from .dashboard import (  # noqa: E402
    audit_form,
    public_base,
    render_connect,
    render_standards,
)

ROLE = os.environ.get("MITOS_ROLE", ROLE_READER)
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "upgradegr-mitos")
DEMO_MODE = os.environ.get("MITOS_DEMO_MODE", "").lower() in {"1", "true", "yes"}


def _require_demo_mode() -> None:
    if not DEMO_MODE:
        raise HTTPException(status_code=404, detail="Demo route is disabled")
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
# Set on every response, including the JSON ones. A scanner reports their
# absence and it would be right to: this service renders HTML that contains a
# pull request title, and a stored cross-site scripting hole in exactly that
# path was found and fixed here on 2026-08-23. These headers are the defence in
# depth that should have been sitting behind that fix.
#
# The content policy is deliberately narrow rather than aspirational. Every page
# this service serves is self contained: no CDN, no external font, no image, no
# fetch. So everything that could reach out is denied outright, and what remains
# is `unsafe-inline` for the one inline script and the inline styles the pages
# are built from.
#
# `unsafe-inline` was the weak part and it is gone. The first dynamic scan
# reported it at MEDIUM twice, which is the same thing this comment already
# said, arrived at independently by somebody else's tool.
#
# A per-request nonce replaces it. The middleware mints one before the handler
# runs, so the same value reaches the tag and the header; a nonce that does not
# match the tag it authorises is worse than none, because the browser blocks the
# script and the page silently stops working, which is how a policy gets
# loosened again.
#
# Three directives were added when the interface became a built application
# served from this origin, and each one names a thing that is now refused
# without it:
#
#   script-src 'self'    the bundle in /assets. A file, not an inline tag, so
#                        the nonce does not cover it.
#   style-src 'self'     the stylesheet in /assets, same reason.
#   connect-src 'self'   every fetch the app makes. There was no connect-src,
#                        so it fell back to `default-src 'none'`, and 'none'
#                        means no sources at all rather than no foreign ones:
#                        the browser refuses a same-origin /identity before it
#                        is sent. The app cannot read its own service without
#                        this and would show only failure panels.
#
# What is deliberately still absent: img-src, font-src, media-src and
# frame-src. The build loads no image, no font, no media and no frame, so
# they stay at `default-src 'none'` and anything that tries becomes visible
# rather than silently allowed. The nonce stays for /standards and /connect,
# which are still rendered here with inline style.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'nonce-{nonce}'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    # Found by the first dynamic scan, which reported "Insufficient Site
    # Isolation Against Spectre" on nine responses. Nothing this service
    # serves is meant to be embedded in, or opened by, another origin, so
    # the strictest values are also the correct ones.
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
    # Cloud Run terminates TLS and serves this origin over HTTPS only, so
    # declaring it costs nothing and closes the downgrade window for a reader
    # who typed the host without a scheme.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@app.middleware("http")
async def security_headers(request, call_next):
    # Generated before the handler runs, so the same value reaches the page and
    # the header. A nonce that does not match the tag it authorises is worse
    # than no nonce: the browser blocks the script and the page silently stops
    # working, which is the failure mode that gets a policy loosened again.
    #
    # `token_urlsafe` rather than `random`: this is the one value in the policy
    # an attacker would want to guess, and guessing it turns the whole thing
    # back into `unsafe-inline`.
    request.state.csp_nonce = secrets.token_urlsafe(16)
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(
            name, value.replace("{nonce}", request.state.csp_nonce)
        )
    return response


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
        # Which source this process is. Reported rather than inferred from
        # the image tag, because a tag is a label and this is a fact.
        "build_sha": BUILD_SHA,
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


class NoPublicUrl(Exception):
    """This service cannot say what its own public address is.

    Raised rather than returning something plausible. Every caller of
    `_public_url` is building a URL that GitHub will store and call back, and a
    wrong one fails later, somewhere else, with a message about GitHub rather
    than about us.
    """


def _public_url(request: Request) -> str:
    """The absolute https origin a caller outside Cloud Run would use.

    Three things went wrong in the one line this replaces, and the first two are
    the same mistake made twice.

    `request.base_url` reports the scheme of the connection this process saw,
    and behind Cloud Run's proxy that is http. Every URL in the GitHub App
    manifest came out `http://`, GitHub refused the whole manifest with
    "redirect_url must be a valid URL", and `hook_attributes.url` would have
    registered a plain text endpoint for an HMAC-signed delivery. `/connect` had
    exactly this bug and `dashboard.public_base` was written and tested to fix
    it; this helper did not use it.

    `os.environ.get(name, default)` returns the empty string when the variable
    is SET AND EMPTY, so a blank `MITOS_PUBLIC_URL` produced `""` and every
    manifest URL became a relative path. `or` rather than a default.

    And nothing checked the result. It does now: a caller gets an absolute https
    URL or an exception, never a string that looks like one.
    """
    configured = (os.environ.get("MITOS_PUBLIC_URL") or "").strip().rstrip("/")
    if not configured:
        configured = public_base(
            str(request.base_url), request.headers.get("x-forwarded-proto", "")
        ).rstrip("/")

    parsed = urlparse(configured)
    if parsed.scheme != "https" or not parsed.netloc:
        raise NoPublicUrl(
            f"{configured!r} is not an absolute https URL. Set MITOS_PUBLIC_URL "
            f"to this deployment's own address, or run behind a proxy that "
            f"sets X-Forwarded-Proto."
        )
    return configured


def _github_app_metadata() -> dict[str, Any]:
    try:
        from google.cloud import firestore  # noqa: PLC0415

        snapshot = firestore.Client(project=PROJECT).collection("system").document("github_app").get()
        return snapshot.to_dict() if snapshot.exists else {}
    except Exception:  # noqa: BLE001 - status must remain useful before Firestore setup
        return {}


def _connected_repositories() -> list[str]:
    try:
        from google.cloud import firestore  # noqa: PLC0415

        docs = firestore.Client(project=PROJECT).collection("repositories").where(
            filter=firestore.FieldFilter("active", "==", True)
        ).stream()
        return sorted({str(doc.to_dict().get("full_name", "")) for doc in docs if doc.to_dict().get("full_name")})
    except Exception:  # noqa: BLE001 - legacy allowlist remains readable during migration
        return sorted(ALLOWED_REPOS)


def _persist_installation_event(payload: dict[str, Any], delivery_id: str) -> dict[str, Any]:
    """Project a verified GitHub installation event into tenant-scoped records."""
    from google.cloud import firestore  # noqa: PLC0415

    installation = payload.get("installation") or {}
    account = installation.get("account") or {}
    installation_id = installation.get("id")
    account_id = account.get("id")
    if not isinstance(installation_id, int) or not isinstance(account_id, int):
        raise wh.Rejected("installation or account identity is missing", 400)
    action = str(payload.get("action", ""))
    workspace_id = f"github-{account_id}"
    active = action not in {"deleted", "suspend"}
    db = firestore.Client(project=PROJECT)
    db.collection("workspaces").document(workspace_id).set(
        {
            "name": str(account.get("login") or "GitHub workspace")[:100],
            "github_account_id": account_id,
            "github_account_type": str(account.get("type") or "Organization"),
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    db.collection("github_installations").document(str(installation_id)).set(
        {
            "workspace_id": workspace_id,
            "installation_id": installation_id,
            "account_id": account_id,
            "account_login": str(account.get("login") or "")[:100],
            "target_type": str(installation.get("target_type") or ""),
            "permissions": installation.get("permissions") or {},
            "events": installation.get("events") or [],
            "active": active,
            "suspended_at": installation.get("suspended_at"),
            "updated_at": firestore.SERVER_TIMESTAMP,
            "last_delivery_id": delivery_id,
        },
        merge=True,
    )
    repositories = payload.get("repositories") or payload.get("repositories_added") or []
    removed = payload.get("repositories_removed") or []
    for repository in repositories:
        repository_id = repository.get("id")
        full_name = str(repository.get("full_name") or "")
        if isinstance(repository_id, int) and full_name:
            db.collection("repositories").document(str(repository_id)).set(
                {
                    "workspace_id": workspace_id,
                    "installation_id": installation_id,
                    "github_repository_id": repository_id,
                    "full_name": full_name[:200],
                    "private": bool(repository.get("private", False)),
                    "default_branch": str(repository.get("default_branch") or "main")[:100],
                    "active": active,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
    for repository in removed:
        repository_id = repository.get("id")
        if isinstance(repository_id, int):
            db.collection("repositories").document(str(repository_id)).set(
                {"active": False, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True
            )
    return {"workspace_id": workspace_id, "installation_id": installation_id, "active": active}


def _store_github_app_secret(secret_id: str, value: str) -> None:
    """Create or rotate one GitHub App credential without persisting it in Firestore."""
    from google.api_core.exceptions import (  # noqa: PLC0415
        AlreadyExists,
        PermissionDenied,
    )
    from google.cloud import secretmanager  # noqa: PLC0415

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT}"
    # The secret is created by Terraform, empty, and this only ever adds a
    # version to it. Creating it here needed `secretmanager.admin` at the
    # project level, which also grants read on every other secret, including
    # the deploy key this service is architecturally forbidden to hold. That
    # was deployed and the reader really could read it.
    #
    # `AlreadyExists` is still caught rather than removed: a deployment whose
    # apply has not run yet should fail on the add below, with a message about
    # a missing secret, rather than here with a permission error that reads as
    # a bug in this function.
    try:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )
    except (AlreadyExists, PermissionDenied):
        # Both are expected and neither is a problem. The secret exists because
        # Terraform made it, and this identity deliberately cannot create
        # secrets: holding that permission project-wide is what let the reader
        # read the write credential.
        #
        # Named rather than caught broadly. `except Exception: pass` here would
        # swallow a quota error or a wrong project id and leave the add below
        # failing for a reason nobody could see, which is the same shape as the
        # bug this function is recovering from.
        pass
    client.add_secret_version(
        request={
            "parent": f"{parent}/secrets/{secret_id}",
            "payload": {"data": value.encode("utf-8")},
        }
    )


def _read_managed_secret(secret_id: str) -> str:
    from google.cloud import secretmanager  # noqa: PLC0415

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT}/secrets/{secret_id}/versions/latest"
    return client.access_secret_version(name=name).payload.data.decode("utf-8").strip()


def _github_installation_token(installation_id: int) -> str:
    metadata = _github_app_metadata()
    secret_prefix = str(metadata.get("secret_prefix") or "")
    app_id = metadata.get("app_id")
    if not secret_prefix or not app_id:
        raise RuntimeError("GitHub App credentials are unavailable")
    private_key = _read_managed_secret(f"{secret_prefix}-private-key")
    now = int(time.time())
    app_token = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, private_key, algorithm="RS256")
    response = httpx.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    token = str(response.json().get("token") or "")
    if not token:
        raise RuntimeError("GitHub did not return an installation token")
    return token


def _github_check(
    *, repository: str, installation_id: int, head_sha: str, status: str,
    check_run_id: Optional[int] = None, conclusion: Optional[str] = None,
    summary: str = "Mitos is analysing this pull request.",
) -> Optional[int]:
    """Create or update the Mitos Check without affecting webhook acceptance."""
    if not head_sha:
        return check_run_id
    token = _github_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload: dict[str, Any] = {
        "name": "Mitos change governance",
        "status": status,
        "output": {"title": "Mitos change governance", "summary": summary[:65000]},
    }
    if conclusion:
        payload["conclusion"] = conclusion
    if check_run_id is None:
        payload["head_sha"] = head_sha
        response = httpx.post(
            f"https://api.github.com/repos/{repository}/check-runs",
            headers=headers, json=payload, timeout=30.0,
        )
    else:
        response = httpx.patch(
            f"https://api.github.com/repos/{repository}/check-runs/{check_run_id}",
            headers=headers, json=payload, timeout=30.0,
        )
    response.raise_for_status()
    value = response.json().get("id")
    return int(value) if isinstance(value, int) else check_run_id


def _safe_github_check(**kwargs: Any) -> Optional[int]:
    try:
        return _github_check(**kwargs)
    except Exception as exc:  # noqa: BLE001 - GitHub Checks are reporting, not orchestration
        print(json.dumps({"event": "github.check_failed", "error": type(exc).__name__}), flush=True)
        return kwargs.get("check_run_id")


def _github_suggested_pr(*, installation_id: int, repository: str, source_pr: int,
                         expected_head: str, path: str, body: str, run_id: str) -> dict[str, Any]:
    token = _github_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api = f"https://api.github.com/repos/{repository}"
    source = httpx.get(f"{api}/pulls/{source_pr}", headers=headers, timeout=30.0)
    source.raise_for_status()
    source_doc = source.json()
    current_head = str((source_doc.get("head") or {}).get("sha") or "")
    if not current_head or current_head != expected_head:
        raise HTTPException(status_code=409, detail="The pull request head changed; run analysis again before approval")

    repository_response = httpx.get(api, headers=headers, timeout=30.0)
    repository_response.raise_for_status()
    base_branch = str(repository_response.json().get("default_branch") or "main")
    base_response = httpx.get(f"{api}/git/ref/heads/{urllib.parse.quote(base_branch, safe='')}", headers=headers, timeout=30.0)
    base_response.raise_for_status()
    base_sha = str((base_response.json().get("object") or {}).get("sha") or "")
    branch = f"mitos/suggestion-{source_pr}-{run_id[:8]}"
    create_ref = httpx.post(
        f"{api}/git/refs", headers=headers,
        json={"ref": f"refs/heads/{branch}", "sha": base_sha}, timeout=30.0,
    )
    if create_ref.status_code not in (201, 422):
        create_ref.raise_for_status()
    # GitHub's contents API requires the blob sha of the file being replaced:
    # "Required if you are updating a file." Without it, a PUT over a file that
    # already exists is refused with 422.
    #
    # That is not the edge case here, it is the main one. What this publishes is
    # a repaired document, so the path almost always exists already, and the
    # only reason nobody hit it is that no GitHub App has ever been installed.
    # Creating a new file still works without a sha, which is why the omission
    # looked correct.
    quoted = urllib.parse.quote(path, safe="/")
    existing = httpx.get(
        f"{api}/contents/{quoted}", headers=headers,
        params={"ref": branch}, timeout=30.0,
    )
    payload: dict[str, Any] = {
        "message": f"docs: apply Mitos suggestion for #{source_pr}",
        "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing.status_code == 200:
        document = existing.json()
        # A directory answers with a list. Writing a file over a directory path
        # cannot succeed, and failing here names the path rather than letting
        # GitHub refuse a payload with a missing sha.
        if not isinstance(document, dict):
            raise HTTPException(
                status_code=409,
                detail=f"{path} is a directory in this repository, not a file",
            )
        payload["sha"] = str(document.get("sha") or "")
    elif existing.status_code != 404:
        existing.raise_for_status()

    content = httpx.put(
        f"{api}/contents/{quoted}", headers=headers, json=payload, timeout=30.0,
    )
    content.raise_for_status()
    pull = httpx.post(
        f"{api}/pulls", headers=headers,
        json={
            "title": f"Mitos suggestion for #{source_pr}", "head": branch, "base": base_branch,
            "body": f"Approval-gated suggestion generated from Mitos analysis `{run_id}` for #{source_pr}.\n\nSource head: `{expected_head}`",
        }, timeout=30.0,
    )
    pull.raise_for_status()
    document = pull.json()
    return {"published": True, "branch": branch, "url": document.get("html_url"), "pull_number": document.get("number"), "commit": (content.json().get("commit") or {}).get("sha")}


def _run_webhook_chore(*, wh: Any, delivery: Any, files: Any, led: Any) -> Any:
    return run_chore(
        wh.to_pull_request(delivery, files), led,
        run_id=delivery.delivery_id, repository=delivery.repository,
        approve=lambda card: False,
        analyst=build_agentic_analyst(
            PROJECT, role=ROLE, repository=delivery.repository,
            ref=delivery.head_sha or "HEAD", scope=READ_SCOPE,
        ),
        critic=build_critic(PROJECT), classifier=build_classifier(PROJECT),
        doc_agent=build_doc_agent(PROJECT, role=ROLE),
    )


def _complete_analysis_check(*, led: Any, delivery: Any, installation_id: Optional[int], check_run_id: Optional[int]) -> None:
    if not isinstance(installation_id, int) or check_run_id is None:
        return
    entries = [item for item in led.all() if item.run_id == delivery.delivery_id]
    findings = sum(item.kind.startswith("finding.") for item in entries)
    needs_review = findings > 0 or any(
        item.kind == "evaluator.verdict" and item.payload.get("passed") is False
        for item in entries
    )
    plans = sum(item.kind == "plan.proposed" for item in entries)
    _safe_github_check(
        repository=delivery.repository, installation_id=installation_id,
        head_sha=delivery.head_sha, status="completed", check_run_id=check_run_id,
        conclusion="action_required" if needs_review else "success",
        summary=f"Analysis completed with {findings} finding(s) and {plans} suggested plan(s). Any repository write remains blocked until an authorised reviewer approves it.",
    )


def _persist_suggested_change(*, result: Any, delivery: Any, installation_id: Optional[int]) -> None:
    if result.card is None or not isinstance(installation_id, int):
        return
    from google.cloud import firestore  # noqa: PLC0415

    firestore.Client(project=PROJECT).collection("suggested_changes").document(delivery.delivery_id).set({
        "run_id": delivery.delivery_id, "repository": delivery.repository,
        "source_pr": delivery.number, "source_head_sha": delivery.head_sha,
        "installation_id": installation_id, "path": result.card.target_path,
        "body": result.card.body, "plan_hash": result.card.plan_hash,
        "findings": result.card.findings, "advisories": result.card.advisories,
        "status": "awaiting_approval", "created_at": firestore.SERVER_TIMESTAMP,
    })


def _session_user(request: Request) -> Optional[dict[str, Any]]:
    session_id = request.cookies.get("mitos_session")
    if not session_id:
        return None
    try:
        from google.cloud import firestore  # noqa: PLC0415

        snapshot = firestore.Client(project=PROJECT).collection("sessions").document(session_id).get()
        if not snapshot.exists:
            return None
        session = snapshot.to_dict() or {}
        expires_at = session.get("expires_at")
        if not expires_at or expires_at <= datetime.now(timezone.utc):
            return None
        user = firestore.Client(project=PROJECT).collection("users").document(str(session["user_id"])).get()
        return user.to_dict() if user.exists else None
    except Exception:  # noqa: BLE001 - an unavailable session store means signed out
        return None


def _workspace_context(request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    from google.cloud import firestore  # noqa: PLC0415

    memberships = list(
        firestore.Client(project=PROJECT)
        .collection("workspace_memberships")
        .where(filter=firestore.FieldFilter("github_user_id", "==", user["github_user_id"]))
        .limit(1)
        .stream()
    )
    if not memberships:
        raise HTTPException(status_code=403, detail="No installed GitHub workspace is available")
    return user, memberships[0].to_dict() or {}


def _require_role(request: Request, workspace_id: str, roles: frozenset[str]) -> dict[str, Any]:
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    from google.cloud import firestore  # noqa: PLC0415

    membership_id = f"{workspace_id}:{user['github_user_id']}"
    membership = firestore.Client(project=PROJECT).collection("workspace_memberships").document(membership_id).get()
    record = membership.to_dict() if membership.exists else None
    if not record or record.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Workspace role does not permit this action")
    return user


@app.get("/github/auth/login")
def github_auth_login(request: Request) -> RedirectResponse:
    metadata = _github_app_metadata()
    client_id = str(metadata.get("client_id") or "")
    if not client_id:
        raise HTTPException(status_code=503, detail="Create the GitHub App before signing in")
    state = secrets.token_urlsafe(32)
    callback = f"{_public_url(request)}/github/auth/callback"
    query = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": callback, "state": state})
    response = RedirectResponse(
        url=f"https://github.com/login/oauth/authorize?{query}",
        status_code=302,
    )
    response.set_cookie(
        "mitos_github_oauth_state", state, max_age=600, httponly=True,
        secure=request.url.scheme == "https", samesite="lax",
    )
    return response


@app.get("/github/auth/callback")
def github_auth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    expected = request.cookies.get("mitos_github_oauth_state")
    if not expected or not secrets.compare_digest(expected, state):
        raise HTTPException(status_code=400, detail="Invalid or expired GitHub login state")
    metadata = _github_app_metadata()
    secret_prefix = str(metadata.get("secret_prefix") or "")
    if not secret_prefix or not metadata.get("client_id"):
        raise HTTPException(status_code=503, detail="GitHub App credentials are unavailable")
    client_secret = _read_managed_secret(f"{secret_prefix}-client-secret")
    token_response = httpx.post(
        "https://github.com/login/oauth/access_token",
        data={"client_id": metadata["client_id"], "client_secret": client_secret, "code": code},
        headers={"Accept": "application/json"}, timeout=30.0,
    )
    token_response.raise_for_status()
    access_token = str(token_response.json().get("access_token") or "")
    if not access_token:
        raise HTTPException(status_code=502, detail="GitHub did not return a user access token")
    github_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    profile_response = httpx.get("https://api.github.com/user", headers=github_headers, timeout=30.0)
    profile_response.raise_for_status()
    profile = profile_response.json()
    github_user_id = profile.get("id")
    if not isinstance(github_user_id, int):
        raise HTTPException(status_code=502, detail="GitHub user identity is invalid")
    from google.cloud import firestore  # noqa: PLC0415

    db = firestore.Client(project=PROJECT)
    user_id = str(github_user_id)
    db.collection("users").document(user_id).set({
        "github_user_id": github_user_id,
        "login": str(profile.get("login") or "")[:100],
        "name": str(profile.get("name") or profile.get("login") or "GitHub user")[:150],
        "avatar_url": str(profile.get("avatar_url") or "")[:500],
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    installations_response = httpx.get("https://api.github.com/user/installations", headers=github_headers, timeout=30.0)
    if installations_response.is_success:
        for installation in installations_response.json().get("installations", []):
            installation_id = installation.get("id")
            account_id = (installation.get("account") or {}).get("id")
            if isinstance(installation_id, int) and isinstance(account_id, int):
                workspace_id = f"github-{account_id}"
                membership_id = f"{workspace_id}:{github_user_id}"
                membership_ref = db.collection("workspace_memberships").document(membership_id)
                existing_membership = membership_ref.get()
                if existing_membership.exists:
                    assigned_role = str((existing_membership.to_dict() or {}).get("role") or "reviewer")
                else:
                    existing_workspace_members = list(
                        db.collection("workspace_memberships")
                        .where(filter=firestore.FieldFilter("workspace_id", "==", workspace_id))
                        .limit(1)
                        .stream()
                    )
                    assigned_role = "owner" if not existing_workspace_members else "reviewer"
                membership_ref.set({
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "github_user_id": github_user_id,
                    "role": assigned_role,
                    "installation_id": installation_id,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                }, merge=True)
    session_id = secrets.token_urlsafe(32)
    db.collection("sessions").document(session_id).create({
        "user_id": user_id,
        "created_at": firestore.SERVER_TIMESTAMP,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
    })
    response = RedirectResponse(url="/#dashboard", status_code=303)
    response.set_cookie(
        "mitos_session", session_id, max_age=604800, httponly=True,
        secure=request.url.scheme == "https", samesite="lax",
    )
    response.delete_cookie("mitos_github_oauth_state")
    return response


@app.get("/api/session")
def session_status(request: Request) -> dict[str, Any]:
    user = _session_user(request)
    if not user:
        return {"authenticated": False, "user": None, "memberships": []}
    try:
        from google.cloud import firestore  # noqa: PLC0415

        memberships = [
            document.to_dict()
            for document in firestore.Client(project=PROJECT)
            .collection("workspace_memberships")
            .where(filter=firestore.FieldFilter("github_user_id", "==", user["github_user_id"]))
            .stream()
        ]
    except Exception:  # noqa: BLE001 - identity remains valid if membership projection is unavailable
        memberships = []
    return {"authenticated": True, "user": user, "memberships": memberships}


@app.post("/api/session/logout")
def session_logout(request: Request) -> RedirectResponse:
    session_id = request.cookies.get("mitos_session")
    if session_id:
        try:
            from google.cloud import firestore  # noqa: PLC0415

            firestore.Client(project=PROJECT).collection("sessions").document(session_id).delete()
        except Exception as exc:  # noqa: BLE001 - cookie removal still logs the browser out
            print(json.dumps({"event": "session.delete_failed", "error": type(exc).__name__}), flush=True)
    response = RedirectResponse(url="/#dashboard", status_code=303)
    response.delete_cookie("mitos_session")
    return response


@app.get("/github/app/status")
def github_app_status() -> dict[str, Any]:
    """Expose readiness and installation metadata, never credentials."""
    metadata = _github_app_metadata()
    slug = str(metadata.get("slug") or os.environ.get("MITOS_GITHUB_APP_SLUG", "")).strip()
    secret_configured = bool(metadata.get("credentials_stored")) or _webhook_secret() != NO_SECRET_CONFIGURED
    return {
        "configured": bool(slug and secret_configured),
        "app_slug": slug or None,
        "install_url": f"/github/app/install" if slug else None,
        "create_url": "/github/app/new",
        "webhook_endpoint": "/webhook/github",
        "webhook_secret_configured": secret_configured,
        "accepted_repositories": _connected_repositories(),
        "events": ["installation", "installation_repositories", "pull_request", "ping"],
        "write_mode": "approval_required",
    }


# A configuration problem, answered as one. Raising out of the route produced
# `500 Internal Server Error` with no body, which tells an operator nothing and
# reads like a bug in the flow rather than a missing setting. 503 because the
# service cannot perform this function until it is configured, and it is not the
# caller who got anything wrong.
@app.exception_handler(NoPublicUrl)
async def _no_public_url(request, exc: NoPublicUrl):
    return JSONResponse(
        status_code=503,
        content={
            "error": "this deployment cannot say what its own public address is",
            "detail": str(exc),
            "fix": (
                "set MITOS_PUBLIC_URL to this service's https address. Terraform "
                "sets it from the Cloud Run URL; a deployment made another way "
                "has to pass it."
            ),
        },
    )


# This page used to post to GitHub the moment it loaded. When GitHub refused the
# manifest the owner saw "Invalid GitHub App configuration / redirect_url must be
# a valid URL", which names the wrong field, gives no cause, and shows nothing of
# what was actually sent. It happened twice.
#
# So the page now does two things it did not do.
#
# It checks its own manifest against `service/manifest.py` before rendering, the
# same rules the integration suite and `deployed.yml` apply. A manifest we
# already know is wrong is never sent, and the reason appears here, in our words,
# next to the value that caused it.
#
# And it shows the URLs GitHub is about to store, then waits. One extra click, in
# exchange for the reader being able to see that the addresses are right —
# including the two failures an automatic redirect cannot show anyone, where a
# cached copy of this page carries `http://`, or a state the cookie no longer
# matches.
#
# The docstring stays one line on purpose: FastAPI publishes it as the
# description of this operation in `openapi.yaml`, which a stranger reads.
@app.get("/github/app/new")
def github_app_new(request: Request) -> HTMLResponse:
    """Show the GitHub App manifest for review, then post it to GitHub."""
    state = secrets.token_urlsafe(32)
    base = _public_url(request)
    manifest = {
        "name": os.environ.get("MITOS_GITHUB_APP_NAME", "Mitos Change Intelligence"),
        "url": base,
        "hook_attributes": {"url": f"{base}/webhook/github", "active": True},
        "redirect_url": f"{base}/github/app/manifest/callback",
        "setup_url": f"{base}/github/app/setup/callback",
        "callback_urls": [f"{base}/github/auth/callback"],
        "public": False,
        "default_permissions": {
            "checks": "write",
            "contents": "write",
            "metadata": "read",
            "pull_requests": "write",
        },
        "default_events": ["pull_request"],
    }

    action = f"https://github.com/settings/apps/new?state={state}"
    faults = manifest_problems(action, manifest)

    nonce = request.state.csp_nonce
    esc = lambda value: html.escape(str(value), quote=True)  # noqa: E731

    rows = "".join(
        f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>"
        for label, value in (
            ("Homepage", manifest["url"]),
            ("Webhook", manifest["hook_attributes"]["url"]),
            ("Redirect after creation", manifest["redirect_url"]),
            ("Setup after install", manifest["setup_url"]),
            ("OAuth callback", manifest["callback_urls"][0]),
        )
    )

    if faults:
        listed = "".join(f"<li>{esc(problem)}</li>" for problem in faults)
        main = (
            "<h1>This deployment cannot create the App</h1>"
            "<p>The manifest it would send is one GitHub would refuse, so it was "
            "not sent. Each line below names the field and the reason.</p>"
            f"<ul class=bad>{listed}</ul>"
            f"<table>{rows}</table>"
            "<p>Every URL comes from this service's own public address. Set "
            "<code>MITOS_PUBLIC_URL</code> to the https address of this "
            "deployment and reload.</p>"
        )
        form = ""
        status = 503
    else:
        main = (
            "<h1>Create the Mitos GitHub App</h1>"
            "<p>GitHub will register an App you own, in your account or "
            "organisation, and store the addresses below. Check them before "
            "continuing: they are what GitHub calls back later, and a wrong one "
            "fails days from now rather than here.</p>"
            f"<table>{rows}</table>"
            "<p>It asks for <b>read and write on checks, contents and pull "
            "requests</b>, <b>read on metadata</b>, and one event, "
            "<code>pull_request</code>. Nothing is installed on a repository "
            "until you choose one on the next screen.</p>"
        )
        form = (
            f'<form id="manifest" method="post" action="{esc(action)}">'
            f'<input type="hidden" name="manifest" value="{esc(json.dumps(manifest))}">'
            '<button type="submit">Continue to GitHub</button></form>'
        )
        status = 200

    style = (
        "body{font:16px/1.6 ui-sans-serif,system-ui,sans-serif;max-width:46rem;"
        "margin:3rem auto;padding:0 1.25rem;color:#111}"
        "h1{font-size:1.5rem;margin:0 0 1rem}"
        "table{border-collapse:collapse;width:100%;margin:1.5rem 0;font-size:.9rem}"
        "th{text-align:left;font-weight:600;padding:.5rem .75rem .5rem 0;"
        "white-space:nowrap;vertical-align:top;color:#444}"
        "td{padding:.5rem 0;font-family:ui-monospace,monospace;word-break:break-all}"
        "tr+tr th,tr+tr td{border-top:1px solid #e5e5e5}"
        "button{font:inherit;background:#111;color:#fff;border:0;border-radius:.4rem;"
        "padding:.7rem 1.25rem;cursor:pointer}"
        "ul.bad{background:#fff4f4;border-left:3px solid #c00;padding:.75rem 1rem "
        ".75rem 2rem;margin:1.5rem 0}"
        "code{background:#f3f3f3;padding:.1rem .3rem;border-radius:.2rem}"
    )

    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Create Mitos GitHub App</title>"
        f'<style nonce="{nonce}">{style}</style></head><body>'
        f"{main}{form}</body></html>"
    )

    response = HTMLResponse(page, status_code=status)
    # A single-use token paired with a ten minute cookie. A cached copy carries
    # a state the cookie no longer matches, and that failure surfaces at GitHub,
    # one step after the thing that caused it.
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        "mitos_github_manifest_state",
        state,
        max_age=600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'none'; style-src 'nonce-{nonce}'; "
        "form-action https://github.com; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/github/app/manifest/callback")
def github_app_manifest_callback(request: Request, code: str, state: str) -> RedirectResponse:
    expected = request.cookies.get("mitos_github_manifest_state")
    if not expected or not secrets.compare_digest(expected, state):
        raise HTTPException(status_code=400, detail="Invalid or expired GitHub App setup state")
    conversion = httpx.post(
        f"https://api.github.com/app-manifests/{code}/conversions",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=30.0,
    )
    if conversion.status_code != 201:
        raise HTTPException(status_code=502, detail="GitHub App creation could not be completed")
    created = conversion.json()
    secret_prefix = f"mitos-{os.environ.get('MITOS_STAGE', 'prod')}-github-app"
    credentials = {
        f"{secret_prefix}-private-key": created["pem"],
        f"{secret_prefix}-client-secret": created["client_secret"],
        f"{secret_prefix}-webhook-secret": created["webhook_secret"],
    }
    try:
        for secret_id, value in credentials.items():
            _store_github_app_secret(secret_id, value)
        from google.cloud import firestore  # noqa: PLC0415

        firestore.Client(project=PROJECT).collection("system").document("github_app").set({
            "app_id": int(created["id"]),
            "client_id": created["client_id"],
            "slug": created["slug"],
            "owner_login": created.get("owner", {}).get("login"),
            "credentials_stored": True,
            "secret_prefix": secret_prefix,
            "created_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as exc:  # noqa: BLE001 - never leak credential values
        print(json.dumps({"event": "github_app.storage_failed", "error": type(exc).__name__}), flush=True)
        raise HTTPException(status_code=503, detail="GitHub App was created but secure credential storage failed") from exc
    response = RedirectResponse(url="/#repositories?github_app=created", status_code=303)
    response.delete_cookie("mitos_github_manifest_state")
    return response


@app.get("/github/app/install")
def github_app_install() -> RedirectResponse:
    metadata = _github_app_metadata()
    slug = str(metadata.get("slug") or os.environ.get("MITOS_GITHUB_APP_SLUG", "")).strip()
    if not slug:
        return RedirectResponse(url="/github/app/new", status_code=302)
    return RedirectResponse(url=f"https://github.com/apps/{slug}/installations/new", status_code=302)


@app.get("/github/app/setup/callback")
def github_app_setup_callback(installation_id: int, setup_action: str = "install") -> RedirectResponse:
    """Return from GitHub installation; webhook data remains the source of truth."""
    if installation_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid GitHub installation")
    return RedirectResponse(
        url=f"/#repositories?installation_id={installation_id}&setup_action={setup_action}",
        status_code=303,
    )


@app.get("/api/workspace/config")
def workspace_config(request: Request) -> dict[str, Any]:
    _, membership = _workspace_context(request)
    workspace_id = str(membership["workspace_id"])
    from google.cloud import firestore  # noqa: PLC0415

    repositories = [
        document.to_dict() or {}
        for document in firestore.Client(project=PROJECT)
        .collection("repositories")
        .where(filter=firestore.FieldFilter("workspace_id", "==", workspace_id))
        .stream()
    ]
    active = sorted(
        str(repository["full_name"])
        for repository in repositories
        if repository.get("active") and repository.get("full_name")
    )
    return {
        "workspace_id": workspace_id,
        "role": membership.get("role"),
        "installation_id": membership.get("installation_id"),
        "read_scope": list(READ_SCOPE),
        "webhook_repositories": active,
        "max_reads_per_run": MAX_READS_PER_RUN,
        "max_bytes_per_read": MAX_BYTES_PER_READ,
    }


def _workspace_analytics_payload(*, repositories: list[dict[str, Any]], entries: list[Entry], suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    active = sorted(str(repo["full_name"]) for repo in repositories if repo.get("active") and repo.get("full_name"))
    active_set = set(active)
    run_ids = {
        entry.run_id for entry in entries
        if str(entry.payload.get("repository") or entry.payload.get("repo") or "") in active_set
    }
    scoped = [entry for entry in entries if entry.run_id in run_ids]
    runs: dict[str, list[Entry]] = {}
    for entry in scoped:
        runs.setdefault(entry.run_id, []).append(entry)
    findings = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    activity: list[dict[str, Any]] = []
    trend: dict[str, dict[str, Any]] = {}
    repository_rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for offset in range(13, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        trend[day] = {"date": day, "analysed": 0, "attention": 0, "published": 0}
    for run_id, run_entries in runs.items():
        ordered = sorted(run_entries, key=lambda item: item.recorded_at)
        trigger = next((item for item in ordered if item.kind == "trigger.webhook"), ordered[0])
        repository = str(trigger.payload.get("repository") or "")
        pr = trigger.payload.get("pr")
        attention = False
        for item in ordered:
            if item.kind.startswith("finding."):
                severity = str(item.payload.get("severity") or "medium").lower()
                findings[severity if severity in findings else "medium"] += 1
                attention = True
            if item.kind == "evaluator.verdict" and item.payload.get("passed") is False:
                attention = True
        day = ordered[-1].recorded_at[:10]
        if day in trend:
            trend[day]["analysed"] += 1
            trend[day]["attention"] += int(attention)
            trend[day]["published"] += int(any(item.kind == "write.executed" for item in ordered))
        activity.append({
            "run_id": run_id, "repository": repository, "pr": pr,
            "event": "Needs attention" if attention else "Analysis completed",
            "actor": ordered[-1].actor, "recorded_at": ordered[-1].recorded_at,
        })
    for repository in active:
        repository_runs = [items for items in runs.values() if any(str(item.payload.get("repository") or "") == repository for item in items)]
        latest = max((item.recorded_at for items in repository_runs for item in items), default=None)
        attention_count = sum(any(item.kind.startswith("finding.") or (item.kind == "evaluator.verdict" and item.payload.get("passed") is False) for item in items) for items in repository_runs)
        repository_rows.append({"repository": repository, "analyses": len(repository_runs), "attention": attention_count, "last_activity": latest, "status": "attention" if attention_count else "healthy"})
    pending = sum(item.get("status") == "awaiting_approval" for item in suggestions)
    published = sum(item.get("status") == "published" for item in suggestions)
    activity.sort(key=lambda item: item["recorded_at"], reverse=True)
    return {
        "summary": {"repositories": len(active), "analysed_prs": len(runs), "findings": sum(findings.values()), "pending_approvals": pending, "published_suggestions": published},
        "trend": list(trend.values()), "findings_by_severity": [{"severity": key, "count": value} for key, value in findings.items()],
        "repositories": repository_rows, "recent_activity": activity[:12],
    }


@app.get("/api/workspace/analytics")
def workspace_analytics(request: Request) -> dict[str, Any]:
    _, membership = _workspace_context(request)
    workspace_id = str(membership["workspace_id"])
    from google.cloud import firestore  # noqa: PLC0415

    db = firestore.Client(project=PROJECT)
    repositories = [doc.to_dict() or {} for doc in db.collection("repositories").where(filter=firestore.FieldFilter("workspace_id", "==", workspace_id)).stream()]
    active = {str(repo.get("full_name")) for repo in repositories if repo.get("active") and repo.get("full_name")}
    suggestions = [doc.to_dict() or {} for doc in db.collection("suggested_changes").stream() if str((doc.to_dict() or {}).get("repository") or "") in active]
    return _workspace_analytics_payload(repositories=repositories, entries=ledger().all(), suggestions=suggestions)


class SuggestedChangeApprovalRequest(BaseModel):
    run_id: str


@app.post("/api/workspace/suggested-changes/approve")
def approve_suggested_change(req: SuggestedChangeApprovalRequest, request: Request) -> dict[str, Any]:
    user, membership = _workspace_context(request)
    workspace_id = str(membership["workspace_id"])
    _require_role(request, workspace_id, frozenset({"owner", "reviewer"}))
    from google.cloud import firestore  # noqa: PLC0415

    db = firestore.Client(project=PROJECT)
    reference = db.collection("suggested_changes").document(req.run_id)
    snapshot = reference.get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Suggested change not found")
    change = snapshot.to_dict() or {}
    repository = str(change.get("repository") or "")
    repository_matches = list(
        db.collection("repositories")
        .where(filter=firestore.FieldFilter("full_name", "==", repository))
        .limit(1)
        .stream()
    )
    repository_doc = repository_matches[0].to_dict() if repository_matches else {}
    if repository_doc.get("workspace_id") != workspace_id or not repository_doc.get("active"):
        raise HTTPException(status_code=403, detail="Suggested change is outside this workspace")
    if change.get("status") == "published":
        return {"status": "published", "receipt": change.get("receipt") or {}}
    if change.get("status") != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Suggested change is not awaiting approval")

    actor = str(user["login"])
    approval = approvals().grant(Approval(
        repository=repository,
        path=str(change["path"]),
        branch=f"mitos/suggestion-{change['source_pr']}-{req.run_id[:8]}",
        digest=body_digest(
            repository=repository, path=str(change["path"]),
            branch=f"mitos/suggestion-{change['source_pr']}-{req.run_id[:8]}",
            body=str(change["body"]), commit=str(change["source_head_sha"]),
        ),
        run_id=req.run_id, actor=actor, commit=str(change["source_head_sha"]),
        intent="create_suggested_pull_request",
    ))
    approval.check(
        repository=repository, path=str(change["path"]), branch=approval.branch,
        body=str(change["body"]), commit=str(change["source_head_sha"]),
    )
    receipt = _github_suggested_pr(
        installation_id=int(change["installation_id"]), repository=repository,
        source_pr=int(change["source_pr"]), expected_head=str(change["source_head_sha"]),
        path=str(change["path"]), body=str(change["body"]), run_id=req.run_id,
    )
    approvals().consume(approval.nonce, by=actor)
    reference.set({
        "status": "published", "approved_by": actor, "approval_nonce": approval.nonce,
        "approved_at": firestore.SERVER_TIMESTAMP, "receipt": receipt,
    }, merge=True)
    ledger().append(Entry(
        kind="write.executed", actor=actor, subject=f"{repository}#{change['source_pr']}",
        run_id=req.run_id, payload={"approved": True, "plan_hash": change.get("plan_hash"), **receipt},
    ))
    return {"status": "published", "receipt": receipt}


@app.get("/api/workspace/thread")
def workspace_thread(request: Request, limit: int = 500) -> dict[str, Any]:
    _, membership = _workspace_context(request)
    workspace_id = str(membership["workspace_id"])
    from google.cloud import firestore  # noqa: PLC0415

    repository_docs = (
        firestore.Client(project=PROJECT)
        .collection("repositories")
        .where(filter=firestore.FieldFilter("workspace_id", "==", workspace_id))
        .stream()
    )
    repositories = {
        str(document.to_dict().get("full_name"))
        for document in repository_docs
        if document.to_dict().get("active") and document.to_dict().get("full_name")
    }
    all_entries = ledger().all()
    scoped_run_ids = {
        entry.run_id
        for entry in all_entries
        if str(entry.payload.get("repository") or entry.payload.get("repo") or "") in repositories
    }
    entries = [entry for entry in all_entries if entry.run_id in scoped_run_ids][-max(1, min(limit, 1000)):]
    return {"count": len(entries), "entries": [entry.to_doc() for entry in entries]}


@app.get("/thread")
def thread(limit: int = 100) -> dict[str, Any]:
    _require_demo_mode()
    entries = ledger().all()[-limit:]
    return {
        "count": len(entries),
        "entries": [e.to_doc() for e in entries],
    }


# `unknown` is a real answer and is not treated as a pass anywhere: the
# deployed check refuses it, because an image that cannot say what it is
# gives no evidence that it is the audited one.
BUILD_SHA = os.environ.get("MITOS_BUILD_SHA", "unknown")

_APPROVALS = None
_CLAIMS = None

# One limiter per process, for the endpoints that spend money or append to
# the thread. The read-only pages are not limited: they cost a Firestore
# read and rationing them would only make the demo look broken.
_LIMITER = RateLimiter()


def _within_budget(request) -> None:
    """Raise 429 if this caller has had its share of the expensive path."""
    decision = _LIMITER.check(client_of(request))
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"{decision.limit} runs per client per "
                f"{int(_LIMITER.window_s // 60)} minutes on this public demo. "
                f"Every run calls Gemini and appends to the provenance thread, "
                f"so the bound is a cost bound rather than a policy. Try again "
                f"in {decision.retry_after_s}s, or read /runs for what has "
                f"already happened."
            ),
            headers={"Retry-After": str(decision.retry_after_s)},
        )

# The repository the specification is published to. Named here rather than
# taken from the request, so a caller cannot approve bytes for one repository
# and have them written to another.
SPEC_REPOSITORY = os.environ.get("MITOS_SPEC_REPO", "upgradedev/mitos-spec")

# Whether an anonymous caller may cause a real publish. Off by default. The
# public reader produces the approval card and stops there, because a public
# endpoint that writes on request is the shape of the hole this replaced, even
# once the bytes are the fleet's own rather than the caller's.
PUBLIC_DEMO_MAY_WRITE = os.environ.get("MITOS_PUBLIC_DEMO_MAY_WRITE", "") == "yes"


def claims():
    global _CLAIMS
    if _CLAIMS is None:
        _CLAIMS = build_claims(PROJECT)
    return _CLAIMS


def approvals():
    global _APPROVALS
    if _APPROVALS is None:
        _APPROVALS = build_approval_store(PROJECT)
    return _APPROVALS


def _publisher(actor: str = "unattended", run_id: str = ""):
    """Only the writer service holds a credential that can publish.

    The reader orchestrates the chore and then has to ask, because IAM will not
    give it the deploy key. `MITOS_WRITER_URL` is that request; without it the
    reader falls back to recording the plan and publishing nothing, which is
    what the offline demo does.
    """
    if ROLE == "writer":
        return build_spec_repo(PROJECT)
    url = os.environ.get("MITOS_WRITER_URL")
    if not url:
        return None
    if not PUBLIC_DEMO_MAY_WRITE:
        # The reader is the anonymous surface. An unauthenticated request that
        # can end in a publish is the shape of the hole this replaced, even now
        # that the bytes are the fleet's own rather than the caller's, so the
        # public deployment produces the approval card and stops.
        return None
    return _RemoteWriter(url, actor=actor, run_id=run_id, repository=SPEC_REPOSITORY)


@dataclass
class _RemoteWriter:
    """Delegates the write to the writer service over an authenticated call.

    This is the privilege boundary made concrete: the reader cannot perform the
    write, so it asks the one identity that can.

    The writer verifies the approval independently: it recomputes the digest
    from the bytes that arrived and refuses if they are not the bytes that were
    approved. For one commit this docstring claimed that and it was not true.
    """

    url: str
    actor: str = "unattended"
    run_id: str = ""
    repository: str = ""
    nonce: str = ""

    def publish(self, *, path: str, body: str, message: str, branch: str) -> dict:
        # Recorded before the request, over the bytes actually being sent. The
        # writer recomputes this digest from what arrives, so the approval
        # covers these bytes and no others.
        granted = approvals().grant(
            Approval(
                repository=self.repository,
                path=path,
                branch=branch,
                digest=body_digest(
                    repository=self.repository, path=path, branch=branch, body=body
                ),
                run_id=self.run_id,
                actor=self.actor,
            )
        )
        self.nonce = granted.nonce
        return self._send(path=path, body=body, message=message, branch=branch)

    def _send(self, *, path: str, body: str, message: str, branch: str) -> dict:
        import google.auth.transport.requests  # noqa: PLC0415
        import google.oauth2.id_token  # noqa: PLC0415

        try:
            token = google.oauth2.id_token.fetch_id_token(
                google.auth.transport.requests.Request(), self.url
            )
            r = httpx.post(
                f"{self.url}/execute",
                json={
                    "path": path,
                    "body": body,
                    "message": message,
                    "branch": branch,
                    "nonce": self.nonce,
                    "repository": self.repository,
                },
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
    # The approval this write claims to be covered by. Not optional: a request
    # without one is refused, which is what stops a caller who reaches this
    # endpoint from choosing what gets written.
    nonce: str = ""
    repository: str = ""
    # The head sha the reviewer was looking at. Presented, not taken from
    # the approval, because an approval that verifies against its own
    # stored value checks nothing.
    commit: str = ""


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

    # The write is bound to an approval, and this is where the binding is
    # enforced rather than described. The digest is recomputed from the bytes
    # actually presented, so a caller that changes one character after the
    # approval was granted produces a different digest and is refused. The
    # nonce is consumed transactionally, so the same approval cannot be
    # replayed into a second write.
    repository = req.repository or SPEC_REPOSITORY
    try:
        approval = verify_and_consume(
            approvals(),
            nonce=req.nonce,
            repository=repository,
            path=req.path,
            branch=req.branch,
            body=req.body,
            commit=req.commit,
            by=f"{ROLE}@{PROJECT}",
        )
    except Replayed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Expired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except Mismatch as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    receipt = build_spec_repo(PROJECT).publish(
        path=req.path, body=req.body, message=req.message, branch=req.branch
    )
    # The receipt names what authorised it, so a published file traces back to
    # a person and a run without anybody having to correlate two systems.
    receipt["approved_by"] = approval.actor
    receipt["approval_nonce"] = approval.nonce
    receipt["run_id"] = approval.run_id
    return receipt


class RunRequest(BaseModel):
    pr: int = 4471
    approve: bool = False
    seed: bool = False


# What a specialist may open in a customer repository. A property of their
# layout, not of ours, so it is configuration.
READ_SCOPE = tuple(
    p.strip()
    for p in os.environ.get("MITOS_READ_SCOPE", "docs/,services/,registers/").split(",")
    if p.strip()
)

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
            metadata = _github_app_metadata()
            generated_prefix = str(metadata.get("secret_prefix") or "").strip()
            secret_id = (
                f"{generated_prefix}-webhook-secret"
                if generated_prefix
                else f"mitos-{os.environ.get('MITOS_STAGE', 'prod')}-settings-reader-github-webhook-secret"
            )
            name = f"projects/{PROJECT}/secrets/{secret_id}/versions/latest"
            _WEBHOOK_SECRET = client.access_secret_version(name=name).payload.data.decode("utf-8").strip()
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
    headers = {key.lower(): value for key, value in request.headers.items()}
    event = headers.get("x-github-event", "")
    delivery_id = headers.get("x-github-delivery", "unknown")
    try:
        if len(body) > wh.MAX_BODY_BYTES:
            raise wh.Rejected(f"body of {len(body)} bytes exceeds the cap", 413)
        wh.verify_signature(body, headers.get("x-hub-signature-256"), _webhook_secret())
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise wh.Rejected("body is not a JSON object", 400)
        if event == "ping":
            return JSONResponse({"accepted": True, "event": "ping", "delivery": delivery_id})
        if event in {"installation", "installation_repositories"}:
            try:
                claims().claim(delivery_id, note=event)
            except AlreadySeen:
                return JSONResponse({"accepted": True, "duplicate": True, "delivery": delivery_id})
            persisted = _persist_installation_event(payload, delivery_id)
            claims().complete(delivery_id, outcome=f"{event} persisted")
            return JSONResponse(
                {"accepted": True, "event": event, "delivery": delivery_id, **persisted},
                status_code=202,
            )
        allowed = frozenset(_connected_repositories()) | ALLOWED_REPOS
        delivery = wh.parse(
            body,
            dict(request.headers),
            secret=_webhook_secret(),
            allowed_repositories=allowed,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return JSONResponse({"accepted": False, "reason": f"body is not JSON: {exc}"}, status_code=400)
    except wh.Rejected as rej:
        # 202 for "understood, not acting" so GitHub does not disable the hook
        # over deliveries we simply do not care about.
        return JSONResponse({"accepted": False, "reason": str(rej)}, status_code=rej.status)

    led = ledger()

    # Claimed before anything is appended or started. GitHub retries a delivery
    # it did not get a timely answer for, and Cloud Run runs up to four readers,
    # so the same pull request can arrive twice within seconds on two different
    # instances. Nothing keyed on the delivery id, so both ran the whole chore:
    # four model calls each, and two accounts of one event in the thread that is
    # supposed to BE the account.
    #
    # 200 rather than an error. A retried delivery is GitHub behaving correctly,
    # and answering it with a failure is how a webhook gets disabled.
    try:
        claims().claim(delivery.delivery_id, note=f"{delivery.repository}#{delivery.number}")
    except AlreadySeen:
        return JSONResponse(
            {
                "accepted": True,
                "duplicate": True,
                "delivery": delivery.delivery_id,
                "note": (
                    "this delivery was handled already; nothing was run again "
                    "and nothing was appended"
                ),
            }
        )

    entry = led.append(
        Entry(
            kind="trigger.webhook",
            actor="github",
            subject=f"{delivery.repository}#{delivery.number}",
            payload=delivery.as_dict(),
            run_id=delivery.delivery_id,
        )
    )
    installation_id = (payload.get("installation") or {}).get("id")
    check_run_id: Optional[int] = None
    if isinstance(installation_id, int):
        try:
            check_run_id = _safe_github_check(
                repository=delivery.repository,
                installation_id=installation_id,
                head_sha=delivery.head_sha,
                status="queued",
            )
        except Exception as exc:  # noqa: BLE001 - check delivery must not reject the webhook
            print(json.dumps({"event": "github.check_create_failed", "error": type(exc).__name__}), flush=True)

    def work() -> None:
        try:
            if isinstance(installation_id, int) and check_run_id is not None:
                _safe_github_check(
                    repository=delivery.repository, installation_id=installation_id,
                    head_sha=delivery.head_sha, status="in_progress", check_run_id=check_run_id,
                )
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
                if isinstance(installation_id, int) and check_run_id is not None:
                    _safe_github_check(
                        repository=delivery.repository, installation_id=installation_id,
                        head_sha=delivery.head_sha, status="completed", check_run_id=check_run_id,
                        conclusion="neutral", summary="No readable patch required analysis.",
                    )
                return
            result = _run_webhook_chore(wh=wh, delivery=delivery, files=files, led=led)
            _persist_suggested_change(
                result=result, delivery=delivery, installation_id=installation_id,
            )
            _complete_analysis_check(
                led=led, delivery=delivery, installation_id=installation_id,
                check_run_id=check_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            led.append(
                Entry(
                    kind="trigger.failed", actor="github", subject=entry.subject,
                    parent_id=entry.entry_id, run_id=delivery.delivery_id,
                    payload={"error": f"{type(exc).__name__}: {str(exc)[:300]}"},
                )
            )
            # Deliberately not completed. A run that failed for a transient
            # reason should be retryable, and leaving the lease to expire is
            # what makes GitHub's next delivery pick it up instead of being
            # told it already happened.
        else:
            claims().complete(delivery.delivery_id, outcome="chore finished")

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
def run_stream(req: RunRequest, request: Request) -> StreamingResponse:
    """The chore, streamed as it happens.

    Bounded like `/run`: it spends the same four model calls, and streaming
    them does not make them free.

    `/run` returns when the whole thing is finished, and with a model in the
    loop that is minutes rather than seconds: the specialists read the
    repository, and each read is a round trip. A judge who posts to it waits,
    sees nothing, and concludes it is broken. The UAT caught exactly that.

    So this streams. Every beat is flushed the moment it occurs, which is both
    the honest fix for the timeout and a better thing to watch: the reads appear
    one at a time, in the order the agent chose them.
    """
    _require_demo_mode()
    _within_budget(request)
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
def run(req: RunRequest, request: Request) -> JSONResponse:
    _require_demo_mode()
    _within_budget(request)
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


@app.get("/config")
def config() -> dict[str, Any]:
    """The bounds, as values rather than as prose in a README.

    Both are enforced elsewhere and neither is settable from here. `read_scope`
    is what the tool layer will open and `webhook_repositories` is what the
    verifier will accept, and publishing them is the difference between a
    dashboard that says a boundary exists and one that shows you where it is.
    """
    _require_demo_mode()
    return {
        "read_scope": list(READ_SCOPE),
        "webhook_repositories": _connected_repositories(),
        "max_reads_per_run": MAX_READS_PER_RUN,
        "max_bytes_per_read": MAX_BYTES_PER_READ,
    }


def _nonce(request) -> str:
    """The nonce the middleware minted for this request.

    Empty when there is none, which happens only if a caller renders a page
    outside the request cycle. An empty nonce produces a tag the policy does not
    authorise, so the page fails visibly rather than falling back to something
    permissive.
    """
    return getattr(request.state, "csp_nonce", "")


def _page_data(limit: int) -> tuple[list[dict[str, Any]], int]:
    """Entries for a page, and how many exist, so a page can say it is a window.

    A list showing the last 300 of 4000 that does not say so is a list that
    quietly lies about what happened.
    """
    everything = ledger().all()
    return [e.to_doc() for e in everything[-limit:]], len(everything)


def _audit(
    repository: Optional[str] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, str]:
    """Audit a repository against the engineering standard.

    Only the deterministic pass runs here, and that is a deliberate limit rather
    than an unfinished one. It returns in about a millisecond over the demo
    corpus, so a page can be served synchronously. The agentic reader takes
    minutes, because it is an agent opening files one at a time and thinking
    between them, and a judge clicking a link should not be holding a socket
    open while that happens. It has a live test instead.
    """
    try:
        corpus = build_corpus(repository, ref="HEAD", scope=AUDIT_SCOPE)
    except ValueError as exc:
        return [], {}, "", str(exc) or "that is not a repository name"
    result = check_repository(corpus)
    note = ""
    if repository:
        # Said on the page, not discovered by the reader when rules start coming
        # back undetermined. Unauthenticated GitHub allows 60 requests an hour
        # and an audit reads up to 300 files, so a large repository will run out
        # partway through and the rules whose files were refused report that
        # they could not be determined.
        note = (
            "Read over the public GitHub API with no credential, which allows 60 "
            "requests an hour. A repository large enough to exhaust that will "
            "have rules reported as could not be determined rather than passed."
        )
    return (
        [f.as_dict() for f in result.results],
        result.summary.as_dict() if result.summary else {},
        note,
        "",
    )


@app.get("/metrics.json")
def metrics_json(limit: int = 300) -> dict[str, Any]:
    """The same figures the overview renders, as data.

    Added because a client that cannot fetch these has to compute them, and a
    client computing its own headline numbers is a second implementation that
    will disagree with the first. Every value here is counted from the
    provenance thread; `summarise` refuses a median under three runs rather
    than producing one.
    """
    entries, total = _page_data(limit)
    return {"window": {"shown": len(entries), "total": total}, **summarise(entries)}


@app.get("/standards.json")
def standards_json(
    request: Request, repository: Optional[str] = None
) -> dict[str, Any]:
    if repository:
        # Only when it reaches out. Auditing the demo corpus is a
        # millisecond of local work and rationing it would be theatre.
        _within_budget(request)
    findings, summary, note, error = _audit(repository)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {
        "repository": repository,
        "summary": summary,
        "findings": findings,
        "note": note,
        "agentic_pass": (
            "not run here. The five rules a pattern cannot settle stay at "
            "needs_judgement, which is the honest verdict for them."
        ),
    }


@app.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request) -> str:
    """What somebody actually does with this, in three steps.

    The base URL comes from the request rather than a constant, so the webhook
    endpoint printed on the page is the one belonging to whichever deployment
    the reader is looking at.
    """
    return render_connect(
        ROLE,
        base=public_base(
            str(request.base_url), request.headers.get("x-forwarded-proto", "")
        ),
        nonce=_nonce(request),
    )


@app.get("/standards", response_class=HTMLResponse)
def standards_page(request: Request, repository: Optional[str] = None) -> str:
    """What a repository fails, and what could not be decided from it.

    `?repository=owner/name` points it at real code. Without one it audits the
    demo corpus, which is what the recorded demo and every offline test use.
    """
    if repository:
        _within_budget(request)
    findings, summary, note, error = _audit(repository)
    # A typo in the form is not a server fault. It used to raise out of the
    # corpus and land as a 500, which reads as "this is broken" rather than
    # "check the name", and the form is the first thing a stranger touches.
    return render_standards(
        findings,
        summary,
        ROLE,
        repository=None if error else repository,
        note=note,
        form=audit_form(repository, error),
        nonce=_nonce(request),
    )


# The interface, which is now a built application rather than pages rendered
# here. This service rendered six pages. Four of them are replaced by the
# application, which says the same things better: the overview at /, the fleet
# table, the run list and the thread view. The two the application does not
# implement, /standards and /connect, stay exactly where they were. Deleting a
# working tool because a different tool shipped would be removing function and
# calling it a refactor.
#
# Built to real files, and served as real files. Nothing is inlined, which is
# what lets `script-src 'self'` and `style-src 'self'` be the whole of the
# widening.
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

# Mounted at /assets and nowhere else. A catch-all that answered index.html for
# any unmatched path is the usual way to serve a single-page app, and it would
# make this service answer 200 for URLs it does not have. The application
# routes on the fragment, which the server never sees, so it needs no such
# fallback.
#
# `check_dir=False` because a source checkout has no `web/dist`: the build
# happens in the image. StaticFiles raises at construction otherwise, and that
# raise would take down every JSON endpoint in this module and the OpenAPI
# generator that imports it.
app.mount(
    "/assets",
    StaticFiles(directory=str(WEB_DIST / "assets"), check_dir=False),
    name="assets",
)

NOT_BUILT = """The interface is not in this image.

web/dist is missing, so there is nothing to serve at this path. Everything else
still answers, and the whole system is readable without it:

  /identity       who this service is, and what it cannot reach
  /config         the read scope and the budgets
  /catalog        the companions
  /thread         the provenance entries
  /metrics.json   the headline figures
  /standards      the audit

To build it:  cd web && npm ci && npm run build
"""


@app.get("/", response_class=HTMLResponse)
def index() -> Response:
    """The application, or a plain account of why it is not here.

    Registered whether or not the build exists, rather than only when it does.
    `openapi.yaml` is generated from these routes and CI fails on drift, and CI
    checks out a tree with no `web/dist` in it, so a route that appeared only
    when the directory did would make the committed document depend on which
    machine generated it.

    503 rather than a 200 carrying an apology. A service whose interface is
    missing is not serving that interface, and the deployed check that fetches
    this path should go red rather than pass on a page of text.
    """
    document = WEB_DIST / "index.html"
    if not document.is_file():
        return PlainTextResponse(NOT_BUILT, status_code=503)
    # no-cache means revalidate, not "do not store". The document names asset
    # files by content hash, so a stale copy of it points a browser at bundles
    # the new deployment no longer has.
    return FileResponse(
        document, media_type="text/html", headers={"Cache-Control": "no-cache"}
    )


# The three URLs that used to render a page here. A redirect rather than a
# deletion: they are in the README, in the recorded demo, and in the comments
# of merged pull requests, and a 404 tells somebody following one of those that
# the thing is gone rather than that it moved.
#
# 302 rather than 301. A browser caches a permanent redirect until its cache is
# cleared, and this interface is a day old; pinning a URL that hard is not a
# claim this build has earned.
_MOVED = 302


@app.get("/thread/view", response_class=RedirectResponse, status_code=_MOVED)
def thread_view_moved() -> RedirectResponse:
    """The thread, now drawn by the application.

    This is the URL the README leads with and the one the recorded demo opens,
    so it keeps working. What it opens is the same graph over the same entries,
    fetched from /thread rather than rendered here.
    """
    return RedirectResponse("/#/thread", status_code=_MOVED)


@app.get("/runs", response_class=RedirectResponse, status_code=_MOVED)
def runs_moved() -> RedirectResponse:
    """What ran and where each run stopped, which the thread screen groups by
    run and shows in the same place."""
    return RedirectResponse("/#/thread", status_code=_MOVED)


@app.get("/fleet", response_class=RedirectResponse, status_code=_MOVED)
def fleet_moved() -> RedirectResponse:
    """The companions and the boundary they sit behind, which the application
    draws as one picture instead of two tables."""
    return RedirectResponse("/#/boundary", status_code=_MOVED)
