"""Two claims a judge would check, asserted against the thing they describe.

Both were false in the README for weeks, and both were the kind of false that
survives review because nobody re-reads a paragraph that was true when it was
written.

**The write-back denial.** The README said "nothing in the repository has ever
called a GitHub write endpoint: `open_pull_request` and `set_commit_status`
exist only as names in the guard's deny list". One day after that sentence
shipped, `service/main.py` gained five GitHub write calls across four endpoints
— create and update a check run, create a ref, put a file, open a pull request.
The sentence was never revisited. It is the entry's most load-bearing claim and
it was checkable in one grep.

**The anonymous curl.** The README told a stranger to run two commands "with no
account", one of them against the writer, which answers a Google 403 HTML page:
only the reader's service account may invoke the writer or the evaluator.
`deployed.yml` has a check that FAILS THE BUILD if either of them ever answers a
stranger, so the deployment and the documentation asserted opposite things and
both were green.

Text over both files, standard library only, for the reason
`test_offline_suite_stays_offline.py` exists.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = (REPO / "README.md").read_text(encoding="utf-8")
SERVICE = (REPO / "service" / "main.py").read_text(encoding="utf-8")

# The GitHub REST endpoints that change something. A `get` against any of these
# reads; the methods below are what makes a call a write.
WRITE_CALLS = (
    ("httpx.post", "check-runs"),
    ("httpx.patch", "check-runs"),
    ("httpx.post", "/git/refs"),
    ("httpx.put", "/contents/"),
    ("httpx.post", "/pulls"),
)

# Services that refuse anonymous callers by design. `deployed.yml` asserts it.
PRIVATE_SERVICES = ("mitos-writer-", "mitos-evaluator-")

DENIALS = (
    "has ever called a GitHub write endpoint",
    "nothing in the repository has ever called",
    "does not call any GitHub write endpoint",
)


def _github_writes_in_the_service() -> list[str]:
    """Every GitHub-changing call the service really makes."""
    found = []
    for method, endpoint in WRITE_CALLS:
        for match in re.finditer(re.escape(method) + r"\((.{0,200}?)\)", SERVICE, re.S):
            if endpoint in match.group(1) and "api.github.com" in match.group(1) + SERVICE[:0]:
                found.append(f"{method} {endpoint}")
                break
        else:
            # `api` is a local variable holding the api.github.com base in the
            # suggested-pull-request helper, so the literal is not on the line.
            if re.search(re.escape(method) + r"\(\s*\n?\s*f?\"[^\"]*" + re.escape(endpoint),
                         SERVICE):
                found.append(f"{method} {endpoint}")
    return found


def test_the_readme_does_not_deny_write_calls_the_service_makes():
    writes = _github_writes_in_the_service()

    assert writes, (
        "no GitHub write calls were found in service/main.py. Either they were "
        "removed, in which case this test should be, or the patterns here have "
        "drifted and the check is asserting nothing."
    )

    for denial in DENIALS:
        assert denial not in README, (
            f"README says {denial!r}, and service/main.py makes these calls: "
            f"{sorted(set(writes))}"
        )


def test_the_readme_never_tells_a_stranger_to_curl_a_private_service():
    """The reader is public. The writer and the evaluator are not, and a check in
    `deployed.yml` fails the build if that ever changes."""
    offenders = [
        line.strip()
        for line in README.splitlines()
        if line.strip().startswith("curl")
        and any(service in line for service in PRIVATE_SERVICES)
        and "Authorization" not in line
        and "-H" not in line
    ]

    assert not offenders, (
        "these commands are printed for a reader with no account and return a "
        "Google 403 HTML page:\n  " + "\n  ".join(offenders)
    )


def test_the_public_reader_is_still_the_one_that_answers():
    """The correction is only correct while the reader stays public. If it stops
    being so, the README's remaining curl is wrong too and this says which."""
    assert "curl -s https://mitos-reader-" in README, (
        "the README no longer shows the one anonymous command that substantiates "
        "the boundary"
    )


def test_every_run_the_readme_names_is_the_run_it_links():
    """A number in the prose and a different number in the href.

    The coverage passage read "CI run 32738967814" and pointed at run
    32756367127. Nobody catches that by reading, because the rendered page shows
    only one of the two, and anyone who follows the link lands on a real CI run
    with real numbers on it. It stayed wrong through every review.
    """
    mismatched = [
        f"prose says {named}, link goes to {linked}"
        for named, linked in re.findall(
            r"\[[^\]]*?(\d{9,})[^\]]*?\]\(https://github\.com/[^)]*?/runs/(\d+)", README
        )
        if named != linked
    ]

    assert not mismatched, "\n  ".join(["a run id in the text is not the one linked:"] + mismatched)


def test_the_three_coverage_numbers_come_from_one_run():
    """A weaker check than it first looks, and worth naming as such.

    The percentage, the statement count and the miss count must agree with each
    other. That catches someone updating one and not the others, which is the
    likely edit.

    It does NOT catch the figures being old, and the figures were old: the
    previous set read 86.20%, 2725 statements, 376 missed, and 2349/2725 really
    is 86.20%. They were internally consistent and 24 statements out of date.
    Nothing here can tell that apart from correct without running coverage,
    which the offline suite will not do. CI's `--cov-fail-under=85` catches the
    percentage falling through the floor and nothing else about these numbers.
    """
    quoted = re.search(r"([0-9]+\.[0-9]+)%, ([0-9]+) statements, ([0-9]+) missed", README)
    assert quoted, "the coverage passage no longer states a figure this test can check"

    percent, statements, missed = (
        float(quoted.group(1)), int(quoted.group(2)), int(quoted.group(3))
    )
    covered = statements - missed

    assert abs(covered / statements * 100 - percent) < 0.05, (
        f"{percent}% does not follow from {covered}/{statements}; the three "
        f"numbers were copied from different runs"
    )


# The check that would have caught the write-boundary claim, and did not.
#
# This file string-matched three README sentences. It never looked at the
# `/identity` payload the deployed check reads, and never at the built interface,
# which is where the flat falsehood lived: BoundaryView rendered "The reader
# cannot write." while the same process opens pull requests and posts check runs
# over plain httpx, which ADK's `before_tool_callback` never sees.
BOUNDARY_VIEW = (REPO / "web" / "src" / "views" / "BoundaryView.tsx").read_text(encoding="utf-8")


def test_no_judge_facing_file_says_the_reader_cannot_write_without_saying_where():
    """"The reader cannot write" is false. "The reader cannot write to the
    specification repository" is true, is what `/identity` proves live, and is
    the claim the boundary table already made correctly."""
    # Normalised, not line by line. The sentence wraps, so its qualifier can
    # land on the following line, and a per-line check then reports the first
    # first half as unqualified. The first draft of this test did exactly that.
    offenders = []
    for name, text in (
        ("README.md", README),
        ("BoundaryView.tsx", BOUNDARY_VIEW),
        ("service/main.py", SERVICE),
    ):
        flat = " ".join(text.split()).lower()
        for phrase in ("cannot write", "nothing to write with"):
            start = 0
            while True:
                at = flat.find(phrase, start)
                if at == -1:
                    break
                start = at + 1
                # The rest of THIS sentence, not a fixed window. A 140 character
                # window passed the mutation that put the bare sentence back,
                # because the paragraph after it happened to mention GitHub. The
                # qualifier has to be in the sentence making the claim.
                rest = flat[at + len(phrase) :]
                end = min(
                    (i for i in (rest.find("."), rest.find("</b>")) if i != -1),
                    default=len(rest),
                )
                sentence = rest[: end if end else 0]
                if any(
                    q in sentence
                    for q in ("specification repository", "spec repo", "spec_repo")
                ):
                    continue
                offenders.append(f"{name}: ...{flat[max(0, at - 40):at + 80]}...")

    assert not offenders, (
        "these say the reader cannot write, unqualified, while it opens pull "
        "requests and posts check runs under an installation token:\n  "
        + "\n  ".join(offenders)
    )


def test_the_identity_payload_reports_the_github_app_road():
    """`may_call_write_tools` covers the ADK tool path. Reporting only that, and
    closing with a sentence about writing, made a true statement about one path
    read as a statement about the service."""
    assert '"github_app_write"' in SERVICE, (
        "/identity reports only the ADK tool guard, so a reader concludes the "
        "service makes no GitHub writes at all"
    )
    assert "do not pass through that callback" in " ".join(SERVICE.split()), (
        "the note does not say that the direct GitHub calls bypass the callback"
    )
