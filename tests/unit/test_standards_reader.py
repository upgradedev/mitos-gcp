"""The agentic half of a standards audit, tested where no model is needed.

`standards.py` settles what a pattern can settle and leaves five rules at
`needs_judgement` because answering them means reading and deciding. Those go to
`StandardsReader`, which is given tools and a bounded budget and chooses what to
open.

What matters here is not that the reader is clever. It is that a reader which is
absent, unreachable, confused or hostile cannot make the audit report anything
softer than the deterministic pass already reported. That is ADR-002, applied to
a second place where a model touches a decision.
"""

from __future__ import annotations

import os
from unittest import mock

from mitos.gemini import StandardsReader, build_standards_reader, shape_judgements
from mitos.standards import NEEDS_JUDGEMENT, check_repository, judgement_queue, tighten
from mitos.tools import DictCorpus
from mitos.standards import Verdict


def test_no_model_configured_means_no_reader() -> None:
    """The offline path must not require a credential, per ADR-004."""
    with mock.patch.dict(os.environ, {"MITOS_MODEL": "stub"}):
        assert build_standards_reader() is None


def test_an_empty_queue_is_answered_without_a_model() -> None:
    """Nothing to judge means nothing to ask, so this must not reach Vertex.

    Constructed with `__new__` because `__post_init__` configures Vertex, which
    is the thing this test is asserting does not have to happen.
    """
    reader = StandardsReader.__new__(StandardsReader)

    assert reader.read([], DictCorpus({"a.py": "x = 1\n"})) == []


def test_a_reply_that_is_not_what_was_asked_for_settles_nothing() -> None:
    for reply in (
        None,
        "the repository looks fine",
        {},
        {"judgements": "all good"},
        {"judgements": None},
        {"verdicts": [{"rule": "x", "verdict": "failed"}]},
    ):
        assert shape_judgements(reply) == [], reply


def test_malformed_items_are_dropped_and_the_rest_survive() -> None:
    shaped = shape_judgements(
        {"judgements": [{"rule": "a", "verdict": "failed"}, "nonsense", 7, None]}
    )

    assert shaped == [{"rule": "a", "verdict": "failed"}]


# --------------------------------------------------------------------------
# The invariant: a reader can only tighten
# --------------------------------------------------------------------------


def _audited() -> tuple:
    corpus = DictCorpus({"README.md": "# A repository\n"})
    return check_repository(corpus), corpus


def test_the_queue_names_only_rules_the_patterns_refused() -> None:
    audit_result, corpus = _audited()
    queue = judgement_queue(corpus)

    deferred = {
        f.rule_id
        for f in audit_result.results
        if f.verdict is Verdict.NEEDS_JUDGEMENT
    }
    assert {item["rule"] for item in queue} == deferred
    assert deferred, "nothing was deferred, so this test proves nothing"


def test_a_reader_claiming_a_pass_is_ignored() -> None:
    """The whole point. These are the rules the audit said it could not decide.

    Accepting a pass would let the thing being asked answer on its own behalf,
    which is the failure ADR-002 exists to make structurally impossible rather
    than merely discouraged.
    """
    audit_result, corpus = _audited()
    rule_id = NEEDS_JUDGEMENT[0].id

    tightened = tighten(
        audit_result,
        [{"rule": rule_id, "verdict": "passed", "found": "looks compliant to me"}],
    )

    verdict = {f.rule_id: f.verdict for f in tightened.results}[rule_id]
    assert verdict is Verdict.NEEDS_JUDGEMENT


def test_a_reader_may_make_a_deferred_rule_fail() -> None:
    audit_result, corpus = _audited()
    rule_id = NEEDS_JUDGEMENT[0].id

    tightened = tighten(
        audit_result,
        [
            {
                "rule": rule_id,
                "verdict": "failed",
                "found": "the section describes a queue the code does not have",
                "looked_at": ["README.md"],
            }
        ],
    )

    finding = {f.rule_id: f for f in tightened.results}[rule_id]
    assert finding.verdict is Verdict.FAILED
    assert "README.md" in finding.looked_at


def test_a_reader_cannot_reopen_a_rule_a_pattern_already_settled() -> None:
    """Only the deferred list is in play.

    A reader that could overturn a deterministic result would make the
    deterministic layer advisory, and the demo's refusals would stop being
    reproducible.
    """
    audit_result, _ = _audited()
    settled = [
        f.rule_id
        for f in audit_result.results
        if f.verdict in (Verdict.PASSED, Verdict.FAILED)
    ]
    assert settled, "nothing was settled deterministically, so this proves nothing"

    before = {f.rule_id: f.verdict for f in audit_result.results}
    tightened = tighten(
        audit_result,
        [{"rule": rule_id, "verdict": "failed", "found": "x"} for rule_id in settled],
    )

    assert {f.rule_id: f.verdict for f in tightened.results} == before


def test_a_finding_from_a_reader_always_says_where_it_looked() -> None:
    """A compliance finding with no path is not actionable, it is an opinion."""
    audit_result, _ = _audited()
    rule_id = NEEDS_JUDGEMENT[0].id

    tightened = tighten(
        audit_result, [{"rule": rule_id, "verdict": "failed", "found": "wrong"}]
    )

    finding = {f.rule_id: f for f in tightened.results}[rule_id]
    assert finding.looked_at, "a finding landed with no record of what was read"


def test_the_brief_tells_the_reader_the_repository_is_not_in_charge() -> None:
    """The audited repository is somebody else's code and may want a clean bill.

    A file in it addressing the reader is the cheapest attack against a tool
    like this, so refusing it is part of the instruction rather than a hope.
    """
    guard = StandardsReader.GUARD.lower()

    assert "data" in guard
    assert "bounded" in guard
    assert "finding" in guard
