"""Three services in the diagram, two in the request path.

`run_chore` called `evaluate` in this process. The evaluator was deployed, held
its own service account, refused anonymous callers, and did no work: nothing
anywhere in the repository referenced an evaluator URL. The architecture the
README describes was true of the deployment and not of the run.

It is asked now, over an authenticated call with a token audience bound to it,
and the reader fails closed if it cannot be reached. A reader that judges its
own draft when the gate is down is a reader with no gate, and that failure is
silent: every run still produces a verdict and nothing says which process
decided it.
"""

from __future__ import annotations

import pytest


def _main(monkeypatch, role="reader"):
    monkeypatch.setenv("MITOS_LEDGER", "memory")
    monkeypatch.setenv("MITOS_ROLE", role)
    import service.main as main

    monkeypatch.setattr(main, "ROLE", role)
    return main


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


def test_only_the_evaluator_serves_the_gate(monkeypatch):
    """All three deployments carry every route. The identity decides."""
    from fastapi.testclient import TestClient

    main = _main(monkeypatch, role="reader")
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/internal/evaluate", json={"draft": "x", "known_paths": []}
    )

    assert response.status_code == 403
    assert "does not judge drafts" in response.json()["detail"]


def test_the_evaluator_returns_a_verdict_and_says_who_decided(monkeypatch):
    from fastapi.testclient import TestClient

    main = _main(monkeypatch, role="evaluator")
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/internal/evaluate", json={"draft": "", "known_paths": []}
    )

    assert response.status_code == 200
    body = response.json()
    assert "passed" in body and "checks_run" in body
    assert body["decided_by"]["role"] == "evaluator"
    assert "build_sha" in body["decided_by"]


def test_the_gate_still_refuses_an_empty_draft_through_the_service(monkeypatch):
    """The non-empty check is the one that catches a run with nothing in it, and
    moving the gate must not lose it."""
    from fastapi.testclient import TestClient

    main = _main(monkeypatch, role="evaluator")
    body = TestClient(main.app, raise_server_exceptions=False).post(
        "/internal/evaluate", json={"draft": "", "known_paths": []}
    ).json()

    assert body["passed"] is False
    assert any(f["check"] == "non-empty" for f in body["findings"])


def _with_a_token(monkeypatch):
    """Make `import google.oauth2.id_token` resolve to a stub.

    Patching `sys.modules` alone is not enough: the import takes the ATTRIBUTE
    off `google.oauth2` when the package already has one. The same trap is
    recorded at length in `test_approving_a_write.py`, and two tests here passed
    for the wrong reason before this existed, raising AttributeError from the
    patch rather than from what they were checking.
    """
    import sys

    import google.auth.transport.requests  # noqa: F401
    import google.oauth2

    stub = type("m", (), {"fetch_id_token": staticmethod(lambda *a, **k: "t")})
    monkeypatch.setitem(sys.modules, "google.oauth2.id_token", stub)
    monkeypatch.setattr(google.oauth2, "id_token", stub, raising=False)


# ---------------------------------------------------------------------------
# The caller
# ---------------------------------------------------------------------------


def test_no_url_means_the_gate_runs_here(monkeypatch):
    """Offline and in the recorded demo, which is why they need no credential."""
    main = _main(monkeypatch)
    monkeypatch.delenv("MITOS_EVALUATOR_URL", raising=False)

    assert main._gate() is None


def test_a_url_means_the_gate_is_delegated(monkeypatch):
    main = _main(monkeypatch)
    monkeypatch.setenv("MITOS_EVALUATOR_URL", "https://evaluator.example.test/")

    gate = main._gate()

    assert isinstance(gate, main.RemoteGate)
    assert gate.url == "https://evaluator.example.test"


def test_an_unreachable_evaluator_stops_the_run(monkeypatch):
    """Fail closed. The alternative is a reader that judges its own draft and
    says nothing about having done so."""
    main = _main(monkeypatch)
    gate = main.RemoteGate("https://evaluator.example.test")

    def _boom(*_a, **_k):
        raise ConnectionError("no route")

    monkeypatch.setattr(main.httpx, "post", _boom)
    _with_a_token(monkeypatch)

    with pytest.raises(main.EvaluatorUnavailable) as caught:
        gate("a draft", known_paths=[])

    assert "nothing was judged" in str(caught.value)


def test_an_answer_that_is_not_a_verdict_stops_the_run(monkeypatch):
    """A 200 carrying the wrong shape is the more dangerous failure: it looks
    like success."""
    main = _main(monkeypatch)
    gate = main.RemoteGate("https://evaluator.example.test")

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"something": "else"}

    monkeypatch.setattr(main.httpx, "post", lambda *a, **k: _Response())
    _with_a_token(monkeypatch)

    with pytest.raises(main.EvaluatorUnavailable):
        gate("a draft", known_paths=[])


def test_a_verdict_survives_the_round_trip(monkeypatch):
    """Findings and their severities have to arrive intact, or the gate is
    weaker across the wire than it was in process."""
    main = _main(monkeypatch)
    gate = main.RemoteGate("https://evaluator.example.test")

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "passed": False,
                "injection_attempt": True,
                "checks_run": ["secret-leak", "non-empty"],
                "findings": [
                    {
                        "severity": "HIGH",
                        "check": "secret-leak",
                        "detail": "a credential",
                        "evidence": "line 4",
                    }
                ],
                "advisories": [],
                "decided_by": {"role": "evaluator", "build_sha": "abc1234"},
            }

    monkeypatch.setattr(main.httpx, "post", lambda *a, **k: _Response())
    _with_a_token(monkeypatch)

    verdict = gate("a draft", known_paths=[])

    assert verdict.passed is False
    assert verdict.injection_attempt is True
    assert verdict.checked == ["secret-leak", "non-empty"]
    assert verdict.findings[0].check == "secret-leak"
    assert verdict.findings[0].severity == "HIGH"
    assert gate.decided_by["build_sha"] == "abc1234"
