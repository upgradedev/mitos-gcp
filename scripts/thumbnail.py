"""Render the Devpost gallery thumbnail.

    python scripts/thumbnail.py

Writes `docs/thumbnail.png` at 1200x800, which is the 3:2 Devpost asks for.

It has to work at about 300 pixels wide in a gallery of twelve thousand
projects, so it carries one sentence and one refusal rather than a diagram.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "thumbnail.png"
W, H = 1200, 800

BG, PANEL, EDGE = "#131316", "#1b1b21", "#32323c"
INK, DIM, WHITE = "#d7d4d9", "#8a8790", "#ffffff"
CYAN, AMBER, GREEN, RED = "#5fd7d7", "#ffd75f", "#73d13d", "#ff6b6b"

def font(path, size):
    for c in (path, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    return ImageFont.load_default()

B  = font("C:/Windows/Fonts/segoeuib.ttf", 76)
B2 = font("C:/Windows/Fonts/segoeuib.ttf", 40)
R  = font("C:/Windows/Fonts/segoeui.ttf", 30)
S  = font("C:/Windows/Fonts/segoeui.ttf", 23)
M  = font("C:/Windows/Fonts/consola.ttf", 25)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
for x in range(0, W, 30):
    d.line([x, 0, x, H], fill="#17171c")
for y in range(0, H, 30):
    d.line([0, y, W, y], fill="#17171c")

d.text((70, 70), "Mitos", font=B, fill=WHITE)
d.text((70, 172), "A fleet of AI agents that", font=R, fill=INK)
d.text((70, 212), "cannot write anything.", font=R, fill=INK)
d.text((70, 262), "That is the feature.", font=B2, fill=AMBER)
d.line([70, 336, 620, 336], fill=EDGE, width=2)

for i, (c, t) in enumerate([
    (CYAN, "a pull request wakes five specialists"),
    (AMBER, "Gemini 3.7 routes and reads. Gemma 4 reviews."),
    (GREEN, "one governed write, behind a human and a sha256"),
]):
    y = 366 + i * 42
    d.ellipse([70, y + 9, 80, y + 19], fill=c)
    d.text((98, y), t, font=S, fill=DIM)

d.rounded_rectangle([70, 528, 1130, 700], radius=16, fill=PANEL, outline=EDGE, width=2)
d.text((100, 552), "POST /execute", font=M, fill=DIM)
d.text((100, 596), "the reader service cannot reach the", font=M, fill=RED)
d.text((100, 628), "specification repository credential", font=M, fill=RED)
d.text((100, 664), "Google IAM refusing, from outside the process.", font=S, fill=DIM)

d.text((70, 730), "Google Cloud  ·  Cloud Run  ·  Vertex AI  ·  Firestore  ·  ADK",
       font=S, fill=DIM)

OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT, "PNG")
print(f"wrote {OUT.relative_to(ROOT)}  {OUT.stat().st_size/1000:.0f} kB  {W}x{H}")
