"""The offline suite imports no service extras, and this is why.

CLAUDE.md keeps the offline path standard library only, so a stranger can run
the whole thing with no cloud account and no install. A unit test that imports
`service.main` pulls in httpx and passes locally, where the extras happen to be
present, then fails in the one CI job that installs nothing.

That has cost three separate commits in one day: the public-write assertion, the
forwarded-scheme helper, and the content policy. Each time the fix was the same,
and each time it was found by CI rather than by the person writing the test.

It nearly cost a fourth, for a reason worth writing down: this file read imports
with a text match that dropped every plain `import x` statement.

    if stripped.startswith(("import ", "from ")) and " import " in f"{stripped} ":

`from google.cloud import firestore` contains " import " and was caught.
`import httpx` does not — the word sits at the start of the line with no space
in front of it — so the line was silently discarded. This guard named `httpx`
first in its own forbidden list while being unable to see the commonest way of
importing it, and roughly half the failure mode it exists to prevent walked
straight through. Measured rather than reasoned about: `import httpx` and
`import yaml` were both reported clean by the previous version of this file.

So imports are read with `ast` now. A parser cannot disagree with Python about
what an import is, and it dissolves the docstring problem the text match was
working around: prose beginning with the word "import" is not in the tree, so it
cannot match.
"""

from __future__ import annotations

import ast
from pathlib import Path

UNIT = Path(__file__).resolve().parent

# Modules that exist only when requirements.txt or spike.txt is installed. The
# offline job installs pytest and pytest-cov and nothing else.
#
# `yaml` is PyYAML, not the standard library. It joined this list after a
# workflow test imported it, passed on a laptop, and would have failed in the
# one job that proves a stranger can run this.
FORBIDDEN = ("httpx", "fastapi", "google.cloud", "google.adk", "google.oauth2", "yaml")

# `service.main` imports every one of them at module level, so importing it from
# a unit test is the same mistake by another name. One entry, not two: `ast`
# resolves `from service import main` and `import service.main` to the same
# dotted name, so the second spelling no longer needs its own string.
FORBIDDEN_MODULES = ("service.main",)


def _imported_modules(path: Path) -> set[str]:
    """Every module a file imports, however the import is spelled.

    `import a.b`, `from a.b import c` and `from a import b` all resolve to the
    dotted names actually brought in, so the checks below compare names instead
    of guessing at the shape of a line.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _brings_in(imported: str, forbidden: str) -> bool:
    """`google.cloud` covers `google.cloud.firestore`, and `yaml` does not cover
    a module that merely starts with those letters."""
    return imported == forbidden or imported.startswith(f"{forbidden}.")


def _unit_tests() -> list[Path]:
    return [p for p in sorted(UNIT.glob("test_*.py")) if p.name != Path(__file__).name]


def test_the_reader_sees_both_ways_of_writing_an_import():
    """The check on the checker, and the reason this file was rewritten.

    Without it the reader can quietly stop seeing an entire form of import while
    every assertion below keeps passing over the thing it was meant to catch.
    That is not hypothetical here; it is what happened.
    """
    probe = UNIT / "_import_forms_probe.py"
    probe.write_text(
        '"""import httpx here is prose, not an import."""\n'
        "import httpx\n"
        "import google.cloud.firestore\n"
        "from fastapi import FastAPI\n"
        "from service import main\n",
        encoding="utf-8",
    )
    try:
        found = _imported_modules(probe)
    finally:
        probe.unlink()

    assert "httpx" in found, "a plain `import x` is invisible to the reader again"
    assert "google.cloud.firestore" in found
    assert "fastapi" in found
    assert "service.main" in found
    assert not any("prose" in name for name in found), "a docstring was read as an import"


def test_no_unit_test_imports_the_service_module():
    """Asserted over imports rather than the whole file.

    Several of these tests read `service/main.py` as text on purpose, which is
    the correct way to check its wiring without importing it, and a naive
    substring search would flag exactly the tests that got this right.
    """
    offenders = [
        f"{path.name} imports {bad}"
        for path in _unit_tests()
        for imported in sorted(_imported_modules(path))
        for bad in FORBIDDEN_MODULES
        if _brings_in(imported, bad)
    ]

    assert not offenders, (
        "these will pass locally and fail in the offline CI job:\n  " + "\n  ".join(offenders)
    )


def test_no_unit_test_imports_a_dependency_the_offline_job_lacks():
    offenders = [
        f"{path.name} imports {imported}"
        for path in _unit_tests()
        for imported in sorted(_imported_modules(path))
        for bad in FORBIDDEN
        if _brings_in(imported, bad)
    ]

    assert not offenders, (
        "the offline job installs neither requirements.txt nor spike.txt:\n  "
        + "\n  ".join(offenders)
    )
