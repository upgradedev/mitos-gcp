"""Build the submission video from a captured run. Runs in CI, never locally.

The input is `run.jsonl`, the byte-for-byte stdout of one real run with the
wall-clock time each line appeared. This module replays exactly that, at exactly
that speed, and lays the narration over it. Nothing is cut and no beat is sped
up, which is what lets the entry call the result an unedited demo.

Single responsibility per stage, and each stage is separately runnable so a
wording fix re-renders one beat instead of the whole cut:

    python video/build.py narrate      # only the changed beats hit the API
    python video/build.py frames
    python video/build.py mux
    python video/build.py all

Narration is cached on a hash of the beat text, so re-running `narrate` after
editing one line costs one API call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 - drives ffmpeg with fixed argv, never a shell
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "video"
BUILD = Path(os.environ.get("MITOS_VIDEO_BUILD", VIDEO / "build"))

WIDTH, HEIGHT = 1600, 900
MARGIN_X, MARGIN_Y = 48, 40
FONT_SIZE = 19
LINE_H = 26
VISIBLE_LINES = (HEIGHT - 2 * MARGIN_Y) // LINE_H
FPS = 10

BG = "0x131316"
FG = "0xd7d4d9"

# The demo's palette, mapped from the ANSI codes it emits. The colours carry
# meaning in this demo (a red finding, a green write), so dropping them to
# monochrome would lose information a judge is being asked to look at.
ANSI = {
    "0": FG,
    "1": "0xffffff",
    "2": "0x8a8790",
    "31": "0xff6b6b",
    "32": "0x5fd75f",
    "33": "0xffd75f",
    "34": "0x7aa2f7",
    "36": "0x5fd7d7",
}

MAX_DURATION_S = 240.0  # "~ 4-min Demo video"
TOLERANCE_S = 1.0 / FPS  # one frame


def font_file() -> str:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ):
        if Path(candidate).exists():
            return candidate
    raise SystemExit("no monospace font found")


def run(cmd: list[str]) -> str:
    # Fixed argv list, shell=False. Nothing here is user supplied.
    proc = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603
    if proc.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd[:4])}…\n{proc.stderr[-2000:]}")
    return proc.stdout


def duration_of(path: Path) -> float:
    out = run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ]
    )
    return float(out.strip())


# --------------------------------------------------------------------------


@dataclass
class Beat:
    id: str
    at: float
    text: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


def load_narration() -> tuple[dict, list[Beat]]:
    cfg = json.loads((VIDEO / "narration.json").read_text(encoding="utf-8"))
    return cfg, [Beat(b["id"], float(b["at"]), b["text"]) for b in cfg["beats"]]


def stage_narrate() -> None:
    """Synthesise only the beats whose text changed."""
    cfg, beats = load_narration()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY is not set")

    import urllib.request  # noqa: PLC0415

    out_dir = BUILD / "vo"
    out_dir.mkdir(parents=True, exist_ok=True)
    made, cached = 0, 0

    for beat in beats:
        target = out_dir / f"{beat.id}.mp3"
        stamp = out_dir / f"{beat.id}.sha"
        if target.exists() and stamp.exists() and stamp.read_text().strip() == beat.digest:
            cached += 1
            continue
        payload = json.dumps(
            {"text": beat.text, "model_id": cfg["model_id"]}
        ).encode("utf-8")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['voice_id']}"
        # The voice id comes from a config file in this repo, so the URL is not
        # attacker controlled, but pinning the scheme means a future edit cannot
        # turn this into a file:// or custom-scheme read.
        if not url.startswith("https://api.elevenlabs.io/"):
            raise SystemExit(f"refusing to call {url!r}")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            target.write_bytes(resp.read())
        stamp.write_text(beat.digest)
        made += 1
        print(f"  synthesised {beat.id} ({len(beat.text)} chars)")

    print(f"narrate: {made} synthesised, {cached} reused from cache")


def stage_check() -> list[tuple[Beat, float]]:
    """Every beat must finish before the next one starts.

    This is the gate that stops a rewritten line silently talking over the next
    one. It fails the build rather than producing a video nobody listens to
    closely enough to catch it.
    """
    cfg, beats = load_narration()
    timed = []
    for beat in beats:
        path = BUILD / "vo" / f"{beat.id}.mp3"
        if not path.exists():
            raise SystemExit(f"missing narration for {beat.id}; run `narrate` first")
        timed.append((beat, duration_of(path)))

    problems = []
    for i, (beat, dur) in enumerate(timed):
        ends = beat.at + dur
        nxt = timed[i + 1][0].at if i + 1 < len(timed) else None
        if nxt is not None and ends > nxt + 0.01:
            problems.append(
                f"  {beat.id}: starts {beat.at:.1f}s, runs {dur:.1f}s, ends "
                f"{ends:.1f}s, but {timed[i + 1][0].id} starts {nxt:.1f}s "
                f"(over by {ends - nxt:.1f}s)"
            )
    if problems:
        raise SystemExit("narration overlaps:\n" + "\n".join(problems))

    total = timed[-1][0].at + timed[-1][1] + cfg["title_card_s"] + cfg["end_card_s"]
    print(f"check: {len(timed)} beats, no overlap, ~{total:.0f}s narration span")
    return timed


# --------------------------------------------------------------------------

ANSI_RX = re.compile(r"\x1b\[([0-9;]*)m")


def split_colours(line: str) -> list[tuple[str, str]]:
    """Turn one ANSI line into (colour, text) runs."""
    runs, pos, colour = [], 0, FG
    for m in ANSI_RX.finditer(line):
        if m.start() > pos:
            runs.append((colour, line[pos : m.start()]))
        codes = [c for c in m.group(1).split(";") if c]
        colour = ANSI.get(codes[-1], FG) if codes else FG
        pos = m.end()
    if pos < len(line):
        runs.append((colour, line[pos:]))
    return [(c, t) for c, t in runs if t]


def stage_frames() -> None:
    """One PNG per screen state, replaying the captured run."""
    meta = json.loads((VIDEO / "run.jsonl").read_text(encoding="utf-8"))
    frames_dir = BUILD / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    txt_dir = BUILD / "txt"
    txt_dir.mkdir(parents=True, exist_ok=True)
    font = font_file()

    # Advance in 0.4 char widths so the monospace grid lines up regardless of
    # what the font reports. Consolas and DejaVu Sans Mono are both 0.6 em.
    char_w = FONT_SIZE * 0.6

    screen: list[str] = []
    written = []
    for idx, entry in enumerate(meta["lines"]):
        screen.append(entry["line"])
        visible = screen[-VISIBLE_LINES:]

        filters = []
        for row, raw in enumerate(visible):
            col = 0
            for run_i, (colour, text) in enumerate(split_colours(raw)):
                tf = txt_dir / f"r{row}_{run_i}.txt"
                tf.write_text(text, encoding="utf-8")
                filters.append(
                    f"drawtext=fontfile='{font}'"
                    f":textfile='{tf.as_posix()}'"
                    f":expansion=none"
                    f":fontcolor={colour}"
                    f":fontsize={FONT_SIZE}"
                    f":x={MARGIN_X + int(col * char_w)}"
                    f":y={MARGIN_Y + row * LINE_H}"
                )
                col += len(text)

        vf = ",".join(filters) if filters else "null"
        out = frames_dir / f"f{idx:05d}.png"
        run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", f"color=c={BG}:s={WIDTH}x{HEIGHT}",
                "-vf", vf, "-frames:v", "1", str(out),
            ]
        )
        written.append((out, entry["t"]))

    # Per-frame durations come from the capture, not from a constant.
    concat = ["ffconcat version 1.0"]
    for i, (path, t) in enumerate(written):
        nxt = written[i + 1][1] if i + 1 < len(written) else t + 2.0
        concat.append(f"file '{path.as_posix()}'")
        concat.append(f"duration {max(0.05, round(nxt - t, 3))}")
    concat.append(f"file '{written[-1][0].as_posix()}'")
    (BUILD / "frames.ffconcat").write_text("\n".join(concat), encoding="utf-8")

    shutil.rmtree(txt_dir, ignore_errors=True)
    print(f"frames: {len(written)} screens, run length {meta['duration_s']:.1f}s")


# --------------------------------------------------------------------------


def _card(text_lines: list[str], out: Path, seconds: float) -> None:
    font = font_file()
    txt_dir = BUILD / "card"
    txt_dir.mkdir(parents=True, exist_ok=True)
    filters = []
    top = HEIGHT // 2 - (len(text_lines) * 44) // 2
    for i, line in enumerate(text_lines):
        tf = txt_dir / f"c{i}.txt"
        tf.write_text(line, encoding="utf-8")
        filters.append(
            f"drawtext=fontfile='{font}':textfile='{tf.as_posix()}'"
            f":expansion=none:fontcolor={'0xffffff' if i == 0 else '0x8a8790'}"
            f":fontsize={34 if i == 0 else 21}:x=(w-tw)/2:y={top + i * 44}"
        )
    run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={BG}:s={WIDTH}x{HEIGHT}:d={seconds}:r={FPS}",
            "-vf", ",".join(filters), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-t", str(seconds), str(out),
        ]
    )
    shutil.rmtree(txt_dir, ignore_errors=True)


def stage_mux() -> None:
    cfg, _ = load_narration()
    timed = stage_check()
    lead = float(cfg["title_card_s"])

    body = BUILD / "body.mp4"
    run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(BUILD / "frames.ffconcat"),
            "-vsync", "vfr", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", str(body),
        ]
    )

    title = BUILD / "title.mp4"
    _card(
        [
            "MITOS",
            "a fleet of institutional agents, one governed write",
            "All Things Agentic  ·  The Fortified Enterprise Fleet",
        ],
        title,
        lead,
    )
    end = BUILD / "end.mp4"
    _card(
        [
            "github.com/upgradedev/mitos-gcp",
            "three Cloud Run services, three service accounts",
            "the reader holds no credential that can write",
        ],
        end,
        float(cfg["end_card_s"]),
    )

    silent = BUILD / "silent.mp4"
    lst = BUILD / "parts.txt"
    lst.write_text(
        "\n".join(
            f"file '{p.as_posix()}'" for p in (title, body, end)
        ),
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(lst), "-c", "copy", str(silent),
        ]
    )

    # Narration is delayed to its anchor, offset by the title card, then mixed.
    inputs, filters, labels = [], [], []
    for i, (beat, _dur) in enumerate(timed):
        inputs += ["-i", str(BUILD / "vo" / f"{beat.id}.mp3")]
        delay_ms = int((beat.at + lead) * 1000)
        filters.append(f"[{i + 1}:a]adelay={delay_ms}|{delay_ms}[a{i}]")
        labels.append(f"[a{i}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(timed)}:normalize=0[mix]")

    out = BUILD / "mitos-demo.mp4"
    run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(silent), *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "0:v", "-map", "[mix]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            str(out),
        ]
    )

    v = duration_of(out)
    print(f"mux: {out.name}, {v:.2f}s, {out.stat().st_size / 1_000_000:.1f} MB")
    if v > MAX_DURATION_S:
        raise SystemExit(
            f"video is {v:.1f}s, over the {MAX_DURATION_S:.0f}s cap. Shorten the "
            f"run pace or the narration; do not raise the cap."
        )


def stage_verify() -> None:
    """Assert on the shipped pixels, not on the inputs that made them."""
    out = BUILD / "mitos-demo.mp4"
    if not out.exists():
        raise SystemExit("no video built")
    streams = json.loads(
        run(
            [
                "ffprobe", "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(out),
            ]
        )
    )
    kinds = {s["codec_type"]: s for s in streams["streams"]}
    if "video" not in kinds:
        raise SystemExit("no video stream")
    if "audio" not in kinds:
        raise SystemExit("no audio stream; the narration did not make it in")

    total = float(streams["format"]["duration"])
    a = float(kinds["audio"].get("duration", total))
    v = float(kinds["video"].get("duration", total))
    print(f"verify: video {v:.2f}s, audio {a:.2f}s, total {total:.2f}s")
    if total > MAX_DURATION_S:
        raise SystemExit(f"over the {MAX_DURATION_S:.0f}s cap at {total:.1f}s")
    if abs(a - v) > 2.0:
        raise SystemExit(f"audio and video differ by {abs(a - v):.2f}s")
    if kinds["video"]["width"] != WIDTH:
        raise SystemExit("unexpected frame width")
    print("verify: OK")


STAGES = {
    "narrate": stage_narrate,
    "check": stage_check,
    "frames": stage_frames,
    "mux": stage_mux,
    "verify": stage_verify,
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="build")
    ap.add_argument("stage", choices=[*STAGES, "all"])
    args = ap.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    order = ["narrate", "frames", "mux", "verify"] if args.stage == "all" else [args.stage]
    for name in order:
        print(f"== {name}")
        STAGES[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
