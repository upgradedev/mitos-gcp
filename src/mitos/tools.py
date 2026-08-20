"""Bounded reads. The thing the product name promised and did not have.

Until now the specialists were handed the diff and asked for prose. Nothing was
read, so nothing was bounded, and the fleet had no agency: the sequence of work
was fixed and every outcome was decided by a regular expression. That is a
workflow with text generation attached, not an agent, and it is worth saying so
plainly because it is the failure mode this project set out to avoid.

Here the specialist gets a repository and a question instead of an answer. It
decides what to open. A schema change might send it to the specification; a
personal-data field might send it to the retention register first; a field whose
name says nothing might send it to the model class and then to the register. The
sequence is not ours to write down in advance, which is what makes it agency.

That is exactly why the reads have to be bounded, and why the bound cannot be a
sentence in a prompt:

    scope       only paths under a declared prefix are readable
    traversal   `..` and absolute paths are refused
    size        a single read is capped
    budget      a run gets a finite number of reads

Every one of these is checked in `guard.py`, which ADK consults before the tool
runs. An agent that decides to read the whole repository does not get to.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

# What a specialist may see. Anything outside this is not "denied at the edges",
# it is invisible: the listing does not mention it and the read refuses.
ALLOWED_PREFIXES = ("docs/", "services/", "registers/")

MAX_BYTES_PER_READ = 8_000
MAX_READS_PER_RUN = 12


class Corpus(Protocol):
    """Where the fleet reads from."""

    def paths(self) -> list[str]: ...

    def read(self, path: str) -> str: ...


# --------------------------------------------------------------------------
# The synthetic repository the fleet reads.
#
# Held as data rather than fetched, for the reason ADR-003 gives: the INPUT stays
# stable so a recorded run is reproducible. What varies, and what makes the demo
# worth watching, is which parts of it the agent chooses to open.
# --------------------------------------------------------------------------

SYNTHETIC_REPO: dict[str, str] = {
    "docs/specs/customer-record.md": (
        "# Customer record\n\n"
        "The customer record is the system of record for contact details.\n\n"
        "## Fields\n\n"
        "| Field | Purpose | Retention |\n"
        "|---|---|---|\n"
        "| customerId | identity | life of contract |\n"
        "| displayName | correspondence | life of contract |\n\n"
        "## Retention\n\n"
        "Contact details follow the customer retention rule in\n"
        "`registers/retention.md`.\n"
    ),
    "docs/specs/metering.md": (
        "# Metering\n\n"
        "Meter configuration and readings.\n\n"
        "Reading interval is fixed at 30 minutes. Settlement windows are not\n"
        "yet specified.\n"
    ),
    "docs/specs/billing.md": (
        "# Billing\n\nTariff codes are six characters. Debt export contains no\n"
        "personal identifiers beyond the account number.\n"
    ),
    # The register. A compliance specialist that never opens this is guessing.
    "registers/retention.md": (
        "# Record of processing, Article 30\n\n"
        "| Field | Lawful basis | Retention | Owner |\n"
        "|---|---|---|---|\n"
        "| displayName | contract | life of contract + 6y | billing-lead |\n"
        "| supplyAddress | contract | life of contract + 6y | billing-lead |\n"
        "| preferredLanguage | legitimate interest | life of contract | ops-lead |\n"
        "| marketingOptInAt | consent | until withdrawn | marketing-lead |\n"
        "\n"
        "Fields not listed here have no recorded lawful basis and no retention\n"
        "period. Adding one without an entry is a finding.\n"
    ),
    "services/customer/src/main/java/com/example/model/CustomerDocument.java": (
        "public class CustomerDocument {\n"
        "    private String customerId;\n"
        "    private String displayName;\n"
        "    private Instant updatedAt;\n"
        "}\n"
    ),
    "services/customer/README.md": (
        "# Customer service\n\nOwns the customer record. Any field added here\n"
        "must appear in `registers/retention.md` before release.\n"
    ),
}


class DictCorpus:
    def __init__(self, files: Optional[dict[str, str]] = None) -> None:
        self._files = dict(files if files is not None else SYNTHETIC_REPO)

    def paths(self) -> list[str]:
        return sorted(self._files)

    def read(self, path: str) -> str:
        return self._files[path]


class OutOfScope(Exception):
    """A read the agent was not entitled to make."""


class ReadBudgetExhausted(Exception):
    """The agent read as much as it is going to read."""


def check_read(path: str, reads_so_far: int) -> None:
    """The bound, as one pure function, so it is testable without an agent.

    Raises rather than returning a flag: a caller that forgets to check a
    boolean gets the read, and a caller that forgets to catch an exception gets
    a crash. Only one of those fails safe.
    """
    if reads_so_far >= MAX_READS_PER_RUN:
        raise ReadBudgetExhausted(
            f"this run has already read {reads_so_far} files, which is the limit"
        )
    if path.startswith("/") or "\\" in path:
        raise OutOfScope(f"absolute paths are not readable: {path!r}")
    if ".." in path.split("/"):
        raise OutOfScope(f"path traversal is not readable: {path!r}")
    if not any(path.startswith(p) for p in ALLOWED_PREFIXES):
        raise OutOfScope(
            f"{path!r} is outside the readable scope "
            f"({', '.join(ALLOWED_PREFIXES)})"
        )


@dataclass
class ReadLog:
    """What the agent chose to open, in order.

    This is the evidence that the fleet has agency. A fixed pipeline produces
    the same log every time; an agent produces a different one per item, and the
    difference is visible in the provenance thread.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reads(self) -> int:
        """Successful reads only, and the distinction is load-bearing.

        Counting refused reads against the budget had two consequences, both
        wrong. An agent that guessed a path badly two or three times would be
        locked out of files it was entitled to open, which punishes exploration,
        and exploration is the behaviour this design exists to allow. And
        because the budget check reads this number, refusals inflated it past
        the cap without ever stopping anything: the limit was decorative. The
        test that asserts the cap holds is what found it.
        """
        return sum(
            1 for c in self.calls if c["tool"] == "read_file" and c["outcome"] == "ok"
        )

    def record(self, tool: str, arg: str, outcome: str, size: int = 0) -> None:
        self.calls.append(
            {"tool": tool, "arg": arg, "outcome": outcome, "bytes": size}
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_calls": len(self.calls),
            "reads": self.reads,
            "denied": sum(1 for c in self.calls if c["outcome"] != "ok"),
            "sequence": [f"{c['tool']}({c['arg']}) -> {c['outcome']}" for c in self.calls],
        }


def make_tools(corpus: Corpus, log: ReadLog) -> list:
    """Build the tool set an ADK agent is given.

    Docstrings are the tool contract the model sees, so they are written for the
    model and not for us.
    """

    def list_paths(pattern: str) -> dict:
        """List repository paths matching a glob pattern.

        Use this first to find out what exists before reading anything.

        Args:
            pattern: a glob such as 'docs/specs/*.md' or 'registers/*'.
        """
        hits = [
            p
            for p in corpus.paths()
            if fnmatch.fnmatch(p, pattern)
            and any(p.startswith(a) for a in ALLOWED_PREFIXES)
        ]
        log.record("list_paths", pattern, "ok", len(hits))
        return {"paths": hits, "count": len(hits)}

    def read_file(path: str) -> dict:
        """Read one repository file.

        Reads are bounded: only paths under docs/, services/ and registers/ are
        readable, and a run may read a limited number of files. Choose what you
        actually need.

        Args:
            path: a repository-relative path from list_paths.
        """
        try:
            check_read(path, log.reads)
        except (OutOfScope, ReadBudgetExhausted) as exc:
            log.record("read_file", path, type(exc).__name__)
            return {"error": str(exc), "readable": False}
        try:
            body = corpus.read(path)
        except KeyError:
            log.record("read_file", path, "not_found")
            return {"error": f"no such file: {path}", "readable": False}
        truncated = body[:MAX_BYTES_PER_READ]
        log.record("read_file", path, "ok", len(truncated))
        # `readable` is present on every return, success or failure. A tool that
        # answers with one shape when it works and another when it does not
        # forces the model to guess which it got, and a model that guesses wrong
        # about whether it read something will confidently report on a file it
        # never opened.
        return {
            "readable": True,
            "path": path,
            "content": truncated,
            "truncated": len(body) > MAX_BYTES_PER_READ,
        }

    def search(term: str) -> dict:
        """Find which readable files mention a term.

        Args:
            term: a word or field name, for example 'retention' or 'mobileNumber'.
        """
        hits = []
        for p in corpus.paths():
            if not any(p.startswith(a) for a in ALLOWED_PREFIXES):
                continue
            if term.lower() in corpus.read(p).lower():
                hits.append(p)
        log.record("search", term, "ok", len(hits))
        return {"term": term, "paths": hits}

    return [list_paths, read_file, search]
