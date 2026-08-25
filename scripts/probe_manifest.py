"""Post the live manifest to GitHub and print what GitHub says. NOT A GATE.

This exists because the obvious answer to "why is this not in CI" is to POST the
manifest to `https://github.com/settings/apps/new` and fail the build on the
error text. That was tried, and the endpoint is not an oracle.

Measured, in one sitting, against unchanged bytes:

    20 of 20 accepted        with a cookie jar, one second apart
     4 of 4  refused         a minute later, same jar, one of them the identical
                             manifest that had just been accepted ten times
     3 of 3  refused         then every following request accepted, in a run
                             where only the request count changed

The refusals track session and anti-abuse state, not the manifest. An earlier
pass at this drew confident conclusions from that noise — that a query string
broke it, that removing any single field fixed it — and every one of those
conclusions was wrong. Anything this script prints is a hint to investigate by
hand, never evidence on its own, and never a reason to change the manifest
without reproducing the result while logged in.

`service/manifest.py` holds the rules and `scripts/check_manifest.py` runs them. It checks the properties GitHub
documents, deterministically, with no third party involved, and it runs both in
the integration suite and against the deployed URL.

    python scripts/probe_manifest.py https://<reader>/github/app/new
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.manifest import parse  # noqa: E402

REFUSAL = "must be a valid URL"


def ask(manifest: dict) -> str:
    """GitHub's answer, or the absence of one. Requires curl."""
    page = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            "--data-urlencode", "manifest=" + json.dumps(manifest),
            "https://github.com/settings/apps/new",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    lines = {line.strip() for line in page.replace("<", "\n").split("\n") if REFUSAL in line}
    return "; ".join(sorted(lines)) if lines else "no complaint"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    with urllib.request.urlopen(argv[1], timeout=60) as response:
        _, manifest = parse(response.read().decode())

    print("Repeating the same manifest. Disagreement between these lines is the")
    print("point: it is why this is a diagnostic and not a check.\n")
    for attempt in range(5):
        print(f"  {attempt + 1}. {ask(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
