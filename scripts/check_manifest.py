"""Run the manifest rules against a deployed `/github/app/new`.

The rules live in `service/manifest.py` because the service applies them to
itself before it renders the page. This is the command line around them, so
`deployed.yml` and a person debugging use the same code the service uses.

    python scripts/check_manifest.py https://<reader>/github/app/new
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.manifest import parse, problems  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    with urllib.request.urlopen(argv[1], timeout=60) as response:
        page = response.read().decode()

    action, manifest = parse(page)
    for field, value in sorted(manifest.items()):
        if "url" in field:
            print(f"  {field}: {value}")

    found = problems(action, manifest)
    if found:
        for problem in found:
            print(f"::error::{problem}")
        return 1

    print(f"manifest is well formed, posting to {action.split('?')[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
