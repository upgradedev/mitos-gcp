"""Regenerate `openapi.yaml` from the running app.

ORG_STANDARDS #6 requires an OpenAPI specification at the repo root. A spec
maintained by hand drifts from the endpoints within a sprint, so it is generated
from the app and CI fails when the committed file differs from what the code
would produce. That makes the spec evidence rather than documentation.

    python scripts/generate_openapi.py           # write
    python scripts/generate_openapi.py --check   # fail if the committed file is stale
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MITOS_LEDGER", "memory")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "mitos-fleet")

HEADER = (
    "# Generated from the running FastAPI app by scripts/generate_openapi.py.\n"
    "# ORG_STANDARDS #6 requires this at the repo root. CI regenerates it and\n"
    "# fails if it differs from what is committed, so it cannot drift from the\n"
    "# endpoints that actually exist.\n"
)

SERVERS = [
    {
        "url": "https://mitos-reader-696476845998.europe-west1.run.app",
        "description": "reader, holds the Firestore query subscription",
    },
    {
        "url": "https://mitos-evaluator-696476845998.europe-west1.run.app",
        "description": "evaluator",
    },
    {
        "url": "https://mitos-writer-696476845998.europe-west1.run.app",
        "description": "writer, the only identity that can publish",
    },
]


def build() -> str:
    import yaml

    from service.main import app  # noqa: PLC0415

    spec = app.openapi()
    spec["info"] = {
        "title": "Mitos fleet API",
        "version": "1.0.0",
        "description": (
            "One image, three deployments. What differs is the service account "
            "Cloud Run starts it with and MITOS_ROLE, neither of which the "
            "process can change. /execute exists on all three deployments and "
            "refuses on two of them."
        ),
        "license": {
            "name": "MIT",
            "url": "https://github.com/upgradedev/mitos-gcp/blob/main/LICENSE",
        },
    }
    spec["servers"] = SERVERS
    return HEADER + yaml.safe_dump(
        json.loads(json.dumps(spec)),
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
    )


def main() -> int:
    target = ROOT / "openapi.yaml"
    generated = build()
    if "--check" in sys.argv:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != generated:
            print(
                "openapi.yaml is stale. Run: python scripts/generate_openapi.py",
                file=sys.stderr,
            )
            return 1
        print("openapi.yaml matches the app")
        return 0
    target.write_text(generated, encoding="utf-8")
    print(f"wrote {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
