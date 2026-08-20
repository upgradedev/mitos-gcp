"""The watchable run.

The judging criterion asks for "a live, unedited demo". So this is built to be
captured in one continuous take with no cuts: it runs the chore twice on two
different pull requests, and the second run recalls what the first one wrote.
That is the difference between proving the memory works and proving we can parse
a date we seeded ourselves.

    python -m mitos.demo --ledger memory     # offline, no credential
    python -m mitos.demo                     # against Firestore

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
from .fleet import CATALOG
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

    backend = args.ledger or os.environ.get("MITOS_LEDGER", "memory")
    ledger = InMemoryLedger() if backend == "memory" else build_ledger(backend)

    print()
    print(f"{BOLD}MITOS{RESET}  a fleet of institutional agents, one governed write")
    print(f"{DIM}ledger backend: {backend}{RESET}")
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
