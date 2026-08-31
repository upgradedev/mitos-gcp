"""Treat the deployment the way a judge will, and fail if it does not hold up.

Every check here corresponds to a sentence this repository says out loud. If a
sentence has no check, either the sentence goes or the check gets written.

The important property is that it runs against the deployed URLs with **no
credentials at all**, exactly as a stranger would. A readiness gate that
authenticates is testing a different system from the one being judged.

    python scripts/judge_uat.py --base-reader https://... --base-writer https://...

Exit 0 means a judge following the README gets what the README promised.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

TIMEOUT = 90


@dataclass
class Result:
    checks: list[tuple[bool, str, str]] = field(default_factory=list)

    def record(self, ok: bool, name: str, detail: str = "") -> bool:
        self.checks.append((ok, name, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  {mark}  {name}" + (f"\n        {detail}" if detail else ""))
        sys.stdout.flush()
        return ok

    @property
    def failed(self) -> list[tuple[bool, str, str]]:
        return [c for c in self.checks if not c[0]]


def get(url: str, method: str = "GET", body: dict | None = None, token: str | None = None):
    """Fetch, with no credentials unless one is passed.

    Anonymous is the default and the point: most of this suite is what a judge
    with no Google account can see. `token` exists for the handful of checks
    that are about what an authenticated caller gets, which cannot be observed
    any other way now that two of the three services refuse strangers.
    """
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # nosec B310
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def as_json(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def get_with_headers(url: str) -> tuple[int, str, dict[str, str]]:
    """As `get`, and the response headers, lowercased.

    Separate rather than folded into `get` so the twenty calls above keep
    reading as two values.
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # nosec B310
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body, {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body, {k.lower(): v for k, v in exc.headers.items()}
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}", {}


class _KeepTheRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect instead of following it.

    `urlopen` follows by default, and a check that follows cannot tell a
    redirect that lands in the right place from one that lands anywhere at all:
    it would fetch the application shell either way and see a page.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def redirect_of(url: str) -> tuple[int, str]:
    opener = urllib.request.build_opener(_KeepTheRedirect)
    try:
        with opener.open(url, timeout=TIMEOUT) as resp:  # nosec B310
            return resp.status, resp.headers.get("location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("location", "")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _asset(document: str, suffix: str) -> str:
    """The first /assets/... path in the document ending in this suffix.

    Read out of the document rather than written down here, because the file
    name carries a content hash that changes with every build. A name pinned in
    this file would go red on the first rebuild for a reason that is not a
    fault, and a check that cries wolf gets deleted.
    """
    for piece in document.replace("'", '"').split('"'):
        if piece.startswith("/assets/") and piece.endswith(suffix):
            return piece
    return ""


def _id_token(audience: str):
    """An OIDC token for a Cloud Run audience, or None if none can be minted.

    Returns None rather than raising, so this suite still runs for a judge with
    no Google account. What it must never do is pretend the checks that need a
    credential passed.
    """
    try:
        import google.auth.transport.requests  # noqa: PLC0415
        import google.oauth2.id_token  # noqa: PLC0415

        return google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience
        )
    except Exception:
        return None


def run(reader: str, evaluator: str, writer: str) -> int:
    r = Result()
    print("\n== The three identities, as a stranger sees them")

    seen: dict[str, dict] = {}

    # The reader is the public surface, on purpose.
    status, raw = get(f"{reader}/identity")
    seen["reader"] = as_json(raw) or {}
    r.record(status == 200, "reader /identity answers with no account", f"HTTP {status}")
    r.record(
        # `or ""` because running_as is null anywhere off Google Cloud, where
        # there is no metadata server to ask. This used to raise, and a suite
        # that dies on its second check prints no result line at all, which the
        # workflow reads as "the suite printed no result" rather than as the
        # failure it is.
        (seen["reader"].get("running_as") or "").startswith("mitos-reader@"),
        "reader runs as its own service account",
        seen["reader"].get("running_as", raw[:120]),
    )

    # The other two are not, and this is the check that was missing. Both were
    # bound to allUsers, and `POST /execute` on the writer publishes a path, a
    # body and a branch taken from the request, so anonymous invoke on that
    # service was an unauthenticated arbitrary write to the specification
    # repository. Nothing in this suite would have noticed.
    print("\n== The two services a stranger must not reach")
    for name, base in (("evaluator", evaluator), ("writer", writer)):
        status, _ = get(f"{base}/identity")
        r.record(
            status in (401, 403),
            f"{name} refuses an anonymous caller",
            f"HTTP {status}",
        )
    status, _ = get(f"{writer}/execute", method="POST", body={
        "path": "docs/probe.md", "body": "probe", "message": "probe", "branch": "main",
    })
    r.record(
        status in (401, 403),
        "an anonymous write to the writer is refused before it is parsed",
        f"HTTP {status}",
    )

    # The privilege boundary itself can only be read with a credential, which is
    # the point. Announced rather than skipped: a suite that quietly drops half
    # its checks when it cannot authenticate reports green over an untested
    # boundary, and this repository has been bitten by a silently skipping suite
    # before.
    token = _id_token(writer)
    if token is None:
        print(
            "\n== The writer's own view of the boundary: NOT CHECKED.\n"
            "   No credential available here. Run with application default\n"
            "   credentials, or read it from the authenticated CI job."
        )
    else:
        status, raw = get(f"{writer}/identity", token=token)
        seen["writer"] = as_json(raw) or {}
        r.record(status == 200, "writer answers an authenticated caller", f"HTTP {status}")
        r.record(
            seen["writer"].get("running_as", "").startswith("mitos-writer@"),
            "writer runs as its own service account",
            seen["writer"].get("running_as", raw[:120]),
        )

    # The central claim. Not a config flag: the service attempts the access.
    #
    # `seen` is only ever keyed "reader" and "writer". This was written as
    # `for name in [n for n in ("reader", "evaluator") if n in seen]`, which
    # yields exactly ["reader"] and always did, so the evaluator half of the
    # boundary was never checked and nothing said so. A dead branch four lines
    # above a comment warning about dead branches, in the suite whose job is to
    # prove the boundary.
    #
    # The evaluator refuses anonymous callers, which is correct and is why it
    # cannot be read here. That makes it a NOT CHECKED, printed, rather than a
    # silent omission dressed as a loop.
    cred = seen["reader"].get("spec_repo_write_credential", {})
    r.record(
        cred.get("reachable") is False,
        "reader cannot reach the write credential",
        f"{cred.get('detail')}",
    )
    if "evaluator" in seen:
        ecred = seen["evaluator"].get("spec_repo_write_credential", {})
        r.record(
            ecred.get("reachable") is False,
            "evaluator cannot reach the write credential",
            f"{ecred.get('detail')}",
        )
    else:
        print(
            "  NOT CHECKED  evaluator cannot reach the write credential\n"
            "               needs a credential; the evaluator refuses strangers,\n"
            "               which is the same refusal this suite is here to prove"
        )
    # Only asserted when the writer was actually read. Written as
    # `"writer" not in seen or ...` for one commit, which made it pass without
    # looking at anything: a check that cannot fail, reported as a pass, in the
    # suite whose job is to prove the boundary.
    if "writer" in seen:
        wcred = seen["writer"].get("spec_repo_write_credential", {})
        r.record(
            wcred.get("reachable") is True,
            "writer can reach it, so the boundary is a boundary and not an outage",
            str(wcred.get("detail")),
        )
    else:
        print(
            "  NOT CHECKED  writer can reach the credential\n"
            "               needs a credential; the writer no longer answers strangers"
        )

    # The mandatory model requirement, reported by the running service.
    model = seen["reader"].get("model", "")
    r.record(
        model.startswith("gemini-") and model >= "gemini-3.5",
        "the deployed fleet reports Gemini 3.5 or newer",
        model,
    )

    print("\n== The refusal a judge is invited to try")
    status, raw = get(
        f"{reader}/execute",
        "POST",
        {"path": "docs/x.md", "body": "x", "message": "m", "branch": "b"},
    )
    r.record(status == 403, "the reader refuses to write", f"HTTP {status}")
    # Asserts the qualifier, not a fixed sentence. The refusal used to say the
    # reader holds no credential that can write, full stop, and that is false:
    # it holds a GitHub App installation token and posts check runs with it.
    # What it cannot reach is the specification repository's key. Checking for
    # the qualifier is stronger than checking for the old wording, because the
    # old wording could come back and still pass a substring match.
    r.record(
        "specification repository" in raw and "cannot reach" in raw,
        "and says which credential, in words a human can read",
        raw[:140],
    )

    print("\n== The catalogue the router queries")
    status, raw = get(f"{reader}/catalog")
    doc = as_json(raw) or {}
    companions = doc.get("companions", [])
    r.record(status == 200 and len(companions) >= 5, f"{len(companions)} companions listed")
    r.record(
        len({c.get("department") for c in companions}) >= 4,
        "across several departments, which is what cross-department means",
        str(sorted({c.get("department") for c in companions})),
    )

    print("\n== The control plane")
    status, raw = get(f"{reader}/watch")
    doc = as_json(raw) or {}
    r.record(doc.get("subscribed") is True, "the query subscription is open", raw[:120])
    r.record(
        "no scheduler" in doc.get("mechanism", ""),
        "and it is a subscription rather than a poller",
        doc.get("mechanism", ""),
    )

    print("\n== The thread")
    status, raw = get(f"{reader}/thread?limit=5")
    doc = as_json(raw) or {}
    r.record(status == 200, "the provenance thread is readable", f"HTTP {status}")

    print("\n== The API surface")
    status, raw = get(f"{reader}/openapi.json")
    doc = as_json(raw) or {}
    r.record(
        status == 200 and "/identity" in doc.get("paths", {}),
        "the OpenAPI specification is served and describes the real routes",
        f"HTTP {status}, {len(doc.get('paths', {}))} paths",
    )

    # The surface a judge clicks. Left out of this suite once already: the
    # dashboard shipped, the rebuild would have gone green, and nothing here
    # would have noticed a deploy that dropped it. Same shape as the missing
    # MITOS_WRITER_URL, which was caught by luck rather than by a check.
    #
    # It is a built application now, so most of what this section used to do
    # cannot be done: every screen is the same document with a different
    # fragment, and the fragment never reaches the server. Fetching four paths
    # that return the same document and grepping each for a word would be a
    # check that can no longer fail. What replaces it asserts the things that
    # can actually go wrong.
    print("\n== The surface a judge clicks")
    status, shell = get(f"{reader}/")
    r.record(
        status == 200,
        "the interface is served at /",
        f"HTTP {status}" + (" - 503 means the image was built without it" if status == 503 else ""),
    )
    js = _asset(shell, ".js")
    css = _asset(shell, ".css")
    r.record(
        bool(js and css),
        "and the document names a script and a stylesheet",
        f"script={js or 'none'} stylesheet={css or 'none'}",
    )
    # Read out of the document rather than pinned here: the file names carry a
    # content hash that changes with every build.
    bundle = ""
    for kind, path in (("script", js), ("stylesheet", css)):
        if not path:
            continue
        status, body = get(f"{reader}{path}")
        # A truncated asset is served with a 200 and breaks the whole
        # interface, so the size is asserted rather than assumed.
        r.record(
            status == 200 and len(body) > 10000,
            f"the {kind} it names is served and is a real build",
            f"{path}: HTTP {status}, {len(body)} bytes",
        )
        if kind == "script":
            bundle = body

    # Not "the screens render": nothing here drives a browser. This proves the
    # bundle that is deployed is the one carrying each screen's copy, which is
    # what a stale or partial build gets wrong.
    # These phrases are also in `.github/workflows/deployed.yml`, and both lists
    # went stale together when the interface was replaced: the check reported a
    # working deployment as broken, twice, which teaches a reader to ignore it.
    # Two copies is the reason it happened twice. They are here because the
    # workflow cannot import this file, and the honest fix is that this list is
    # the one that gets edited and the workflow's is derived from it by hand
    # until something better exists.
    #
    # Anchored on claims rather than headings. A heading changes for reasons
    # that do not matter; "no write action is simulated" is a promise, and if it
    # leaves the bundle that is worth failing over.
    for screen, phrase in (
        ("value", "Turn every pull request into an explainable change decision."),
        ("policy", "Schema, API, and security checks run deterministically."),
        ("write safety", "No write action is simulated or sent from the browser."),
        ("webhook", "Signatures and delivery IDs are checked server-side."),
    ):
        r.record(
            phrase in bundle,
            f"the bundle carries the {screen} copy",
            phrase,
        )

    # The policy had to be widened for the bundle. A widening nobody checks is
    # a widening that keeps going.
    status, _, headers = get_with_headers(f"{reader}/")
    csp = headers.get("content-security-policy", "")
    for directive in (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ):
        r.record(directive in csp, f"the content policy still says {directive}", "")
    r.record(
        "unsafe-inline" not in csp,
        "and does not allow inline script or style",
        csp[:120],
    )

    # The three URLs that used to render a page here. They are in the README,
    # in the recorded demo and in merged pull request comments, so they redirect
    # rather than 404. Asserted without following: a suite that follows lands on
    # the application shell and passes no matter where the redirect pointed.
    for path, target in (
        ("/thread/view", "#/thread"),
        ("/runs", "#/thread"),
        ("/fleet", "#/boundary"),
    ):
        status, location = redirect_of(f"{reader}{path}")
        r.record(
            status in (301, 302, 307, 308) and location.endswith(target),
            f"{path} redirects into the application",
            f"HTTP {status} -> {location or 'no location'}",
        )

    # The two pages the application does not implement. Nothing replaced them,
    # so they are still rendered by the service.
    for path, must_contain in (
        ("/standards", "the audit"),
        ("/connect", "three steps"),
    ):
        status, raw = get(f"{reader}{path}")
        r.record(
            status == 200 and must_contain in raw,
            f"{path} renders",
            f"HTTP {status}, {len(raw)} bytes",
        )

    status, raw = get(f"{reader}/config")
    cfg = as_json(raw) or {}
    r.record(
        status == 200 and bool(cfg.get("read_scope")) and "max_reads_per_run" in cfg,
        "/config publishes the bounds as values",
        f"scope={cfg.get('read_scope')} reads={cfg.get('max_reads_per_run')}",
    )

    # A typo in the form must not read as the service being broken. This was a
    # 500 until the corpus started validating the name it interpolates.
    status, raw = get(f"{reader}/standards?repository=not-a-repo")
    r.record(
        status == 200 and "audit it" in raw,
        "a bad repository name returns the form and an explanation, not a 500",
        f"HTTP {status}",
    )
    status, _ = get(f"{reader}/standards.json?repository=../../user")
    r.record(
        status == 400,
        "a repository name that could steer a URL is refused",
        f"HTTP {status}",
    )

    status, raw = get(f"{reader}/standards.json")
    summary = (as_json(raw) or {}).get("summary") or {}
    r.record(
        status == 200 and summary.get("rules", 0) > 0,
        "the standards audit runs and returns a verdict per rule",
        f"{summary.get('rules')} rules, {summary.get('failed')} failed",
    )
    # The load-bearing property of that module, asserted against the live
    # service and not only in a unit test: silence is never counted as
    # compliance.
    r.record(
        summary.get("could_not_be_determined", 0) > 0
        and summary.get("passed", 0) + summary.get("failed", 0)
        + summary.get("could_not_be_determined", 0)
        + summary.get("suspected", 0)
        + summary.get("not_applicable", 0)
        <= summary.get("rules", 0),
        "undecided rules are reported as undecided, not folded into the pass count",
        f"{summary.get('could_not_be_determined')} undecided of {summary.get('rules')}",
    )

    print("\n== The chore, streamed, as a judge would watch it")
    # Deliberately streamed rather than awaited. With a model in the loop the
    # whole chore takes minutes, and a judge who posts to a blocking endpoint
    # waits, sees nothing, and concludes it is broken. This check exists because
    # that is exactly what this suite found.
    started = time.time()
    first_beat_at = None
    beats: list[dict] = []
    final: dict = {}
    try:
        req = urllib.request.Request(
            f"{reader}/run/stream",
            data=json.dumps({"pr": 4471, "approve": False, "seed": True}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=900) as resp:  # nosec B310
            for raw_line in resp:
                text = raw_line.decode("utf-8", "replace").strip()
                if not text.startswith("data: "):
                    continue
                beat = json.loads(text[6:])
                if first_beat_at is None:
                    first_beat_at = time.time() - started
                beats.append(beat)
                if beat.get("kind") in ("done", "error"):
                    final = beat
    except Exception as exc:  # noqa: BLE001
        r.record(False, "the stream opened", f"{type(exc).__name__}: {exc}")

    took = time.time() - started
    r.record(bool(beats), f"the run streams ({len(beats)} beats in {took:.0f}s)")

    # The number that decides whether a judge waits or leaves.
    r.record(
        first_beat_at is not None and first_beat_at < 20,
        "the first beat arrives promptly",
        f"{first_beat_at:.1f}s" if first_beat_at is not None else "never",
    )
    r.record(
        final.get("kind") == "done",
        "the run finished without erroring",
        final.get("text", "") or final.get("kind", "no terminal beat"),
    )

    kinds = sorted({b.get("kind") for b in beats})
    r.record("dispatch" in kinds, "the router reported who it woke", str(kinds))
    # Which control fires depends on which specialist produced the draft, and
    # both outcomes are correct.
    #
    # The deterministic specialist quotes the diff faithfully, so it carries the
    # planted credential into the draft and the gate catches it. That is what
    # the recorded demo shows and it is repeatable.
    #
    # The agentic specialist reads the repository and writes its own
    # assessment. It does not quote the config hunk, so the credential never
    # enters the draft and the gate has nothing to say. That is a better
    # outcome, not a missing one.
    #
    # What must be true on both paths is that the gate ran and the interceptor
    # refused the write. Asserting a rejection here would be asserting that the
    # specialist leaked.
    gate_ran = any(b.get("kind") == "evaluate" for b in beats)
    guard_fired = any(
        b.get("kind") == "guard" and "refused" in b.get("text", "") for b in beats
    )
    r.record(gate_ran, "the gate ran on the draft")
    r.record(
        guard_fired,
        "the interceptor refused the write inside the product path",
        next(
            (b["text"].splitlines()[0] for b in beats if b.get("kind") == "guard"), ""
        ),
    )
    r.record(
        final.get("written") is False,
        "nothing is written without approval",
        f"written={final.get('written')}",
    )

    print("\n== Result")
    failed = r.failed
    print(f"  {len(r.checks) - len(failed)}/{len(r.checks)} passed")
    for _, name, detail in failed:
        print(f"  FAILED: {name} :: {detail}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="judge_uat")
    ap.add_argument("--base-reader", required=True)
    ap.add_argument("--base-evaluator", required=True)
    ap.add_argument("--base-writer", required=True)
    args = ap.parse_args()
    return run(
        args.base_reader.rstrip("/"),
        args.base_evaluator.rstrip("/"),
        args.base_writer.rstrip("/"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
