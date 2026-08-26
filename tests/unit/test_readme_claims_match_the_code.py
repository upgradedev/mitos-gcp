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
