"""What the fleet tells GitHub, which is the only part a judge sees.

The companion to `tests/unit/test_a_change_with_nothing_to_govern.py`, which
covers what the fleet decides. This covers the mapping from a finished run to a
check conclusion, and nothing covered `_complete_analysis_check` in either
direction before.

It lives in `tests/integration/` rather than beside its sibling because it
imports `service.main`, and `test_offline_suite_stays_offline.py` forbids that in
the unit suite. That rule caught this file on the first run, which is the rule
working: the offline suite is what a stranger runs with no cloud account, and one
import of the service would have made it need FastAPI to collect.
"""

from __future__ import annotations

class _Led:
    def __init__(self, entries):
        self._entries = entries

    def all(self):
        return self._entries


class _Delivery:
    repository = "upgradedev/mitos-gcp"
    delivery_id = "d1"
    head_sha = "abc123"
    number = 102


def _conclusion_for(entries, monkeypatch):
    import service.main as main

    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return kwargs.get("check_run_id")

    monkeypatch.setattr(main, "_safe_github_check", capture)
    main._complete_analysis_check(
        led=_Led(entries), delivery=_Delivery(), installation_id=7, check_run_id=99
    )
    return seen


def _entry(kind, payload=None):
    from mitos.ledger import Entry

    return Entry(kind=kind, actor="a", subject="s", payload=payload or {}, run_id="d1")


def test_nothing_to_govern_is_reported_as_neutral_not_as_a_pass(monkeypatch):
    """`neutral` renders as "not applicable"; `success` claims something was
    checked and cleared. Nothing was checked, so `success` would be a small lie
    on the endpoint the whole entry is about."""
    seen = _conclusion_for(
        [_entry("fleet.dispatch"), _entry("run.nothing_to_govern")], monkeypatch
    )

    assert seen.get("conclusion") == "neutral", (
        f"a run with nothing to govern reported {seen.get('conclusion')!r}"
    )
    assert "nothing to assess" in seen.get("summary", "")


def test_a_finding_is_still_reported_as_action_required(monkeypatch):
    """The counterweight. A change that made everything neutral would pass the
    test above and destroy the product."""
    seen = _conclusion_for(
        [_entry("finding.raised", {"finding": "x"})], monkeypatch
    )

    assert seen.get("conclusion") == "action_required"


def test_a_failed_gate_is_reported_as_action_required(monkeypatch):
    """A verdict can fail with no `finding.*` entry, and that must not read as a
    pass. This is the branch that fired on the live run."""
    seen = _conclusion_for(
        [_entry("evaluator.verdict", {"passed": False})], monkeypatch
    )

    assert seen.get("conclusion") == "action_required"


def test_a_clean_run_is_reported_as_success(monkeypatch):
    """Something was assessed and nothing was wrong, which is not the same
    statement as "nothing was assessed"."""
    seen = _conclusion_for(
        [_entry("specialist.response"), _entry("evaluator.verdict", {"passed": True})],
        monkeypatch,
    )

    assert seen.get("conclusion") == "success"


def test_no_check_is_posted_without_an_installation(monkeypatch):
    """Offline and in the demo there is no App, and a check run cannot be
    invented. ADR-013: a failure to report is never a failure to analyse."""
    import service.main as main

    calls = []
    monkeypatch.setattr(main, "_safe_github_check", lambda **k: calls.append(k))

    main._complete_analysis_check(
        led=_Led([_entry("run.nothing_to_govern")]),
        delivery=_Delivery(), installation_id=None, check_run_id=99,
    )
    main._complete_analysis_check(
        led=_Led([_entry("run.nothing_to_govern")]),
        delivery=_Delivery(), installation_id=7, check_run_id=None,
    )

    assert calls == [], f"a check was posted with no App to post it: {calls}"
