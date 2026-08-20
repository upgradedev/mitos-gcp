"""The bound on what a specialist may open.

The product's own one-line description promises "bounded reads". For most of
this project's life there was nothing to bound: specialists were handed the diff
and returned prose, so the fleet had no agency and every outcome was decided by a
regular expression elsewhere. That is a workflow with text generation attached.

Now a specialist chooses what to open, which is where the agency is, and which
is precisely why the bound has to be enforced in code rather than asked for in a
prompt. An agent that genuinely decides where to look is an agent that can decide
to look somewhere it should not.
"""

from __future__ import annotations

import pytest

from mitos.tools import (
    ALLOWED_PREFIXES,
    MAX_BYTES_PER_READ,
    MAX_READS_PER_RUN,
    DictCorpus,
    OutOfScope,
    ReadBudgetExhausted,
    ReadLog,
    check_read,
    make_tools,
)


def _tools():
    log = ReadLog()
    list_paths, read_file, search = make_tools(DictCorpus(), log)
    return list_paths, read_file, search, log


# --------------------------------------------------------------------------
# The bound, as a pure function
# --------------------------------------------------------------------------


def test_a_path_in_scope_is_allowed():
    check_read("docs/specs/customer-record.md", 0)  # does not raise


@pytest.mark.parametrize(
    "path",
    [
        "../../etc/passwd",
        "docs/../../secrets.env",
        "/etc/passwd",
        "C:\\Windows\\System32\\config",
        "services/../../../root/.ssh/id_rsa",
    ],
)
def test_escaping_the_repository_is_refused(path):
    with pytest.raises(OutOfScope):
        check_read(path, 0)


@pytest.mark.parametrize("path", [".env", "secrets.yaml", "terraform.tfstate"])
def test_a_path_outside_the_declared_prefixes_is_refused(path):
    with pytest.raises(OutOfScope) as exc:
        check_read(path, 0)
    assert "outside the readable scope" in str(exc.value)


def test_the_read_budget_is_finite():
    check_read("docs/a.md", MAX_READS_PER_RUN - 1)
    with pytest.raises(ReadBudgetExhausted):
        check_read("docs/a.md", MAX_READS_PER_RUN)


def test_every_allowed_prefix_actually_admits_something():
    """A prefix nobody can reach is a comment, not a policy."""
    corpus = DictCorpus()
    for prefix in ALLOWED_PREFIXES:
        assert any(p.startswith(prefix) for p in corpus.paths()), prefix


# --------------------------------------------------------------------------
# The tools an agent is handed
# --------------------------------------------------------------------------


def test_listing_only_shows_what_is_in_scope():
    list_paths, _, _, _ = _tools()
    for path in list_paths("*")["paths"]:
        assert any(path.startswith(p) for p in ALLOWED_PREFIXES), path


def test_reading_in_scope_returns_content():
    _, read_file, _, log = _tools()
    out = read_file("registers/retention.md")
    assert out["readable"] is not False
    assert "Lawful basis" in out["content"]
    assert log.reads == 1


def test_reading_out_of_scope_returns_an_error_rather_than_raising():
    """The agent must be able to see the refusal and carry on, not crash."""
    _, read_file, _, log = _tools()
    out = read_file("../../.env")
    assert out["readable"] is False
    assert "traversal" in out["error"]
    assert log.reads == 0, "a refused read must not consume the budget"


def test_a_refused_read_is_recorded_as_denied():
    _, read_file, _, log = _tools()
    read_file("secrets.env")
    assert log.as_dict()["denied"] == 1


def test_a_missing_file_is_reported_not_invented():
    _, read_file, _, _ = _tools()
    out = read_file("docs/specs/does-not-exist.md")
    assert out["readable"] is False
    assert "no such file" in out["error"]


def test_the_budget_stops_a_runaway_agent():
    _, read_file, _, log = _tools()
    paths = DictCorpus().paths()
    for _ in range(MAX_READS_PER_RUN + 5):
        for p in paths:
            read_file(p)
    assert log.reads <= MAX_READS_PER_RUN


def test_a_single_read_is_size_capped():
    log = ReadLog()
    big = {"docs/big.md": "x" * (MAX_BYTES_PER_READ * 3)}
    _, read_file, _ = make_tools(DictCorpus(big), log)
    out = read_file("docs/big.md")
    assert len(out["content"]) == MAX_BYTES_PER_READ
    assert out["truncated"] is True


def test_search_only_reaches_files_in_scope():
    log = ReadLog()
    corpus = DictCorpus({**DictCorpus()._files, "secrets.env": "retention"})
    _, _, search = make_tools(corpus, log)
    assert "secrets.env" not in search("retention")["paths"]


# --------------------------------------------------------------------------
# The read log, which is the evidence that any of this happened
# --------------------------------------------------------------------------


def test_the_log_records_the_sequence_not_just_the_count():
    list_paths, read_file, _, log = _tools()
    list_paths("registers/*")
    read_file("registers/retention.md")
    d = log.as_dict()
    assert d["tool_calls"] == 2
    assert d["reads"] == 1
    assert "list_paths(registers/*)" in d["sequence"][0]
    assert "read_file(registers/retention.md) -> ok" in d["sequence"][1]


def test_the_register_says_what_a_compliance_agent_needs_to_conclude():
    """The corpus has to make the interesting answer reachable, or the agent
    cannot do anything a template could not."""
    body = DictCorpus().read("registers/retention.md")
    assert "mobileNumber" not in body, (
        "the register already lists the field, so there is nothing to find"
    )
    assert "no recorded lawful basis" in body
