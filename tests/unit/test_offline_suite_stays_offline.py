"""The offline suite imports no service extras, and this is why.

CLAUDE.md keeps the offline path standard library only, so a stranger can run
the whole thing with no cloud account and no install. A unit test that imports
`service.main` pulls in httpx and passes locally, where the extras happen to be
present, then fails in the one CI job that installs nothing.

That has cost three separate commits in one day: the public-write assertion, the
forwarded-scheme helper, and the content policy. Each time the fix was the same,
and each time it was found by CI rather than by the person writing the test.
"""

from __future__ import annotations

import re
from pathlib import Path

UNIT = Path(__file__).resolve().parent

# Modules that exist only when requirements.txt or spike.txt is installed. The
# offline job installs neither.
FORBIDDEN = ("httpx", "fastapi", "google.cloud", "google.adk", "google.oauth2")

# `service.main` imports every one of them at module level, so importing it from
# a unit test is the same mistake by another name.
FORBIDDEN_MODULES = ("service.main", "service import main")


def _imports(path: Path) -> str:
    """Only the import statements, so a mention in a docstring is not a match."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and " import " in f"{stripped} ":
            lines.append(stripped)
        elif re.match(r"\s+(import|from)\s+\S", line):
            lines.append(stripped)
    return "\n".join(lines)


def test_no_unit_test_imports_the_service_module():
    """Asserted over imports rather than the whole file.

    Several of these tests read `service/main.py` as text on purpose, which is
    the correct way to check its wiring without importing it, and a naive
    substring search would flag exactly the tests that got this right.
    """
    offenders = []
    for path in sorted(UNIT.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        imports = _imports(path)
        for bad in FORBIDDEN_MODULES:
            if bad in imports:
                offenders.append(f"{path.name} imports {bad}")

    assert not offenders, (
        "these will pass locally and fail in the offline CI job:\n  "
        + "\n  ".join(offenders)
    )


def test_no_unit_test_imports_a_dependency_the_offline_job_lacks():
    offenders = []
    for path in sorted(UNIT.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        imports = _imports(path)
        for bad in FORBIDDEN:
            if re.search(rf"(^|\s){re.escape(bad)}(\.|\s|$)", imports, re.M):
                offenders.append(f"{path.name} imports {bad}")

    assert not offenders, (
        "the offline job installs neither requirements.txt nor spike.txt:\n  "
        + "\n  ".join(offenders)
    )
