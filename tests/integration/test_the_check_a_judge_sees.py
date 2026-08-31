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


# ---------------------------------------------------------------------------
# The second opinion, on the surface a reviewer opens
# ---------------------------------------------------------------------------
#
# A second model that only writes to a log is a second model nobody can check.
# The requirement was that a human-facing surface changes when it adds
# something, so these assert the change and — the half that is easy to skip —
# that the surface stays quiet when the review did not happen.


def _review(count, model="google/gemma-4-26b-a4b-it-maas"):
    return _entry(
        "critic.independent_review",
        {"model": model, "advisory_count": count, "status": "concerns_found"},
    )


def test_the_check_names_the_second_model_when_it_reviewed(monkeypatch):
    seen = _conclusion_for(
        [_entry("fleet.dispatch"), _entry("plan.proposed"), _review(2)], monkeypatch
    )

    assert "google/gemma-4-26b-a4b-it-maas" in seen["summary"]
    assert "2 advisory note(s)" in seen["summary"]


def test_it_says_so_on_a_run_with_no_plan_too(monkeypatch):
    """The three completion branches are three separate strings, and a line
    added to one of them is absent from the other two."""
    seen = _conclusion_for(
        [_entry("fleet.dispatch"), _entry("plan.review_only"), _review(1)], monkeypatch
    )

    assert "reviewed the draft" in seen["summary"]


def test_and_on_a_run_with_nothing_to_govern(monkeypatch):
    seen = _conclusion_for(
        [_entry("fleet.dispatch"), _entry("run.nothing_to_govern"), _review(0)],
        monkeypatch,
    )

    assert "reviewed the draft" in seen["summary"]


def test_the_check_says_nothing_about_a_review_that_did_not_run(monkeypatch):
    """The line is derived from the thread, not from the deployment. An
    environment variable proves a deployment intends to call a model; it proves
    nothing about whether one answered, and a check run that claims a second
    opinion it never got is the exact failure this project keeps finding in
    itself."""
    seen = _conclusion_for(
        [_entry("fleet.dispatch"), _entry("plan.proposed")], monkeypatch
    )

    assert "second model" not in seen["summary"]
    assert "gemma" not in seen["summary"].lower()


def test_the_second_opinion_cannot_change_the_conclusion(monkeypatch):
    """It reports; it does not decide. A run that passed still passes when the
    critic had four things to say, because the advisories are for the human and
    the conclusion is the deterministic result."""
    clean = _conclusion_for([_entry("fleet.dispatch"), _entry("plan.proposed")], monkeypatch)
    noisy = _conclusion_for(
        [_entry("fleet.dispatch"), _entry("plan.proposed"), _review(4)], monkeypatch
    )

    assert clean["conclusion"] == "success"
    assert noisy["conclusion"] == "success"


# ---------------------------------------------------------------------------
# A picture in a commit is not a failing check
# ---------------------------------------------------------------------------
#
# Found on this repository's own pull request. Adding `docs/architecture.png`
# turned the Mitos check red with "1 file(s) came back with no patch". The
# refusal is right, because a verdict over part of a change is a verdict about a
# different change. The conclusion was not: `failure` tells every contributor
# who adds an image that they broke something, on the one surface a judge sees.
#
# Two different pieces of news, so two different conclusions. A file that has no
# diff because it is binary is normal and reports `neutral`. A file count that
# does not match, or a read that drifted off the delivered head, means the read
# itself was unreliable and still reports `failure`.


def _diff(**kw):
    import service.main as main

    return main.Diff(files=[], whole=False, **kw)


def test_a_binary_file_is_not_reported_as_a_failure():
    seen = _diff(reason="1 file(s) came back with no patch: ['docs/a.png']")

    assert seen.read_is_in_doubt is False


def test_a_read_that_does_not_add_up_still_fails(monkeypatch):
    """The premise of the split. If nothing sets this, the branch below is
    dead and every unreadable change reports neutral, which is the same
    mistake pointing the other way."""
    import service.main as main

    calls = []

    def fake_get(url, **kw):
        calls.append(url)

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                # GitHub says three files changed and returns one.
                if "/files" in url:
                    return [{"filename": "a.py", "patch": "@@ -1 +1 @@"}]
                return {"changed_files": 3, "head": {"sha": "abc"}}

            headers: dict = {}

        return R()

    monkeypatch.setattr(main.httpx, "get", fake_get)
    out = main._fetch_diff("o/r", 1, head_sha="abc")

    assert out.whole is False
    assert out.read_is_in_doubt is True, (
        "a file count that does not match is a read nobody should trust, and it "
        "must not be softened into the branch meant for binary files"
    )
