"""The watchable run.

The judging criterion asks for "a live, unedited demo". So this is built to be
captured in one continuous take with no cuts: it runs the chore twice on two
different pull requests, and the second run recalls what the first one wrote.
That is the difference between proving the memory works and proving we can parse
a date we seeded ourselves.

    python -m mitos.demo        # the real system: Firestore and Gemini
    python -m mitos.demo --ledger memory   # what CI runs, and it says so on screen

The default is the real thing, deliberately. A demo that quietly falls back to an
in-memory ledger shows a stub and nobody watching can tell, which is worse than
failing. When it cannot reach Firestore it says THIS IS NOT THE REAL SYSTEM in
red and points at the deployed URL.

Pacing exists so a human can read it. `--fast` removes it for CI.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from typing import Optional

from .batch import run_batch
from .chore import ApprovalCard, run_chore
from .fixtures import BACKLOG, PR_4471, PR_4472, SEEDED_HISTORY
from .fleet import CATALOG, route, route_with_model, run_specialist
from .gemini import build_agentic_analyst, build_classifier
from .ledger import Entry, InMemoryLedger, Ledger, build_ledger

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"

STYLE = {
    "trigger": (CYAN, "TRIGGER"),
    "signal": (DIM, "  signal"),
    "dispatch": (BLUE, "DISPATCH"),
    "recall": (YELLOW, "RECALL"),
    "escalate": (YELLOW, "ESCALATE"),
    "engine": (DIM, "  engine"),
    "specialist": (DIM, "  agent"),
    "evaluate": (BLUE, "GATE"),
    "finding": (RED, "  finding"),
    "repair": (YELLOW, "REPAIR"),
    "approval": (BOLD, "APPROVAL"),
    "identity": (DIM, "  identity"),
    "write": (GREEN, "WRITE"),
    "divergence": (BLUE, "DIVERGE"),
    "guard": (RED, "GUARD"),
    "parked": (YELLOW, "PARKED"),
    "halt": (RED, "HALT"),
}

_PACE = 0.7


def _emit(kind: str, text: str) -> None:
    colour, label = STYLE.get(kind, ("", kind.upper()))
    first, *rest = text.split("\n")
    print(f"{colour}{label:>10}{RESET}  {first}")
    for line in rest:
        print(f"{'':>10}  {line}")
    sys.stdout.flush()
    time.sleep(_PACE)


def _rule(title: str = "") -> None:
    if title:
        print(f"\n{BOLD}{'─' * 4} {title} {'─' * max(4, 68 - len(title))}{RESET}")
    else:
        print(f"{DIM}{'─' * 74}{RESET}")
    sys.stdout.flush()
    time.sleep(_PACE)


def _print_catalog() -> None:
    _rule("THE CATALOG, queried by the router to decide who wakes")
    for c in CATALOG:
        print(
            f"  {c.name:<26} {DIM}{c.department:<16}{RESET} "
            f"{DIM}wakes on {', '.join(c.wakes_on)}{RESET}"
        )
    sys.stdout.flush()
    time.sleep(_PACE)


def _seed(ledger: Ledger) -> None:
    """Plant the July deferral. Labelled synthetic wherever it is shown."""
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


def _approver(auto: bool):
    def approve(card: ApprovalCard) -> bool:
        print()
        print(f"{BOLD}  ┌─ approve this write ─────────────────────────────────────┐{RESET}")
        print(f"  │ target   {card.target_path:<47}│")
        print(f"  │ sha256   {card.plan_hash[:48]:<48}│")
        print(f"  │ findings {str(len(card.findings)) + ' new':<47}│")
        if card.advisories:
            print(
                f"  │ advisory {str(len(card.advisories)) + ' from the model critic':<47}│"
            )
            for a in card.advisories[:2]:
                print(f"  │   {a[:53]:<53}│")
        print(f"{BOLD}  └──────────────────────────────────────────────────────────┘{RESET}")
        sys.stdout.flush()
        if auto:
            time.sleep(_PACE * 2)
            print(f"  {GREEN}approved{RESET} {DIM}(--yes){RESET}\n")
            return True
        answer = input("  approve? [y/N] ").strip().lower()
        print()
        return answer == "y"

    return approve


def main(argv: Optional[list[str]] = None) -> int:
    global _PACE
    ap = argparse.ArgumentParser(prog="mitos.demo")
    ap.add_argument("--ledger", choices=("memory", "firestore"), default=None)
    ap.add_argument("--yes", action="store_true", help="auto-approve the write")
    ap.add_argument("--fast", action="store_true", help="no pacing, for CI")
    ap.add_argument(
        "--pace",
        type=float,
        default=None,
        help="seconds between beats. The recording uses this so the captured "
        "run is real time and needs no speed-up in the edit.",
    )
    ap.add_argument("--today", default="2026-08-19")
    args = ap.parse_args(argv)

    if args.fast:
        _PACE = 0.0
    elif args.pace is not None:
        _PACE = args.pace

    # A Windows console defaults to cp1252 and dies on the box-drawing
    # characters below. A judge following the spin-up instructions on Windows
    # would hit this before seeing a single line of the demo.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover - exotic streams
            pass

    # The default is the real thing. A demo that quietly runs on a stub is a
    # demo that shows a stub, and nobody watching can tell the difference, which
    # is the worst of both.
    backend = args.ledger or os.environ.get("MITOS_LEDGER", "firestore")
    ledger: Ledger
    if backend == "memory":
        ledger = InMemoryLedger()
    else:
        try:
            ledger = build_ledger(backend)
        except Exception as exc:
            # Loudly, and not as a footnote. Falling back in silence is how a
            # stub gets mistaken for the product.
            print()
            print(f"{RED}{BOLD}  THIS IS NOT THE REAL SYSTEM{RESET}")
            print(f"  Firestore is unreachable: {type(exc).__name__}")
            print(f"  {DIM}Running on an in-memory ledger instead. The fleet logic is")
            print(f"  identical, the control plane is not: there is no query")
            print(f"  subscription, so nothing wakes on its own.{RESET}")
            print()
            print(f"  {DIM}For the real system, open:{RESET}")
            print(f"  https://mitos-reader-437828525303.europe-west1.run.app/thread/view")
            print()
            backend = "memory (fallback)"
            ledger = InMemoryLedger()

    print()
    print(f"{BOLD}MITOS{RESET}  a fleet of institutional agents, one governed write")
    engine = os.environ.get("MITOS_MODEL", "stub")
    marker = GREEN if backend.startswith("firestore") else YELLOW
    print(f"{DIM}ledger {marker}{backend}{RESET}{DIM}  ·  model {marker}{engine}{RESET}")
    if engine == "stub":
        print(
            f"  {YELLOW}no model configured, so the specialists use templates.{RESET}\n"
            f"  {DIM}Set MITOS_MODEL=gemini-3.7-flash for the agentic path.{RESET}"
        )
    _print_catalog()

    _rule("SEED, synthetic history so the fleet has weeks to remember")
    _seed(ledger)
    print(
        f"  {DIM}one deferral from 2026-07-29, expiring 2026-08-12. "
        f"SYNTHETIC SEED DATA.{RESET}"
    )
    sys.stdout.flush()
    time.sleep(_PACE)

    approve = _approver(args.yes)

    _rule("RUN 1  nobody opened Mitos; a webhook did this")
    first = run_chore(
        PR_4471, ledger, run_id=uuid.uuid4().hex[:8],
        emit=_emit, approve=approve, today=args.today,
    )

    _rule("RUN 2  a different PR, same subject, minutes later")
    second = run_chore(
        PR_4472, ledger, run_id=uuid.uuid4().hex[:8],
        emit=_emit, approve=approve, today=args.today,
    )

    _rule("THE REST OF THE MORNING, ten more pull requests, unattended")
    rest = [pr for pr in BACKLOG if pr.number not in (PR_4471.number, PR_4472.number)]
    report = run_batch(rest, ledger, approve=lambda card: True, today=args.today)
    for o in report.outcomes:
        if o.state == "parked":
            print(f"  {YELLOW}parked   {RESET} PR {o.pr_number}  {o.title[:44]}")
            print(f"           {DIM}{o.parked_by}: {o.reason[:96]}{RESET}")
        elif o.state == "no_action":
            print(f"  {DIM}no action{RESET} PR {o.pr_number}  {o.title[:44]}")
        else:
            print(f"  {GREEN}completed{RESET} PR {o.pr_number}  {o.title[:44]}")
        sys.stdout.flush()
        time.sleep(_PACE / 3)

    # The closing argument, and the only part of this demo that needs a model.
    #
    # PR 4483 completed in the backlog above. That is the honest deterministic
    # result and it is also the failure: the column is called `vuln_code`, no
    # pattern matches it, and only a comment says it holds medical dependency
    # data. A rules-only fleet ships health data.
    _rule("THE ONE THE RULES CANNOT SEE")
    target = [pr for pr in BACKLOG if pr.number == 4483][0]
    print(f"  PR {target.number}  {target.title}")
    print(f"  {DIM}the column is called vuln_code. Only a comment says what it holds.{RESET}")
    sys.stdout.flush()
    time.sleep(_PACE)

    rules_only = route(target)
    print(f"  {RED}rules alone {RESET} compliance-companion "
          f"{'woken' if 'compliance-companion' in rules_only.woken else 'NEVER WOKEN'}"
          f"{DIM}, so the change ships{RESET}")
    sys.stdout.flush()
    time.sleep(_PACE)

    classifier = build_classifier(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    analyst = build_agentic_analyst(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    if classifier is None:
        print(f"  {DIM}with the model  not run here; no model configured.{RESET}")
        print(f"  {DIM}                proven in CI: tests/integration/test_gemini_live.py{RESET}")
    else:
        widened, divergence = route_with_model(target, classifier)
        print(f"  {GREEN}with the model{RESET} the router widened: "
              f"added {divergence.get('model_added')}, Article 9 "
              f"{divergence.get('special_category')}")
        sys.stdout.flush()
        if "compliance-companion" in widened.woken:
            out = run_specialist(
                "compliance-companion", target, widened.signals, analyst=analyst
            )
            print(f"  {GREEN}              {RESET} compliance opened "
                  f"{out.read_log.get('reads', 0)} file(s) and returned "
                  f"{BOLD}{out.status.value}{RESET}")
            for line in (out.reason or "")[:150].split(". ")[:2]:
                if line.strip():
                    print(f"                {DIM}{line.strip()}{RESET}")
    sys.stdout.flush()
    time.sleep(_PACE)

    _rule("THE THREAD, walked back from the last entry to the diff that caused it")
    for entry in ledger.thread(second.last_entry_id):
        print(
            f"  {DIM}{entry.recorded_at[11:19]}{RESET}  "
            f"{entry.kind:<24} {DIM}{entry.actor}{RESET}"
        )
    sys.stdout.flush()

    _rule("THE COUNT")
    total = report.presented + 2
    print(f"  {BOLD}{total} presented{RESET}, "
          f"{BOLD}{report.completed + 2} completed unattended{RESET}, "
          f"{BOLD}{report.parked} parked for a human{RESET}, "
          f"{report.no_action} needed nothing")
    print(f"  {DIM}human interventions before the approval step: 0{RESET}")
    print()
    for label, r in (("run 1", first), ("run 2", second)):
        state = "published" if r.published else "approved, not published"
        detail = r.receipt.get("compare") or r.receipt.get("reason", "")
        print(f"  {label}: governed write {state}")
        if detail:
            print(f"         {DIM}{detail}{RESET}")
    print(
        f"  gate rejected run 1 draft on "
        f"{len(first.first_verdict.findings)} finding(s); "
        f"run 2 recalled {len(second.recalled)} prior entr(ies)"
    )
    print(f"  ledger entries: {len(ledger.all())}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
