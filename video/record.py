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
