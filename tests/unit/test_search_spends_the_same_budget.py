"""`search` read the whole repository and reported nothing.

`read_file` checked scope, consumed the run budget, capped the bytes and logged
the read. `search` checked scope inline and did none of the rest: it called
`corpus.read` on every path in scope, whole, and recorded a single summary line.

Measured on a 1,000 file corpus with `MAX_READS_PER_RUN` at 12, before the fix:

    actual corpus.read calls: 1000
    log.reads (counted)     : 0
    log entries             : 1

So the bound this product publishes at `/config`, and describes in the README as
the thing that makes an agent's own choices safe, governed one of its two ways
of opening a file. An agent that wanted the whole repository only had to ask for
a common word.

Standard library only, per `test_offline_suite_stays_offline.py`.
"""

from __future__ import annotations

import pytest

from mitos import tools


class _Corpus:
    """Counts what it actually served, which is the number under test."""

    def __init__(self, count: int, body: str = "retention policy applies here") -> None:
        self.files = {f"docs/specs/f{i:04}.md": body for i in range(count)}
        self.files["registers/processing.md"] = "retention register"
        self.served = 0

    def paths(self) -> list[str]:
        return sorted(self.files)

    def read(self, path: str) -> str:
        self.served += 1
        return self.files[path]


def _tools(corpus, scope=None):
    log = tools.ReadLog()
    list_paths, read_file, search = tools.make_tools(corpus, log, scope)
    return log, list_paths, read_file, search


def test_a_search_over_a_thousand_files_stays_inside_the_budget():
    """The reproduction. This read all 1,000 before the fix."""
    corpus = _Corpus(1000)
    log, _, _, search = _tools(corpus)

    search("retention")

    assert corpus.served <= tools.MAX_READS_PER_RUN, (
        f"search served {corpus.served} files against a budget of "
        f"{tools.MAX_READS_PER_RUN}"
    )


def test_what_it_reports_is_what_it_read():
    """A bound nobody can verify from the log is a bound on paper."""
    corpus = _Corpus(1000)
    log, _, _, search = _tools(corpus)

    out = search("retention")

    assert log.reads == corpus.served == out["files_scanned"], (
        f"log says {log.reads}, corpus served {corpus.served}, "
        f"search reported {out['files_scanned']}"
    )


def test_it_says_when_the_answer_is_partial():
    """A partial answer that looks total is worse than a refusal: an agent that
    believes it searched everything reports that a field appears nowhere."""
    corpus = _Corpus(1000)
    _, _, _, search = _tools(corpus)

    out = search("retention")

    assert out["truncated"] is True
    assert out["files_in_scope"] > out["files_scanned"]
    assert out["files_scanned"] == tools.MAX_SEARCH_SCAN
    assert out["reads_remaining"] == tools.MAX_READS_PER_RUN - tools.MAX_SEARCH_SCAN, (
        "one search consumed the whole run budget, which leaves the agent "
        "nothing to read with"
    )


def test_a_small_corpus_is_searched_completely_and_says_so():
    """The counterweight. A change that truncated everything would satisfy the
    test above and make the tool useless."""
    corpus = _Corpus(3)
    _, _, _, search = _tools(corpus)

    out = search("retention")

    assert out["truncated"] is False
    assert out["files_scanned"] == out["files_in_scope"] == 4
    assert len(out["paths"]) == 4


def test_repeated_searches_cannot_re_spend_the_run_budget():
    """Each search is capped, and together they cannot exceed the run budget.

    Both halves matter. Capping per call without a run budget lets an agent
    search fifty times; a run budget without a per-call cap lets one search
    spend everything, which is how the first version of this fix broke the
    live agent.
    """
    corpus = _Corpus(1000)
    log, _, _, search = _tools(corpus)

    calls = tools.MAX_READS_PER_RUN // tools.MAX_SEARCH_SCAN
    for i in range(calls):
        search(f"term{i}")

    assert corpus.served == tools.MAX_READS_PER_RUN
    exhausted = search("one more")

    assert exhausted["files_scanned"] == 0
    assert corpus.served == tools.MAX_READS_PER_RUN, (
        "a search bought itself a fresh budget"
    )


def test_search_and_read_file_draw_on_one_budget():
    """Two doors into the same room. Separate budgets would be no budget."""
    corpus = _Corpus(1000)
    log, _, read_file, search = _tools(corpus)

    for i in range(tools.MAX_READS_PER_RUN // tools.MAX_SEARCH_SCAN):
        search(f"term{i}")

    refused = read_file("docs/specs/f0500.md")
    assert refused["readable"] is False
    assert "limit" in refused["error"]


def test_files_outside_the_budget_leak_no_term_presence():
    """Continuing past the budget would keep testing files the budget did not
    pay for, and a hit from one of them is the information the bound withholds."""
    corpus = _Corpus(50, body="nothing interesting")
    corpus.files["docs/specs/f0049.md"] = "the secret term appears here"
    _, _, _, search = _tools(corpus)

    out = search("secret term")

    assert out["paths"] == [], (
        "a file beyond the budget was reported as containing the term"
    )
    assert out["truncated"] is True


def test_the_scan_order_is_deterministic():
    """Which files fall inside the budget must not depend on dictionary order,
    or the bound moves between runs and cannot be audited."""
    first = _Corpus(60)
    second = _Corpus(60)
    _, _, _, search_one = _tools(first)
    _, _, _, search_two = _tools(second)

    assert search_one("retention")["paths"] == search_two("retention")["paths"]


def test_bytes_are_capped_per_file_inside_search():
    """`read_file` truncated at MAX_BYTES_PER_READ and search did not, so one
    search pulled whole files into memory and into the model's context."""
    corpus = _Corpus(2, body="x" * (tools.MAX_BYTES_PER_READ * 4))
    _, _, _, search = _tools(corpus)

    out = search("xxx")

    assert out["bytes_read"] <= tools.MAX_BYTES_PER_READ * out["files_scanned"]


def test_scope_still_applies_to_search():
    """The one thing search did enforce must survive the rewrite."""
    corpus = _Corpus(2)
    corpus.files["secrets/keys.txt"] = "retention"
    _, _, _, search = _tools(corpus)

    out = search("retention")

    assert "secrets/keys.txt" not in out["paths"]


def test_every_read_search_makes_is_in_the_log():
    """Provenance: a read nobody can point at is a read nobody can audit."""
    corpus = _Corpus(5)
    log, _, _, search = _tools(corpus)

    search("retention")
    recorded = [c for c in log.calls if c["tool"] == "search:read"]

    assert len(recorded) == corpus.served
    assert all(c["arg"] in corpus.files for c in recorded)


@pytest.mark.parametrize("count", [0, 1, tools.MAX_READS_PER_RUN])
def test_it_does_not_fall_over_at_the_boundaries(count):
    corpus = _Corpus(count)
    _, _, _, search = _tools(corpus)

    out = search("retention")

    assert out["files_scanned"] <= tools.MAX_READS_PER_RUN
    assert out["reads_remaining"] >= 0
