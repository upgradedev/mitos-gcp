"""The opening and the closing, drawn frame by frame.

The demo recording is the middle of the video and it is the honest part: a real
run, captured, replayed at its own pace. What was missing either side of it was
a reason to keep watching and a reason to remember, and a static title card is
neither.

Drawn with Pillow rather than with ffmpeg's `drawtext`, for two reasons. It can
be rendered and inspected on any machine, including a Windows one where the
drive letter in a `textfile=` path makes ffmpeg's filter parser fail. And a
frame is a function of time here, so a fade or a slide is arithmetic rather than
a filter graph nobody can read.

Each function returns a list of `(image, seconds)`, which `build.py` turns into
an ffconcat with per-frame durations, exactly the way it already handles the
captured terminal frames.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1600, 900
FPS = 10

BG = (19, 19, 22)
INK = (215, 212, 217)
DIM = (138, 135, 144)
WHITE = (255, 255, 255)
CYAN = (95, 215, 215)
BLUE = (122, 162, 247)
AMBER = (255, 215, 95)
GREEN = (115, 209, 61)
RED = (255, 107, 107)
EDGE = (50, 50, 60)

SEGOE = "C:/Windows/Fonts/segoeui.ttf"
SEGOE_B = "C:/Windows/Fonts/segoeuib.ttf"
MONO = "C:/Windows/Fonts/consola.ttf"

# CI is Linux and has DejaVu; a developer machine is usually Windows and has
# Segoe. Neither is required: the last entry always exists.
FALLBACKS = {
    SEGOE: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    SEGOE_B: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    MONO: ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
}


def font(path: str, size: int):
    for candidate in [path, *FALLBACKS.get(path, [])]:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _mix(a, b, t: float):
    """Blend two colours. `t` of 0 gives `a`, 1 gives `b`."""
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _ease(t: float) -> float:
    """Ease out cubic. Linear motion is what makes a fade look cheap."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _text(d, xy, s, f, colour, alpha: float = 1.0):
    """Text faded towards the background, which is how you fade without an
    alpha channel on an opaque frame."""
    if alpha <= 0.01:
        return
    d.text(xy, s, font=f, fill=_mix(BG, colour, alpha))


def _centre(d, s, cy_x, y, f, colour, alpha: float = 1.0):
    w = d.textbbox((0, 0), s, font=f)[2]
    _text(d, (cy_x - w / 2, y), s, f, colour, alpha)


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------

F_HERO = font(SEGOE_B, 92)
F_LEAD = font(SEGOE, 34)
F_BODY = font(SEGOE, 28)
F_SMALL = font(SEGOE, 19)
F_MONO = font(MONO, 22)
F_TAG = font(SEGOE_B, 16)


def opening_frames(seconds: float = 14.0) -> list[tuple[Image.Image, float]]:
    """The problem first, then the name, timed to the voice.

    The first narration beat is: "A schema change ships on Tuesday. In March, a
    regulator asks who approved it. This is Mitos, the fleet that answers that."

    So the title does not open the video. The problem does, and the name arrives
    on the sentence that says it, roughly six seconds in. A title card that
    announces the product before the viewer has a reason to care is the thing
    every one of these videos does, and it is why nobody watches past ten
    seconds.
    """
    step = 1.0 / FPS
    frames: list[tuple[Image.Image, float]] = []
    n = int(seconds * FPS)

    # (start, text, colour, size) against the narration above.
    PROBLEM = [
        (0.4, "A schema change ships on Tuesday.", INK),
        (2.6, "In March, a regulator asks who approved it,", INK),
        (3.8, "on what evidence, and whether anyone checked.", INK),
        (5.0, "The commit is there. The reasoning is not.", AMBER),
    ]

    for i in range(n):
        t = i * step
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        d = ImageDraw.Draw(img)

        # The problem, one line at a time, high on the frame so the title has
        # somewhere to arrive.
        for k, (start, line, colour) in enumerate(PROBLEM):
            if t < start:
                continue
            p = _ease((t - start) / 0.8)
            # Everything lifts out of the way when the name lands.
            lift = _ease((t - 6.2) / 1.1) * 150 if t > 6.2 else 0.0
            y = 250 + k * 48 - lift + (1 - p) * 10
            fade = p * (1 - 0.45 * (_ease((t - 6.2) / 1.1) if t > 6.2 else 0))
            _centre(d, line, WIDTH / 2, y, F_BODY, colour, fade)

        # The name, on the sentence that says it.
        if t > 6.2:
            p = _ease((t - 6.2) / 1.0)
            _centre(d, "Mitos", WIDTH / 2, 400 + (1 - p) * 18, F_HERO, WHITE, p)
        if t > 7.4:
            w = int(_ease((t - 7.4) / 1.2) * 760)
            d.line([(WIDTH - w) // 2, 520, (WIDTH + w) // 2, 520], fill=EDGE, width=2)
        if t > 7.8:
            _centre(d, "change governance for a fleet of agents", WIDTH / 2, 546,
                    F_LEAD, DIM, _ease((t - 7.8) / 1.0))

        # Three identities, arriving one at a time, because the three identities
        # are the argument rather than decoration.
        if t > 9.6:
            for k, (label, colour) in enumerate([
                ("mitos-reader", CYAN),
                ("mitos-evaluator", BLUE),
                ("mitos-writer", GREEN),
            ]):
                start = 9.6 + k * 0.42
                if t < start:
                    continue
                p = _ease((t - start) / 0.7)
                x = 370 + k * 290
                y = 660 + (1 - p) * 14
                d.rounded_rectangle([x, y, x + 250, y + 54], radius=10,
                                    outline=_mix(BG, colour, p * 0.9), width=2)
                w = d.textbbox((0, 0), label, font=F_SMALL)[2]
                _text(d, (x + 125 - w / 2, y + 16), label, F_SMALL, colour, p)

        if t > 11.6:
            p = _ease((t - 11.6) / 0.8)
            _centre(d, "three services, three identities, one governed write",
                    WIDTH / 2, 748, F_SMALL, DIM, p)

        frames.append((img, step))

    return frames


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


def closing_frames(seconds: float = 13.0) -> list[tuple[Image.Image, float]]:
    """What was just shown, and where to go and check it.

    Deliberately not a summary of features. Four claims a stranger can verify
    without an account, and the address of each one.
    """
    step = 1.0 / FPS
    frames: list[tuple[Image.Image, float]] = []
    n = int(seconds * FPS)

    claims = [
        (CYAN, "Nobody opened it", "a GitHub App webhook woke the fleet"),
        (BLUE, "The gate is a control, not a prompt",
         "a deterministic verdict, in its own service, under its own identity"),
        (AMBER, "Two Google model families",
         "Gemini 3.7 Flash routes and reads. Gemma 4 26B reviews, and cannot approve"),
        (GREEN, "One governed write, behind a human",
         "addressed by its own sha256, refused to every agent in the fleet"),
    ]

    for i in range(n):
        t = i * step
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        d = ImageDraw.Draw(img)

        _text(d, (150, 96), "What you just watched", F_LEAD, WHITE, _ease(t / 0.8))
        if t > 0.5:
            w = int(_ease((t - 0.5) / 1.2) * 1300)
            d.line([150, 156, 150 + w, 156], fill=EDGE, width=2)

        for k, (colour, head, sub) in enumerate(claims):
            start = 0.9 + k * 0.55
            if t < start:
                continue
            p = _ease((t - start) / 0.8)
            y = 210 + k * 118 + (1 - p) * 12
            d.rounded_rectangle([150, y, 158, y + 66], radius=4,
                                fill=_mix(BG, colour, p))
            _text(d, (188, y + 2), head, F_BODY, _mix(DIM, INK, p), p)
            _text(d, (188, y + 40), sub, F_SMALL, DIM, p)

        if t > 3.6:
            p = _ease((t - 3.6) / 1.0)
            d.rounded_rectangle([150, 700, 1450, 800], radius=12,
                                outline=_mix(BG, EDGE, p), width=2)
            _text(d, (182, 722), "github.com/upgradedev/mitos-gcp", F_MONO,
                  _mix(DIM, WHITE, p), p)
            _text(d, (182, 756),
                  "mitos-reader-437828525303.europe-west1.run.app/identity",
                  F_MONO, _mix(DIM, CYAN, p), p)

        if t > 5.2:
            p = _ease((t - 5.2) / 1.0)
            _text(d, (150, 836),
                  "Every claim in the README carries the command that produced it.",
                  F_SMALL, DIM, p)

        frames.append((img, step))

    return frames


def write(frames, into: Path, prefix: str) -> list[tuple[Path, float]]:
    into.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (img, dur) in enumerate(frames):
        p = into / f"{prefix}{i:05d}.png"
        img.save(p, "PNG")
        out.append((p, dur))
    return out


# ---------------------------------------------------------------------------
# The Google Cloud stills
# ---------------------------------------------------------------------------

# What each capture is, in the viewer's words rather than the filename's. A
# console screenshot with no caption is a wall of somebody else's UI, and the
# thing it is meant to prove goes unread.
CAPTIONS = {
    "01-cloud-run": ("Cloud Run, europe-west1",
                     "three services, deployed, serving traffic"),
    "02-reader-revisions": ("mitos-reader, revisions",
                            "the running revision, its URL, and the image it was built from"),
    "03-service-accounts": ("IAM service accounts",
                            "a separate identity per service, and the CI identity that holds no keys"),
    "04-model-garden": ("Vertex AI Model Garden",
                        "Gemma 4, served as a managed open model in this project"),
    "05-firestore-ledger": ("Firestore, the provenance thread",
                            "every trigger, dispatch, finding, verdict and approval, appended"),
    "06-cloud-build": ("Cloud Build",
                       "the image, with the commit baked into it"),
    "07-app": ("The product",
               "served by the reader service, from the same container"),
    "08-check-run": ("The check on a real pull request",
                     "posted by the deployed fleet, under a scoped installation token"),
}

F_CAP = font(SEGOE_B, 30)
F_CAPSUB = font(SEGOE, 21)


def stills_frames(path: Path, seconds: float, fps: int = FPS):
    """One console capture, zoomed slowly, with a caption band under it.

    The zoom is not decoration. A 2356 pixel wide console screenshot scaled to
    fit a 1600 pixel frame puts its text at about nine pixels, which is not
    readable, and a still that cannot be read proves nothing. Cropping a window
    out of the original and growing it keeps real pixels on screen instead of
    enlarging blurry ones.
    """
    src = Image.open(path).convert("RGB")
    n = max(1, int(seconds * fps))
    band = 96          # caption height
    view_h = HEIGHT - band

    key = path.stem
    head, sub = CAPTIONS.get(key, (key.replace("-", " "), ""))

    out = []
    for i in range(n):
        p = i / max(1, n - 1)
        # Ease so it settles rather than arriving at full speed at the cut.
        zoom = 1.0 + 0.20 * _ease(p)

        w = int(src.width / zoom)
        h = int(src.height / zoom)
        # Drift towards the upper middle, which is where console content lives
        # and where the left navigation is not.
        cx = src.width / 2 + (src.width * 0.04) * _ease(p)
        cy = src.height * 0.42
        left = max(0, min(src.width - w, int(cx - w / 2)))
        top = max(0, min(src.height - h, int(cy - h / 2)))
        crop = src.crop((left, top, left + w, top + h))

        scale = min(WIDTH / crop.width, view_h / crop.height)
        crop = crop.resize((max(1, int(crop.width * scale)),
                            max(1, int(crop.height * scale))), Image.LANCZOS)

        frame = Image.new("RGB", (WIDTH, HEIGHT), BG)
        frame.paste(crop, ((WIDTH - crop.width) // 2, (view_h - crop.height) // 2))

        d = ImageDraw.Draw(frame)
        d.line([0, view_h, WIDTH, view_h], fill=EDGE, width=2)
        alpha = _ease(min(1.0, i / (fps * 0.5)))
        _text(d, (60, view_h + 16), head, F_CAP, WHITE, alpha)
        _text(d, (60, view_h + 56), sub, F_CAPSUB, DIM, alpha)
        out.append((frame, 1.0 / fps))
    return out
