"""A job that reads a repository file must check the repository out.

The deployed pyramid's `judge journeys` job was entirely `curl` for its whole
life, so it never needed the source and never checked it out. A step was then
added to it that runs `python scripts/check_manifest.py`, and the job failed
with

    python: can't open file '/home/runner/work/mitos-gcp/mitos-gcp/scripts/check_manifest.py'
    [Errno 2] No such file or directory

which is a missing checkout wearing the costume of a broken manifest. Worse than
the noise: layer 3 stopped at that step, so `judge UAT` and `dynamic scan` were
skipped, and a deployment that was in fact correct was reported as failing on the
one gate that had just been built to say so.

The direction of the failure was luck. A step that reads a repository path in a
job with no checkout can just as easily exit 0 — `grep -c something file` on a
missing file, a `test -f` that is negated, a script whose absence is swallowed by
`|| true` — and then it is a gate that passes forever without running.

Deliberately over-inclusive, and worth saying so rather than dressing it up as
precise: it matches a repository path anywhere in a job, including inside an
`echo` that merely mentions one. The asymmetry justifies it. A false positive
costs one checkout step, about two seconds. A false negative is a gate that
either fails for a reason nobody can read, as this one did, or passes forever
without running.

Standard library only. `tests/unit` installs pytest and nothing else, so this
reads the workflows with a small indentation reader rather than PyYAML, for the
reason `test_offline_suite_stays_offline.py` exists.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# Top-level directories that only exist after a checkout. A step naming one of
# these is reading the repository.
REPOSITORY_PATHS = ("scripts/", "tests/", "service/", "src/", "infra/", "web/", "video/")

# Files at the repository root that a step might name directly.
REPOSITORY_FILES = ("openapi.yaml", "requirements.txt", "cloudbuild.yaml", "Dockerfile")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blocks(lines: list[str], at: int, under: list[str] | None = None) -> dict[str, list[str]]:
    """Every `key:` at indentation `at`, mapped to the lines beneath it."""
    source = under if under is not None else lines
    found: dict[str, list[str]] = {}
    for i, line in enumerate(source):
        if _indent(line) != at or not line.strip().endswith(":"):
            continue
        key = line.strip()[:-1]
        body: list[str] = []
        for following in source[i + 1 :]:
            if not following.strip():
                continue
            if _indent(following) <= at:
                break
            body.append(following)
        found[key] = body
    return found


def _jobs(path: Path) -> dict[str, list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    jobs_block = _blocks(lines, 0).get("jobs")
    return _blocks(lines, 2, under=jobs_block) if jobs_block else {}


def _reads_the_repository(body: list[str]) -> list[str]:
    """The lines in a job that name a path only a checkout provides.

    `uses:` lines are excluded: `actions/checkout@sha` contains a slash and is
    not a repository path, and neither is any other action reference.
    """
    naming = []
    for line in body:
        stripped = line.strip()
        if stripped.startswith(("uses:", "#", "-  uses:", "- uses:")):
            continue
        if any(p in stripped for p in REPOSITORY_PATHS) or any(
            f in stripped for f in REPOSITORY_FILES
        ):
            naming.append(stripped)
    return naming


def test_the_reader_finds_the_jobs_and_their_steps():
    """The check on the checker. Without it a rename turns every assertion below
    into a silent pass over an empty dictionary, which is the same class of
    failure this file is about."""
    jobs = _jobs(WORKFLOWS / "deployed.yml")

    assert jobs, "no jobs were found in deployed.yml"
    assert "journeys" in jobs, f"the journeys job was not found; jobs are {sorted(jobs)}"
    assert any("actions/checkout" in line for line in jobs["journeys"])
    assert _reads_the_repository(jobs["journeys"]), (
        "the journeys job no longer names any repository path, so this file "
        "would pass without proving anything"
    )


def test_every_job_that_reads_a_repository_path_checks_the_repository_out():
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, body in _jobs(path).items():
            reading = _reads_the_repository(body)
            if not reading:
                continue
            if not any("actions/checkout" in line for line in body):
                offenders.append(f"{path.name}:{name} reads {reading[0][:70]!r} with no checkout")

    assert not offenders, (
        "these jobs read a file that only exists after a checkout:\n  " + "\n  ".join(offenders)
    )
