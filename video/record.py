"""Capture one real run of the demo, with the wall-clock time each line appeared.

This is the honesty layer of the video pipeline. It does not reconstruct or
re-time anything: it runs `mitos.demo` as a subprocess and writes down every byte
it printed and the second it printed it. The renderer then replays exactly that,
at exactly that speed.

So the video is a real-time recording of one complete run. Nothing is cut, no
beat is sped up, and a failure would appear in it.

    python video/record.py --pace 1.6 --out video/run.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 - runs this repo's own demo with fixed argv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def record(pace: float, out: Path, ledger: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # MITOS_MODEL and the project pass through from the environment, so the
    # recorded run can end on the comparison that needs a model. Everything
    # before it stays deterministic, which is what keeps the rejection
    # repeatable on every take.

    cmd = [
        sys.executable,
        "-m",
        "mitos.demo",
        "--ledger",
        ledger,
        "--yes",
        "--pace",
        str(pace),
    ]

    started = time.monotonic()
    lines: list[dict] = []
    proc = subprocess.Popen(  # nosec B603
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if proc.stdout is None:  # pragma: no cover - defensive
        raise RuntimeError("could not capture the demo's stdout")
    for raw in proc.stdout:
        lines.append(
            {"t": round(time.monotonic() - started, 3), "line": raw.rstrip("\n")}
        )
    code = proc.wait()
    duration = round(time.monotonic() - started, 3)

    meta = {
        "command": " ".join(cmd[1:]),
        "exit_code": code,
        "duration_s": duration,
        "line_count": len(lines),
        "ledger": ledger,
        "lines": lines,
    }
    out.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    # A silent fallback would ship the words THIS IS NOT THE REAL SYSTEM in the
    # submission video.
    #
    # `mitos.demo` prints that banner in red and keeps going when it cannot
    # reach Firestore, which is the right behaviour for a person running it and
    # the wrong one for an unattended build: the recording would succeed, the
    # duration checks would pass, and the artifact would open on a red banner
    # nobody looked at until a judge did. Asking for the real ledger and
    # silently getting the other one is exactly the class of defect this project
    # keeps finding in itself.
    if ledger == "firestore":
        # Colour codes stripped first. The header colours the value, so the
        # captured bytes read `ledger ESC[33mfirestore`, and a plain substring
        # test for the two words together fails on a correct recording. Same
        # shape as a claim split across two string literals: what a person
        # reads as one phrase is not one phrase in the text being searched.
        raw = "\n".join(item["line"] for item in lines)
        text = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        if "THIS IS NOT THE REAL SYSTEM" in text:
            # Carry the demo's own reason up. Without it this said "check the
            # credential" while the actual cause was an ImportError, and the
            # capture that held the answer was in a JSON file nobody opens
            # during a failing build.
            why = next(
                (
                    item["line"].strip()
                    for item in lines
                    if "Firestore is unreachable" in item["line"]
                ),
                "no reason line was captured",
            )
            raise SystemExit(
                "asked for the Firestore ledger and the demo fell back to "
                f"memory. The demo said: {why}. The recording would have "
                "shipped the fallback banner."
            )
        if "ledger firestore" not in text:
            raise SystemExit(
                "the recorded header does not say `ledger firestore`, so what "
                "was captured is not what was asked for"
            )

    return meta


def main() -> int:
    ap = argparse.ArgumentParser(prog="record")
    ap.add_argument("--pace", type=float, default=1.6)
    ap.add_argument("--ledger", default="memory")
    ap.add_argument("--out", type=Path, default=ROOT / "video" / "run.jsonl")
    args = ap.parse_args()

    meta = record(args.pace, args.out, args.ledger)
    print(
        f"captured {meta['line_count']} lines in {meta['duration_s']}s "
        f"(exit {meta['exit_code']}) -> {args.out}"
    )
    if meta["exit_code"] != 0:
        print("the run failed; not recording a broken take", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
