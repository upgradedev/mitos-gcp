"""Render the infrastructure diagram, from what Terraform actually declares.

    python scripts/infra_diagram.py

Writes `docs/infrastructure.png`.

The companion to `scripts/architecture_diagram.py`. That one answers "what
happens to a pull request"; this one answers "what exists in the project, under
which identity, and who is allowed to reach what".

Every binding drawn here was read out of `infra/main.tf` rather than
remembered. That distinction has already cost this project once: the
architecture diagram said the reader holds no write credential, and it holds the
GitHub App private key because it needs it to post a check run.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "infrastructure.png"

W, H = 1800, 1200

BG = "#131316"
PANEL = "#1b1b21"
SUNK = "#20202a"
EDGE = "#32323c"
INK = "#d7d4d9"
DIM = "#8a8790"
WHITE = "#ffffff"

CYAN = "#5fd7d7"
BLUE = "#7aa2f7"
AMBER = "#ffd75f"
GREEN = "#73d13d"
RED = "#ff6b6b"
VIOLET = "#bb9af7"

SEGOE = "C:/Windows/Fonts/segoeui.ttf"
SEGOE_B = "C:/Windows/Fonts/segoeuib.ttf"
MONO = "C:/Windows/Fonts/consola.ttf"


def font(path: str, size: int):
    for candidate in (path,
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE = font(SEGOE_B, 38)
F_H = font(SEGOE_B, 21)
F_B = font(SEGOE, 17)
F_S = font(SEGOE, 14)
F_M = font(MONO, 14)
F_MB = font(MONO, 15)


def box(d, xy, *, fill=PANEL, outline=EDGE, width=2, radius=12):
    d.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def role(d, x, y, text, colour=DIM):
    """A role, in monospace, because it is an identifier and not prose."""
    w = d.textbbox((0, 0), text, font=F_M)[2]
    d.rounded_rectangle([x, y, x + w + 16, y + 22], radius=6, fill=SUNK,
                        outline=EDGE, width=1)
    d.text((x + 8, y + 3), text, font=F_M, fill=colour)
    return x + w + 16


def arrow(d, start, end, colour=EDGE, width=2, head=8, dash=False):
    import math

    x1, y1 = start
    x2, y2 = end
    if dash:
        for i in range(0, 24, 2):
            a, b = i / 24, (i + 1) / 24
            d.line([x1 + (x2 - x1) * a, y1 + (y2 - y1) * a,
                    x1 + (x2 - x1) * b, y1 + (y2 - y1) * b], fill=colour, width=width)
    else:
        d.line([x1, y1, x2, y2], fill=colour, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    for side in (-1, 1):
        a = ang + side * 2.6
        d.line([x2, y2, x2 + head * math.cos(a), y2 + head * math.sin(a)],
               fill=colour, width=width)


def label(d, text, x, y, colour=DIM, f=F_S):
    w, h = d.textbbox((0, 0), text, font=f)[2:]
    d.rectangle([x - 5, y - 2, x + w + 5, y + h + 2], fill=BG)
    d.text((x, y), text, font=f, fill=colour)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((56, 40), "Mitos infrastructure", font=F_TITLE, fill=WHITE)
    d.text((56, 92),
           "project upgradegr-mitos  ·  europe-west1  ·  every resource and every "
           "binding below is declared in infra/main.tf",
           font=F_S, fill=DIM)
    d.line([56, 122, W - 56, 122], fill=EDGE, width=1)

    # ------------------------------------------------------ supply chain
    box(d, [56, 152, 560, 620], outline=VIOLET)
    d.text((80, 170), "Supply chain", font=F_H, fill=VIOLET)
    d.text((80, 200), "No service account key exists anywhere.", font=F_S, fill=GREEN)

    box(d, [80, 232, 536, 326], fill=SUNK, outline=EDGE, width=1)
    d.text((100, 242), "GitHub Actions", font=F_B, fill=INK)
    d.text((100, 266), "authenticates with a short lived OIDC token", font=F_S, fill=DIM)
    role(d, 100, 292, "iam.workloadIdentityUser")

    arrow(d, (308, 326), (308, 350), VIOLET, 2)
    box(d, [80, 350, 536, 414], fill=SUNK, outline=EDGE, width=1)
    d.text((100, 360), "Workload Identity Pool + Provider", font=F_B, fill=VIOLET)
    d.text((100, 384), "repository claim bound; impersonation only", font=F_S, fill=DIM)

    for i, (sa, note, rows) in enumerate([
        ("mitos-build", "builds and pushes the image",
         [["artifactregistry.writer", "logging.logWriter"], ["storage.objectViewer"]]),
        ("mitos-ci", "runs the live model suites", [["aiplatform.user"]]),
    ]):
        y = 432 + i * 88
        d.text((100, y), sa, font=F_MB, fill=INK)
        d.text((262, y + 1), note, font=F_S, fill=DIM)
        for j, row in enumerate(rows):
            x = 100
            for r in row:
                x = role(d, x, y + 24 + j * 28, r) + 8

    d.text((100, 588), "mitos-ci cannot read Firestore and cannot reach any secret.",
           font=F_S, fill=RED)

    # ------------------------------------------------------ build to run
    box(d, [620, 152, 1080, 340], outline=EDGE)
    d.text((644, 170), "Build and registry", font=F_H, fill=WHITE)
    box(d, [644, 206, 1056, 262], fill=SUNK, outline=EDGE, width=1)
    d.text((664, 214), "Cloud Build  ·  cloudbuild.yaml", font=F_B, fill=INK)
    d.text((664, 238), "MITOS_BUILD_SHA baked in as a build argument", font=F_S, fill=DIM)
    box(d, [644, 272, 1056, 320], fill=SUNK, outline=EDGE, width=1)
    d.text((664, 280), "Artifact Registry  ·  one image, three services", font=F_B, fill=INK)
    d.text((664, 300), "the deployed check refuses an image with no commit in it",
           font=F_S, fill=DIM)

    # ------------------------------------------------------ the three services
    box(d, [620, 380, 1080, 912], outline=CYAN)
    d.text((644, 398), "Cloud Run  ·  europe-west1", font=F_H, fill=CYAN)
    d.text((644, 428), "one image, three services, three identities", font=F_S, fill=DIM)

    services = [
        ("mitos-reader", CYAN, "public. Takes the webhook, runs the fleet, serves the UI"),
        ("mitos-evaluator", BLUE, "no public access. Judges drafts"),
        ("mitos-writer", GREEN, "no public access. Performs the governed write"),
    ]
    for i, (name, colour, note) in enumerate(services):
        y = 462 + i * 112
        box(d, [644, y, 1056, y + 96], fill=SUNK, outline=colour, width=2)
        d.text((664, y + 10), name, font=F_MB, fill=colour)
        d.text((664, y + 34), note, font=F_S, fill=DIM)
        x = role(d, 664, y + 58, "aiplatform.user") + 8
        role(d, x, y + 58, "datastore.user")

    d.text((644, 840), "allUsers may invoke the reader.", font=F_S, fill=AMBER)
    d.text((644, 862), "Only the reader holds run.invoker on the other two,",
           font=F_S, fill=AMBER)
    d.text((644, 880), "and its token is audience bound to the one it is calling.",
           font=F_S, fill=AMBER)

    # ------------------------------------------------------ state and secrets
    box(d, [1140, 152, 1744, 540], outline=RED)
    d.text((1164, 170), "Secret Manager", font=F_H, fill=RED)
    d.text((1164, 200), "Bindings are per secret, not per project.", font=F_S, fill=DIM)

    secrets = [
        ("spec-repo deploy key", [("mitos-writer", "secretAccessor", GREEN)],
         "and nobody else. This is the credential the whole design is about."),
        ("github app webhook secret", [("mitos-reader", "secretAccessor", CYAN)],
         "verifies the HMAC over the raw body"),
        ("github app private key,\nclient secret", [("mitos-reader", "secretAccessor", CYAN),
                                                    ("mitos-reader", "secretVersionAdder", CYAN)],
         "mints installation tokens to post the check run"),
    ]
    y = 234
    for title, members, note in secrets:
        lines = title.split("\n")
        h = 78 + (len(lines) - 1) * 20
        box(d, [1164, y, 1720, y + h], fill=SUNK, outline=EDGE, width=1)
        for k, line in enumerate(lines):
            d.text((1184, y + 8 + k * 20), line, font=F_B, fill=INK)
        x = 1184
        for who, r, colour in members:
            x = role(d, x, y + 30 + (len(lines) - 1) * 20, f"{who} {r}", colour) + 8
        d.text((1184, y + h - 22), note, font=F_S, fill=DIM)
        y += h + 14

    box(d, [1140, 572, 1744, 764], outline=AMBER)
    d.text((1164, 590), "Data and models", font=F_H, fill=AMBER)
    box(d, [1164, 626, 1720, 690], fill=SUNK, outline=EDGE, width=1)
    d.text((1184, 634), "Firestore  ·  collection provenance", font=F_B, fill=INK)
    d.text((1184, 658), "roles/datastore.user includes update and delete. Append only "
                        "is enforced", font=F_S, fill=DIM)
    d.text((1184, 676), "by the interface, not by IAM, and the README says so.",
           font=F_S, fill=DIM)
    box(d, [1164, 700, 1720, 752], fill=SUNK, outline=EDGE, width=1)
    d.text((1184, 708), "Vertex AI  ·  global endpoint", font=F_B, fill=AMBER)
    d.text((1184, 730), "Gemini 3.7 Flash and Gemma 4 26B, both under aiplatform.user",
           font=F_S, fill=DIM)

    # ------------------------------------------------------ what is refused
    box(d, [1140, 788, 1744, 944], outline=EDGE)
    d.text((1164, 802), "What each identity is refused", font=F_H, fill=WHITE)
    for i, (who, what) in enumerate([
        ("mitos-reader", "the spec-repo deploy key"),
        ("mitos-evaluator", "every secret, and both other services"),
        ("mitos-writer", "the webhook secret, and invoking anything"),
        ("mitos-ci", "Firestore, and every secret"),
    ]):
        y = 840 + i * 25
        d.text((1164, y), who, font=F_M, fill=DIM)
        d.text((1330, y), what, font=F_S, fill=RED)

    # ------------------------------------------------------ edges
    arrow(d, (560, 290), (620, 234), VIOLET, 2)
    arrow(d, (850, 340), (850, 380), EDGE, 2)
    label(d, "deployed by terraform apply", 862, 348)
    arrow(d, (1080, 300), (1140, 300), EDGE, 2, dash=True)
    arrow(d, (1080, 560), (1140, 660), AMBER, 2)
    arrow(d, (1080, 500), (1140, 300), RED, 2, dash=True)

    d.line([56, 972, W - 56, 972], fill=EDGE, width=1)
    d.text((56, 992), "Reproducing it", font=F_H, fill=WHITE)
    for i, line in enumerate([
        "gcloud builds submit --project PROJECT --region europe-west1 --config cloudbuild.yaml --substitutions _SHA=$(git rev-parse --short HEAD)",
        "terraform -chdir=infra init -backend-config=\"bucket=STATE_BUCKET\" -backend-config=\"prefix=mitos\"",
        "terraform -chdir=infra apply -var=\"project_id=PROJECT\" -var=\"image=europe-west1-docker.pkg.dev/PROJECT/cloud-run-source-deploy/mitos-reader:SHA\"",
    ]):
        d.text((56, 1028 + i * 26), line, font=F_M, fill=DIM)

    d.text((56, 1118),
           "Terraform creates the services, the identities, the bindings between them, "
           "the Firestore database and the secrets. MITOS_CRITIC_MODEL is a variable; "
           "setting it empty turns the second model off.",
           font=F_S, fill=DIM)
    d.text((56, 1146),
           "No step in the deployment, and no step in CI, uses a downloaded service "
           "account key.", font=F_S, fill=GREEN)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"wrote {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1000:.0f} kB  {W}x{H}")


if __name__ == "__main__":
    main()
