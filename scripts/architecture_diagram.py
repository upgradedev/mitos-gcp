"""Render the architecture diagram the submission requires, from code.

    python scripts/architecture_diagram.py

Writes `docs/architecture.png`.

Drawn rather than exported so it is reproducible: a diagram pasted in from a
drawing tool drifts from the system the moment either changes, and this project
has already found one of those. The README's Mermaid diagram still had the
deterministic gate inside the reader three commits after ADR-019 moved it into
its own service.

Palette and vocabulary match the deployed system: the colours are the ones
`service/thread_view.py` gives the same entities in the provenance thread, and
every box here is a thing that exists in `infra/main.tf`.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "architecture.png"

W, H = 1800, 1150

BG = "#131316"
PANEL = "#1b1b21"
EDGE = "#32323c"
INK = "#d7d4d9"
DIM = "#8a8790"

CYAN = "#5fd7d7"      # trigger, and the boundary a request crosses
BLUE = "#7aa2f7"      # dispatch and gate
AMBER = "#ffd75f"     # the models
GREEN = "#73d13d"     # the human
RED = "#ff6b6b"       # what is refused
WHITE = "#ffffff"

SEGOE = "C:/Windows/Fonts/segoeui.ttf"
SEGOE_B = "C:/Windows/Fonts/segoeuib.ttf"
MONO = "C:/Windows/Fonts/consola.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:  # pragma: no cover - fallback for a machine without it
        return ImageFont.load_default()


F_TITLE = font(SEGOE_B, 40)
F_SUB = font(SEGOE, 21)
F_H = font(SEGOE_B, 23)
F_B = font(SEGOE, 18)
F_S = font(SEGOE, 15)
F_M = font(MONO, 15)
F_TAG = font(SEGOE_B, 14)


def box(d, xy, *, fill=PANEL, outline=EDGE, width=2, radius=12):
    d.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def centred(d, text, cx, y, f, fill=INK):
    w = d.textbbox((0, 0), text, font=f)[2]
    d.text((cx - w / 2, y), text, font=f, fill=fill)


def arrow(d, start, end, colour=EDGE, width=2, head=9, dash=False):
    x1, y1 = start
    x2, y2 = end
    if dash:
        n = 26
        for i in range(n):
            if i % 2:
                continue
            a = i / n
            b = (i + 1) / n
            d.line(
                [x1 + (x2 - x1) * a, y1 + (y2 - y1) * a,
                 x1 + (x2 - x1) * b, y1 + (y2 - y1) * b],
                fill=colour, width=width,
            )
    else:
        d.line([x1, y1, x2, y2], fill=colour, width=width)
    # Head, oriented on the segment rather than assumed vertical.
    import math

    ang = math.atan2(y2 - y1, x2 - x1)
    for side in (-1, 1):
        a = ang + side * 2.6
        d.line([x2, y2, x2 + head * math.cos(a), y2 + head * math.sin(a)],
               fill=colour, width=width)


def label(d, text, x, y, f=F_S, fill=DIM, bg=BG):
    w, h = d.textbbox((0, 0), text, font=f)[2:]
    d.rectangle([x - 6, y - 3, x + w + 6, y + h + 3], fill=bg)
    d.text((x, y), text, font=f, fill=fill)


def tag(d, text, x, y, colour):
    w = d.textbbox((0, 0), text, font=F_TAG)[2]
    d.rounded_rectangle([x, y, x + w + 18, y + 24], radius=12, outline=colour, width=2)
    d.text((x + 9, y + 4), text, font=F_TAG, fill=colour)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((60, 44), "Mitos", font=F_TITLE, fill=WHITE)
    d.text((190, 57), "a fleet of institutional agents, one governed write",
           font=F_SUB, fill=DIM)
    d.text((60, 100), "Google Cloud  ·  project upgradegr-mitos  ·  europe-west1",
           font=F_S, fill=DIM)
    d.line([60, 130, W - 60, 130], fill=EDGE, width=1)

    # ---------------------------------------------------------------- trigger
    box(d, [60, 165, 430, 285], outline=CYAN)
    d.text((84, 183), "GitHub", font=F_H, fill=CYAN)
    d.text((84, 216), "pull request opened", font=F_B, fill=INK)
    d.text((84, 242), "GitHub App webhook, HMAC-SHA256", font=F_S, fill=DIM)
    d.text((84, 260), "over the raw body. Nobody opens Mitos.", font=F_S, fill=DIM)

    # ----------------------------------------------------------------- reader
    box(d, [60, 340, 700, 720], outline=EDGE)
    d.text((84, 358), "mitos-reader", font=F_H, fill=WHITE)
    d.text((84, 390), "Cloud Run", font=F_S, fill=DIM)
    tag(d, "SA mitos-reader", 470, 356, DIM)
    d.text((470, 388), "no write credential", font=F_S, fill=RED)

    box(d, [90, 424, 670, 490], fill="#20202a", outline=BLUE, width=2)
    d.text((110, 436), "architect-leader", font=F_B, fill=BLUE)
    d.text((110, 460), "router: reads the catalogue, decides who wakes",
           font=F_S, fill=DIM)

    for i, (name, note) in enumerate([
        ("db-architect-leader", "schema-change"),
        ("documentation-companion", "schema-change, spec-touched"),
        ("compliance-companion", "personal-data, skipped when absent"),
    ]):
        y = 508 + i * 56
        box(d, [90, y, 670, y + 46], fill="#191921", outline=EDGE, width=1)
        d.text((110, y + 6), name, font=F_B, fill=INK)
        d.text((110, y + 26), note, font=F_S, fill=DIM)

    d.text((90, 684), "React + TypeScript SPA served from the same service",
           font=F_S, fill=DIM)

    # -------------------------------------------------------------- Vertex AI
    box(d, [790, 340, 1290, 560], outline=AMBER)
    d.text((814, 358), "Vertex AI", font=F_H, fill=AMBER)
    d.text((814, 390), "global endpoint, Application Default Credentials",
           font=F_S, fill=DIM)

    box(d, [814, 424, 1266, 480], fill="#20202a", outline=EDGE, width=1)
    d.text((834, 432), "Gemini 3.7 Flash", font=F_B, fill=AMBER)
    d.text((834, 454), "routes, reads the repository, drafts. Primary.",
           font=F_S, fill=DIM)

    box(d, [814, 490, 1266, 546], fill="#20202a", outline=EDGE, width=1)
    d.text((834, 498), "Gemma 4 26B A4B IT", font=F_B, fill=AMBER)
    d.text((834, 520), "managed open model. Independent critic, advisory only.",
           font=F_S, fill=DIM)

    # -------------------------------------------------------------- evaluator
    box(d, [790, 620, 1290, 830], outline=BLUE)
    d.text((814, 638), "mitos-evaluator", font=F_H, fill=WHITE)
    d.text((814, 670), "Cloud Run", font=F_S, fill=DIM)
    tag(d, "SA mitos-evaluator", 1060, 636, DIM)

    box(d, [814, 704, 1266, 806], fill="#20202a", outline=EDGE, width=1)
    d.text((834, 712), "deterministic gate", font=F_B, fill=BLUE)
    for i, line in enumerate([
        "leaked credentials  ·  prompt injection planted in the diff",
        "gate-skip attempts  ·  citations of files never opened",
        "Not a prompt. A function that returns a refusal.",
    ]):
        d.text((834, 738 + i * 21), line, font=F_S, fill=DIM if i < 2 else RED)

    # ---------------------------------------------------------------- human
    box(d, [60, 790, 700, 940], outline=GREEN)
    d.text((84, 808), "approval card", font=F_H, fill=GREEN)
    d.text((84, 842), "the exact bytes, their sha256, and what will happen",
           font=F_B, fill=INK)
    d.text((84, 870), "plus any advisory the second model added", font=F_S, fill=DIM)
    d.text((84, 900), "A human approves. Owner role only.", font=F_S, fill=GREEN)

    # ---------------------------------------------------------------- writer
    box(d, [790, 890, 1290, 1075], outline=EDGE)
    d.text((814, 908), "mitos-writer", font=F_H, fill=WHITE)
    d.text((814, 940), "Cloud Run", font=F_S, fill=DIM)
    tag(d, "SA mitos-writer", 1080, 906, DIM)
    box(d, [814, 974, 1266, 1050], fill="#20202a", outline=EDGE, width=1)
    d.text((834, 982), "governed write", font=F_B, fill=INK)
    d.text((834, 1006), "refuses any plan whose hash it was not given.",
           font=F_S, fill=DIM)
    d.text((834, 1026), "The only identity holding a write credential.",
           font=F_S, fill=GREEN)

    # ------------------------------------------------------- state and secrets
    box(d, [1380, 340, 1740, 560], outline=CYAN)
    d.text((1404, 358), "Firestore", font=F_H, fill=CYAN)
    d.text((1404, 392), "provenance thread", font=F_B, fill=INK)
    for i, line in enumerate([
        "append only by interface: no update",
        "and no delete method exists.",
        "Any entry walks back to the diff",
        "that caused it.",
        "",
        "Open query subscriptions are the",
        "trigger: no scheduler, no queue.",
    ]):
        d.text((1404, 420 + i * 19), line, font=F_S, fill=DIM)

    box(d, [1380, 620, 1740, 790], outline=EDGE)
    d.text((1404, 638), "Secret Manager", font=F_H, fill=WHITE)
    for i, line in enumerate([
        "GitHub App private key,",
        "webhook secret, spec-repo",
        "deploy key.",
        "",
        "Granted to the writer.",
        "Refused to the reader.",
    ]):
        d.text((1404, 674 + i * 19), line, font=F_S,
               fill=RED if i == 5 else DIM)

    box(d, [1380, 850, 1740, 1075], outline=EDGE)
    d.text((1404, 868), "Build and identity", font=F_H, fill=WHITE)
    for i, line in enumerate([
        "Cloud Build, commit baked",
        "into the image.",
        "Artifact Registry.",
        "Terraform for all of it.",
        "",
        "Workload Identity Federation:",
        "CI holds no key, cannot read",
        "Firestore, cannot reach the",
        "write credential.",
    ]):
        d.text((1404, 904 + i * 19), line, font=F_S, fill=DIM)

    # ------------------------------------------------------------------ edges
    arrow(d, (245, 285), (245, 340), CYAN, 3)
    label(d, "webhook", 258, 296, fill=CYAN)

    arrow(d, (700, 455), (790, 452), AMBER, 2)
    label(d, "ADK", 716, 424, fill=AMBER)

    arrow(d, (700, 560), (790, 700), BLUE, 2)
    label(d, "OIDC, audience bound", 706, 604, fill=BLUE)

    arrow(d, (1000, 830), (704, 858), BLUE, 2)
    label(d, "verdict. Unreachable means the run stops", 745, 838, fill=BLUE)

    arrow(d, (1090, 560), (1090, 620), AMBER, 2)
    label(d, "sanitised draft only", 1100, 574, fill=AMBER)

    arrow(d, (700, 918), (898, 894), GREEN, 3)
    label(d, "approved plan, addressed by its hash", 505, 956, fill=GREEN)

    arrow(d, (1290, 1000), (1380, 1000), EDGE, 2)
    arrow(d, (1290, 940), (1380, 700), EDGE, 2, dash=True)

    # Routed over the top rather than through Vertex AI. A line that crosses a
    # box it has nothing to do with reads as a connection to it.
    d.line([700, 356, 700, 312], fill=CYAN, width=2)
    d.line([700, 312, 1560, 312], fill=CYAN, width=2)
    arrow(d, (1560, 318), (1560, 338), CYAN, 2)
    label(d, "every step appended, by every service", 1000, 288, fill=CYAN)

    # The check run goes back to where the trigger came from.
    d.line([560, 340, 560, 248], fill=CYAN, width=2)
    arrow(d, (556, 248), (434, 248), CYAN, 2)
    label(d, "check run posted back, under an installation token scoped to "
             "one repository", 575, 224, fill=CYAN)

    d.line([60, H - 58, W - 60, H - 58], fill=EDGE, width=1)
    d.text((60, H - 42),
           "Three Cloud Run services, three service accounts, one image. "
           "Each service refuses the routes that are not its job, so a misrouted "
           "request fails twice rather than once.",
           font=F_S, fill=DIM)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"wrote {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1000:.0f} kB  {W}x{H}")


if __name__ == "__main__":
    main()
