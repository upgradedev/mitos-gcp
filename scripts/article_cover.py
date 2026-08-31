"""Render the cover image for the write-up.

    python scripts/article_cover.py

Writes `docs/article-cover.png` at 1000x420, which is the ratio dev.to crops to.

Same palette and the same reason as `scripts/architecture_diagram.py`: drawn
from code so it can be corrected in a commit rather than reopened in an editor.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "article-cover.png"

W, H = 1000, 420

BG = "#131316"
INK = "#d7d4d9"
DIM = "#8a8790"
CYAN = "#5fd7d7"
BLUE = "#7aa2f7"
AMBER = "#ffd75f"
GREEN = "#73d13d"
RED = "#ff6b6b"
WHITE = "#ffffff"
EDGE = "#32323c"

SEGOE = "C:/Windows/Fonts/segoeui.ttf"
SEGOE_B = "C:/Windows/Fonts/segoeuib.ttf"
MONO = "C:/Windows/Fonts/consola.ttf"


def font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:  # pragma: no cover
        return ImageFont.load_default()


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # A faint grid, so the ground is not a flat rectangle.
    for x in range(0, W, 25):
        d.line([x, 0, x, H], fill="#17171c", width=1)
    for y in range(0, H, 25):
        d.line([0, y, W, y], fill="#17171c", width=1)

    d.text((56, 52), "Mitos", font=font(SEGOE_B, 62), fill=WHITE)
    d.text((58, 130), "A fleet of agents that cannot write anything.",
           font=font(SEGOE, 27), fill=INK)
    d.text((58, 168), "That is the feature.", font=font(SEGOE_B, 27), fill=AMBER)

    d.line([58, 218, 560, 218], fill=EDGE, width=1)

    for i, (colour, text) in enumerate([
        (CYAN, "a pull request wakes five specialists"),
        (BLUE, "a deterministic gate judges the draft, in its own process"),
        (AMBER, "two Google model families, neither of which can approve"),
        (GREEN, "one governed write, addressed by its own sha256"),
    ]):
        y = 240 + i * 30
        d.ellipse([58, y + 7, 66, y + 15], fill=colour)
        d.text((80, y), text, font=font(SEGOE, 18), fill=DIM)

    d.text((58, 372), "Google Cloud  ·  Cloud Run  ·  Vertex AI  ·  Firestore  ·  ADK",
           font=font(SEGOE, 15), fill=DIM)

    # The refusal, quoted, because it is the whole argument in one line.
    box = [610, 120, 950, 300]
    d.rounded_rectangle(box, radius=14, fill="#1b1b21", outline=EDGE, width=2)
    d.text((634, 142), "POST /execute", font=font(MONO, 17), fill=DIM)
    for i, line in enumerate([
        "the reader service cannot",
        "reach the specification",
        "repository credential",
    ]):
        d.text((634, 182 + i * 26), line, font=font(MONO, 17), fill=RED)
    d.text((634, 268), "Google IAM, not a prompt.", font=font(SEGOE, 15), fill=DIM)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"wrote {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1000:.0f} kB  {W}x{H}")


if __name__ == "__main__":
    main()
