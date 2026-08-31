"""The most serious refusal this product makes arrived naming no file.

`route_with_model` widens the dispatch by appending signals the model raised.
Those classify the change rather than a file, so they carry `path=""`. Rendered
naively, the GDPR Article 9 refusal, the one that says a Data Protection Impact
Assessment is required, came out as:

    `` introduces what looks like special-category data under GDPR Article 9

Seen in a live run, in the reason a human is supposed to act on. The ordinary
personal-data path had the same defect in its findings, and both built a
citation list containing a single empty string, which looks populated and points
nowhere.

A refusal a human cannot act on is barely better than no refusal.

Standard library only, per `test_offline_suite_stays_offline.py`.
"""

from __future__ import annotations

from mitos import fleet
from mitos.envelope import Status
from mitos.fixtures import BACKLOG

PR = [p for p in BACKLOG if p.number == 4483][0]


def _with_model_signal(evidence: str) -> list:
    """A signal the way `route_with_model` builds one: no path, ever."""
    return list(fleet.detect_signals(PR)) + [
        fleet.Signal("personal-data", evidence, "")
    ]


def _run(signals):
    return fleet.run_specialist("compliance-companion", PR, signals, analyst=None)


def test_the_article_9_refusal_names_the_change_when_it_cannot_name_a_file():
    out = _run(_with_model_signal("raised by the model: health dependency data"))

    assert out.status is Status.BLOCKED
    assert "``" not in out.reason, f"empty backticks in a refusal: {out.reason}"
    assert "this change" in out.reason


def test_the_ordinary_finding_names_the_change_too():
    out = _run(_with_model_signal("raised by the model: an email address"))

    assert out.findings
    assert not any("``" in f for f in out.findings), out.findings


def test_no_refusal_cites_an_empty_string():
    """A citation list of one empty string looks populated and opens nothing."""
    for evidence in (
        "raised by the model: health dependency data",
        "raised by the model: an email address",
    ):
        out = _run(_with_model_signal(evidence))
        assert "" not in out.citations, out.citations
        assert out.citations, "nothing to open at all"
        assert all(c in PR.paths() for c in out.citations), out.citations


def test_a_signal_with_a_real_path_still_names_that_path():
    """The counterweight: falling back to the whole change everywhere would
    lose the precision the deterministic rules do have."""
    signals = fleet.detect_signals(PR) + [
        fleet.Signal(
            "personal-data",
            "health condition column",
            "services/customer/migrations/V222__vuln_code.sql",
        )
    ]
    out = _run(signals)

    assert "services/customer/migrations/V222__vuln_code.sql" in out.reason
    assert out.citations == ["services/customer/migrations/V222__vuln_code.sql"]
