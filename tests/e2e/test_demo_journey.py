"""The end-to-end user journey: what a judge actually watches.

Drives `mitos.demo` exactly as the README tells a stranger to run it, then
asserts on what appeared on screen. This is the layer that would catch a demo
that still exits 0 while having stopped showing the thing it is meant to show.
"""

from __future__ import annotations

import re

from mitos.demo import main


def _run(capsys, *argv) -> str:
    assert main(["--ledger", "memory", "--yes", "--fast", *argv]) == 0
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_the_journey_shows_every_beat_a_judge_is_promised(capsys):
    out = _run(capsys)
    for beat in (
        "THE CATALOG",
        "TRIGGER",
        "DISPATCH",
        "RECALL",
        "ESCALATE",
        "GATE",
        "REPAIR",
        "APPROVAL",
        "WRITE",
        "THE THREAD",
    ):
        assert beat in out, f"the demo no longer shows {beat}"


def test_the_gate_visibly_rejects_then_passes(capsys):
    out = _run(capsys)
    assert "draft 1: FAIL" in out, "no rejection on screen; the demo has no gate to show"
    assert "draft 2: PASS" in out
    assert "secret-leak" in out and "prompt-injection" in out


def test_the_router_visibly_skips_a_companion(capsys):
    out = _run(capsys)
    assert "skipped: compliance-companion" in out, (
        "the branch point is not visible, so the fleet looks like a pipeline"
    )


def test_the_second_run_visibly_recalls_the_first(capsys):
    out = _run(capsys)
    assert "not re-filing it" in out


def test_the_approval_card_shows_a_full_sha256(capsys):
    out = _run(capsys)
    hashes = re.findall(r"\b[0-9a-f]{64}\b", out)
    assert hashes, "the approval card shows no content address"


def test_the_reader_identity_is_shown_as_unable_to_write(capsys):
    out = _run(capsys)
    assert "reader may call write_spec_repo: False" in out


def test_the_seed_is_labelled_synthetic_on_screen(capsys):
    """It is scaffolding, not a record of anything that happened, and a viewer
    must be able to tell."""
    out = _run(capsys)
    assert "SYNTHETIC SEED DATA" in out


def test_the_journey_is_deterministic_apart_from_run_ids(capsys):
    a = re.sub(r"\b[0-9a-f]{8,}\b|\d\d:\d\d:\d\d", "X", _run(capsys))
    b = re.sub(r"\b[0-9a-f]{8,}\b|\d\d:\d\d:\d\d", "X", _run(capsys))
    assert a == b, "the demo is not reproducible, so one take can differ from the next"
