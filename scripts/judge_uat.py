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


def get(url: str, method: str = "GET", body: dict | None = None):
    """Fetch with no credentials, which is the whole point."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
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


def run(reader: str, evaluator: str, writer: str) -> int:
    r = Result()
    print("\n== The three identities, as a stranger sees them")

    seen: dict[str, dict] = {}
    for name, base in (("reader", reader), ("evaluator", evaluator), ("writer", writer)):
        status, raw = get(f"{base}/identity")
        doc = as_json(raw) or {}
        seen[name] = doc
        r.record(status == 200, f"{name} /identity answers", f"HTTP {status}")
        r.record(
            doc.get("running_as", "").startswith(f"mitos-{name}@"),
            f"{name} runs as its own service account",
            doc.get("running_as", raw[:120]),
        )

    # The central claim. Not a config flag: the service attempts the access.
    for name in ("reader", "evaluator"):
        cred = seen[name].get("spec_repo_write_credential", {})
        r.record(
            cred.get("reachable") is False,
            f"{name} cannot reach the write credential",
            f"{cred.get('detail')}",
        )
    wcred = seen["writer"].get("spec_repo_write_credential", {})
    r.record(
        wcred.get("reachable") is True,
        "writer can reach it, so the boundary is a boundary and not an outage",
        str(wcred.get("detail")),
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
    r.record(
        "no credential that can write" in raw,
        "and says why in words a human can read",
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
    status, raw = get(f"{reader}/thread/view")
    r.record(
        status == 200 and "<title>" in raw,
        "the thread view renders, which is the one thing here worth looking at",
        f"HTTP {status}",
    )

    print("\n== The API surface")
    status, raw = get(f"{reader}/openapi.json")
    doc = as_json(raw) or {}
    r.record(
        status == 200 and "/identity" in doc.get("paths", {}),
        "the OpenAPI specification is served and describes the real routes",
        f"HTTP {status}, {len(doc.get('paths', {}))} paths",
    )

    # The pages a judge actually clicks. Left out of this suite once already:
    # the dashboard shipped, the rebuild would have gone green, and nothing here
    # would have noticed a deploy that dropped it. Same shape as the missing
    # MITOS_WRITER_URL, which was caught by luck rather than by a check.
    print("\n== The surface a judge clicks")
    for path, must_contain in (
        ("/", "the privilege boundary"),
        ("/fleet", "catalogued companions"),
        ("/runs", "this window, counted"),
        ("/standards", "the audit"),
        ("/connect", "three steps"),
        ("/thread/view", "Mitos"),
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
