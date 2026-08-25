"""No workflow may start twice for one commit.

`on: push` with no `branches:` fires for every branch, and `on: pull_request`
fires for the same commit again the moment a pull request is open. Both were set
in `ci.yml` and in `infra.yml`, so every commit on a branch with a pull request
ran the entire pipeline twice.

In `ci.yml` that was waste: every check appeared twice on every pull request and
each was paid for twice, `Gemini, live` and the Firestore emulator suite
included.

In `infra.yml` it broke the build. Both runs reach `terraform plan`, there is one
state file for the project, and the loser dies with

    Error acquiring the state lock
    googleapi: Error 412: At least one of the pre-conditions you specified did not hold

so a pull request touching `infra/` got a red X on a coin toss. The workspace
notes recorded it as "the trap that has cost four commits in one day". It was not
a trap; it was this trigger, and it was reproducible every time.

The `concurrency:` block did not save it. `infra.yml` grouped on `github.ref`,
which is `refs/heads/<branch>` for a push and `refs/pull/<n>/merge` for a pull
request — two different groups, so the very runs it was meant to serialise both
went straight at the lock.

Read as text with a small indentation reader rather than with PyYAML. The
offline job installs pytest and nothing else, so a `import yaml` here would pass
on a laptop and fail in the one job that proves a stranger can run this. The
first draft of this file did exactly that, which is the mistake
`test_offline_suite_stays_offline.py` was written about; `yaml` is on its
forbidden list now.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block(lines: list[str], header: str, at: int) -> list[str]:
    """The lines under `header`, which sits at indentation `at`."""
    for i, line in enumerate(lines):
        if _indent(line) == at and line.strip() == header:
            body = []
            for following in lines[i + 1 :]:
                if not following.strip():
                    continue
                if _indent(following) <= at:
                    break
                body.append(following)
            return body
    return []


def _value(lines: list[str], key: str) -> str | None:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            return stripped[len(key) + 1 :].strip()
    return None


def _workflows() -> list[tuple[str, list[str]]]:
    return [
        (path.name, path.read_text(encoding="utf-8").splitlines())
        for path in sorted(WORKFLOWS.glob("*.yml"))
    ]


def test_the_reader_finds_the_blocks_it_claims_to():
    """This file is a parser, so the parser is checked before it is trusted.

    Without this, a rename anywhere in the workflows turns every assertion below
    into a silent pass over an empty list.
    """
    lines = dict(_workflows())["infra.yml"]

    triggers = _block(lines, "on:", 0)
    assert triggers, "the on: block was not found in infra.yml"
    assert any(line.strip() == "pull_request:" for line in triggers)

    push = _block(triggers, "push:", 2)
    assert push, "the push: block was not found under on: in infra.yml"
    assert _value(push, "branches") is not None


def test_no_workflow_runs_twice_for_one_commit():
    """A workflow may listen to `push` and to `pull_request`. It may not listen
    to both in a way that matches the same commit."""
    offenders = []
    for name, lines in _workflows():
        triggers = _block(lines, "on:", 0)
        if not triggers:
            continue
        listens_to_pull_requests = any(
            line.strip() == "pull_request:" and _indent(line) == 2 for line in triggers
        )
        listens_to_pushes = any(
            line.strip() == "push:" and _indent(line) == 2 for line in triggers
        )
        if not (listens_to_pushes and listens_to_pull_requests):
            continue

        branches = _value(_block(triggers, "push:", 2), "branches")
        if not branches:
            offenders.append(f"{name}: push has no branches filter, so it fires for every branch")
        elif branches.replace(" ", "") != "[main]":
            offenders.append(f"{name}: push covers {branches}, not just main")

    assert not offenders, (
        "these workflows start twice for every commit on a pull request branch: "
        + "; ".join(offenders)
    )


def test_the_terraform_workflow_serialises_on_the_state_not_on_the_branch():
    """One state file means one concurrency group, for the whole workflow.

    Grouping on anything event-dependent puts the duplicate runs in separate
    groups, which is how this failed while looking like it was handled.
    """
    lines = dict(_workflows())["infra.yml"]
    concurrency = _block(lines, "concurrency:", 0)
    assert concurrency, "infra.yml has no concurrency block; two applies would corrupt the state"

    group = _value(concurrency, "group") or ""
    assert group, "infra.yml declares no concurrency group"
    for varying in ("github.ref", "github.head_ref", "github.event", "github.sha"):
        assert varying not in group, (
            f"the concurrency group contains {varying}, which differs between the "
            f"push and the pull_request run of the same commit, so the two do not "
            f"serialise against each other: {group!r}"
        )

    assert _value(concurrency, "cancel-in-progress") == "false", (
        "a cancelled apply leaves the state locked and the project half changed"
    )
