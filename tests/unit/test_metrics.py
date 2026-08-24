"""The summary figures, each asserted on a thread whose answer is known.

Every case here builds its own small thread and asserts a number derived from
it, rather than the numbers observed on one deployment, which are an
observation and not an invariant. That rule is inherited from the dashboard
tests and it matters more here, because these figures are the ones a buyer
repeats.

Three ways a figure on this page lies, and there is a case for each:

It counts the wrong unit. The webhook path records two trigger entries for one
run, an escalation is written from two different places, and a write that
executed is not a write that published. A count that adds those together is
wrong in exactly the direction that flatters the product.

It prints a zero it cannot support. A window holding nothing but control-plane
entries knows nothing about runs, and "0 pull requests triggered" is a claim
about the world rather than about the window. Those stages have to come back
None, and the empty cases below assert that no funnel count is 0 when the
window cannot see a single run.

It answers on too little. The median is the one figure a buyer leans on, so it
is asserted at two runs where it must refuse, at three where it must appear, at
an even count where the rounding is pinned, and against a mean that would give
a different answer.

Fixtures are built by the real producers. `Entry.to_doc` writes the entry shape
and `chore.escalate_on_wake` writes the wake escalations, so the `woken_by`
stamp and the `parent_id` pairing under test are the ones production writes and
not a restatement of them here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mitos.chore import SUBJECT, escalate_on_wake
from mitos.ledger import Entry, InMemoryLedger
from service.metrics import summarise

BASE = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
TONES = {"good", "bad", "warn", "plain", "unknown"}


def at(seconds: int = 0, days: int = 0) -> str:
    return (BASE + timedelta(days=days, seconds=seconds)).isoformat()


def entry(kind, run_id, when, actor="architect-leader", subject=SUBJECT, parent=None, **payload):
    return Entry(
        kind=kind,
        actor=actor,
        subject=subject,
        payload=dict(payload),
        parent_id=parent,
        run_id=run_id,
        recorded_at=when,
    ).to_doc()


def a_run(run_id, *, start=0, to_card=30, day=0, webhook=False, card=True,
          parked=False, trigger=True, specialists=2):
    """One run, in the order and with the kinds `run_chore` appends.

    `to_card` is seconds from the run's first trigger to `plan.proposed`, which
    is the interval the median is taken over.
    """
    out = []
    if trigger and webhook:
        out.append(entry("trigger.webhook", run_id, at(start, day), actor="github", pr=7))
        # The chore the delivery starts records its own trigger, under the same
        # run id, one second later. Counting entries would count this run twice.
        out.append(entry("trigger.pull_request", run_id, at(start + 1, day), actor="webhook", pr=7))
    elif trigger:
        out.append(entry("trigger.pull_request", run_id, at(start, day), actor="webhook", pr=7))
    out.append(entry("fleet.dispatch", run_id, at(start + 2, day)))
    for index in range(specialists):
        out.append(
            entry(
                "specialist.response",
                run_id,
                at(start + 3 + index, day),
                actor="compliance-companion",
                status="blocked" if (parked and index == 0) else "ok",
            )
        )
    if parked:
        out.append(
            entry(
                "item.parked",
                run_id,
                at(start + 5, day),
                actor="compliance-companion",
                status="blocked",
                reason="Article 9 data, a human has to look",
            )
        )
        return out
    out.append(entry("evaluator.verdict", run_id, at(start + 6, day), actor="evaluator-companion", passed=True))
    out.append(
        entry(
            "guard.exercised",
            run_id,
            at(start + 7, day),
            actor="documentation-companion",
            attempted=True,
            denied=True,
            tool_executed=False,
            role="reader",
        )
    )
    if card:
        out.append(
            entry(
                "plan.proposed",
                run_id,
                at(start + to_card, day),
                actor="documentation-companion",
                path="docs/specs/customer-record.md",
                plan_hash="a" * 64,
            )
        )
    return out


def a_deferral(index: int) -> Entry:
    """A seeded deferral, as an `Entry` so a wake can name it as its parent."""
    return Entry(
        kind="finding.deferred",
        actor="compliance-companion",
        subject=SUBJECT,
        payload={"finding": f"finding {index}", "expires_on": "2026-08-01"},
        run_id="seed",
        recorded_at=at(index),
    )


def woken_over(deferrals: list[Entry]) -> list[dict]:
    """Wake escalations from the function that writes them in production."""
    return [e.to_doc() for e in escalate_on_wake(InMemoryLedger(), deferrals)]


def tile(summary: dict, key: str) -> dict:
    return next(t for t in summary["headline"] if t["key"] == key)


def stage(summary: dict, name: str) -> dict:
    return next(s for s in summary["funnel"] if s["stage"] == name)


# ---------------------------------------------------------------------------
# The shape of the contract, so the two halves can be built apart.
# ---------------------------------------------------------------------------


def test_the_contract_shape_holds_on_a_full_thread():
    summary = summarise(a_run("r1") + a_run("r2", start=100), now=at(600))

    assert set(summary) == {"window", "headline", "funnel", "activity", "unknown"}
    assert set(summary["window"]) == {"entries", "runs", "first", "last"}
    for item in summary["headline"]:
        assert set(item) == {
            "key",
            "label",
            "value",
            "unit",
            "caption",
            "method",
            "tone",
            "primary",
        }
        # A tile reads short and checks long. `caption` is one line for
        # somebody deciding whether to care; `method` is the arithmetic,
        # rendered under the page so no figure has to be taken on trust.
        assert item["caption"] and len(item["caption"]) <= 90, item["key"]
        assert item["method"], item["key"]
        assert isinstance(item["value"], str) and item["value"]
        assert item["tone"] in TONES
    for step in summary["funnel"]:
        assert set(step) == {"stage", "count", "note"}
        assert step["count"] is None or isinstance(step["count"], int)
        assert step["note"]
    assert [s["stage"] for s in summary["funnel"]] == [
        "triggered", "specialists woken", "gate ran", "card produced", "published"
    ]
    for day in summary["activity"]:
        assert set(day) == {"day", "runs"}
    assert all(isinstance(u, str) for u in summary["unknown"])


def test_window_counts_real_runs_and_bounds_do_not_depend_on_order():
    entries = a_run("r1") + a_run("r2", start=100)
    entries += [entry("finding.deferred", "seed", at(5), finding="old")]
    entries += woken_over([a_deferral(1)])

    summary = summarise(list(reversed(entries)))

    assert summary["window"]["entries"] == len(entries)
    # `seed` and `watch` carry a run id and are not runs.
    assert summary["window"]["runs"] == 2
    assert summary["window"]["first"] == at(0)


def test_an_unreadable_timestamp_is_said_rather_than_dropped_quietly():
    entries = a_run("r1")
    entries.append(entry("specialist.response", "r1", "not a timestamp"))

    summary = summarise(entries)

    assert any("could not be read" in u for u in summary["unknown"])
    assert summary["window"]["first"] == at(0)


# ---------------------------------------------------------------------------
# Pull requests, cards, and the run that stopped early.
# ---------------------------------------------------------------------------


def test_a_webhook_run_is_one_pull_request_not_two():
    summary = summarise(a_run("r1", webhook=True) + a_run("r2", start=100))

    handled = tile(summary, "runs_triggered")
    assert handled["value"] == "2"
    # The literal entry count is not hidden, it is in the caption.
    assert "3 trigger entries over 2 runs" in handled["method"]


def test_cards_are_counted_and_the_funnel_narrows():
    summary = summarise(a_run("r1") + a_run("r2", start=100) + a_run("r3", start=200))

    assert tile(summary, "cards")["value"] == "3"
    assert [s["count"] for s in summary["funnel"]] == [3, 3, 3, 3, 0]
    assert "6 specialist responses" in stage(summary, "specialists woken")["note"]
    assert "3 verdicts, 3 passed" in stage(summary, "gate ran")["note"]


def test_a_run_with_a_trigger_and_no_card_drops_out_of_the_funnel():
    failed = [
        entry("trigger.webhook", "d-1", at(400), actor="github", pr=9),
        entry("trigger.failed", "d-1", at(402), actor="github", error="HTTPError: 404"),
    ]
    summary = summarise(a_run("r1") + failed)

    assert tile(summary, "runs_triggered")["value"] == "2"
    assert tile(summary, "cards")["value"] == "1"
    assert [s["count"] for s in summary["funnel"]] == [2, 1, 1, 1, 0]
    # It never reached the fleet, so it is not held against the card rate.
    assert tile(summary, "unattended_to_card")["value"] == "1 of 1"


def test_a_run_whose_trigger_fell_off_the_front_is_a_tail_not_a_fast_run():
    summary = summarise(a_run("r1") + a_run("r2", start=100, trigger=False))

    assert any("no trigger in this window" in u for u in summary["unknown"])
    assert "1 are tails whose trigger is further back" in stage(summary, "triggered")["note"]
    assert stage(summary, "triggered")["count"] == 2


def test_a_parked_run_is_sent_to_a_human_and_never_reaches_a_card():
    summary = summarise(a_run("r1") + a_run("r2", start=100, parked=True))

    assert tile(summary, "parked")["value"] == "1"
    assert tile(summary, "unattended_to_card")["value"] == "1 of 2"
    assert tile(summary, "unattended_to_card")["tone"] == "warn"


def test_zero_parked_is_a_real_zero():
    parked = tile(summarise(a_run("r1")), "parked")

    assert parked["value"] == "0"
    assert "can happen and did not" in parked["method"]


# ---------------------------------------------------------------------------
# The median. The figure a buyer repeats, so it refuses more than it answers.
# ---------------------------------------------------------------------------


def test_two_runs_are_not_enough_for_a_median():
    summary = summarise(a_run("r1", to_card=30) + a_run("r2", start=100, to_card=90))

    median = tile(summary, "median_to_card")
    assert median["value"] == "not enough runs"
    assert median["tone"] == "unknown"
    assert "2 runs in this window carry both a trigger and a card" in median["method"]


def test_three_runs_give_a_median_and_sixty_seconds_reads_as_one_minute():
    summary = summarise(
        a_run("r1", to_card=30)
        + a_run("r2", start=200, to_card=60)
        + a_run("r3", start=400, to_card=90)
    )

    assert tile(summary, "median_to_card")["value"] == "1m 0s"


def test_the_median_is_the_middle_and_not_the_mean():
    summary = summarise(
        a_run("r1", to_card=10)
        + a_run("r2", start=1000, to_card=20)
        + a_run("r3", start=2000, to_card=30)
        + a_run("r4", start=3000, to_card=3660)
    )

    # The mean of these four is 930 seconds. The median of 20 and 30 is 25.
    assert tile(summary, "median_to_card")["value"] == "25s"


def test_an_even_count_landing_on_a_half_second_rounds_up():
    summary = summarise(
        a_run("r1", to_card=30)
        + a_run("r2", start=200, to_card=41)
        + a_run("r3", start=400, to_card=42)
        + a_run("r4", start=600, to_card=300)
    )

    # (41 + 42) / 2 is 41.5, and 41.5 seconds is reported as 42s rather than
    # moving with the rounding mode.
    assert tile(summary, "median_to_card")["value"] == "42s"


def test_a_long_run_reads_in_minutes_and_seconds():
    summary = summarise(
        a_run("r1", to_card=250)
        + a_run("r2", start=1000, to_card=250)
        + a_run("r3", start=2000, to_card=250)
    )

    assert tile(summary, "median_to_card")["value"] == "4m 10s"


def test_the_clock_starts_at_the_delivery_not_at_the_chore_it_started():
    summary = summarise(
        a_run("r1", webhook=True, to_card=90)
        + a_run("r2", start=1000, webhook=True, to_card=90)
        + a_run("r3", start=2000, webhook=True, to_card=90)
    )

    # The chore's own trigger is one second after the delivery. Measuring from
    # it would report 1m 29s and quietly drop the second nobody was watching.
    assert tile(summary, "median_to_card")["value"] == "1m 30s"


def test_a_card_stamped_before_its_trigger_is_not_a_duration():
    summary = summarise(
        a_run("r1", to_card=30)
        + a_run("r2", start=1000, to_card=30)
        + a_run("r3", start=2000, to_card=30)
        + a_run("r4", start=3000, to_card=-500)
    )

    assert tile(summary, "median_to_card")["value"] == "30s"
    assert any("before their own trigger" in u for u in summary["unknown"])


# ---------------------------------------------------------------------------
# Writes, refusals, wakes, deferrals.
# ---------------------------------------------------------------------------


def test_zero_writes_against_cards_is_the_gate_holding():
    writes = tile(summarise(a_run("r1") + a_run("r2", start=100)), "writes")

    assert writes["value"] == "0"
    assert writes["tone"] == "good"
    assert "0 against 2 approval cards" in writes["method"]
    assert "rather than an outage" in writes["method"]
    assert stage(summarise(a_run("r1")), "published")["count"] == 0


def test_a_write_that_executed_is_not_a_write_that_published():
    entries = a_run("r1")
    entries.append(
        entry(
            "write.executed",
            "r1",
            at(60),
            actor="writer",
            approved=True,
            published=False,
            reason="no publisher configured",
        )
    )
    summary = summarise(entries)

    assert tile(summary, "writes")["value"] == "1"
    assert "1 write passed the role check" in tile(summary, "writes")["method"]
    assert "0 landed bytes" in tile(summary, "writes")["method"]
    # The funnel's last stage is bytes in the repository, not checks passed.
    assert stage(summary, "published")["count"] == 0
    assert "1 writes executed" in stage(summary, "published")["note"]


def test_a_probe_that_never_ran_is_counted_neither_way():
    entries = a_run("r1") + a_run("r2", start=100)
    entries.append(
        entry(
            "guard.exercised",
            "r2",
            at(150),
            actor="documentation-companion",
            attempted=True,
            error="the probe raised before it reached the tool",
        )
    )
    summary = summarise(entries)

    refusals = tile(summary, "refusals")
    assert refusals["value"] == "2 of 2"
    assert refusals["tone"] == "good"
    assert "1 probe could not run and is counted neither way" in refusals["method"]
    assert any("could not run" in u for u in summary["unknown"])


def test_a_tool_that_actually_ran_is_not_reported_as_a_refusal():
    entries = a_run("r1")
    entries.append(
        entry(
            "guard.exercised",
            "r1",
            at(50),
            actor="documentation-companion",
            attempted=True,
            denied=False,
            tool_executed=True,
        )
    )
    refusals = tile(summarise(entries), "refusals")

    assert refusals["value"] == "1 of 2"
    assert refusals["tone"] == "bad"
    assert "The tool itself ran 1 times" in refusals["method"]


def test_no_probe_at_all_is_a_zero_and_not_a_refusal():
    entries = [e for e in a_run("r1") if e["kind"] != "guard.exercised"]
    refusals = tile(summarise(entries), "refusals")

    assert refusals["value"] == "0 probes"
    assert refusals["tone"] == "plain"


def test_a_wake_is_unattended_and_a_recall_escalation_is_not():
    entries = a_run("r1")
    # What `run_chore` appends when recall finds an expired deferral: inside a
    # run, under that run's id, with no `woken_by`.
    entries.append(
        entry("finding.escalated", "r1", at(8), actor="compliance-companion",
              reason="the deferral expired and the same subject changed again")
    )
    entries += woken_over([a_deferral(1), a_deferral(2), a_deferral(3)])

    wakes = tile(summarise(entries), "unattended_wakes")
    assert wakes["value"] == "3"
    assert "4 escalations in this window, 3 unattended" in wakes["method"]
    assert "1 escalation here was raised during recall" in wakes["method"]


def test_an_escalation_the_two_signals_disagree_about_is_unknown():
    entries = a_run("r1")
    entries.append(
        entry("finding.escalated", "r1", at(9), actor="compliance-companion",
              woken_by="firestore-query-subscription")
    )
    summary = summarise(entries)

    assert tile(summary, "unattended_wakes")["value"] == "0"
    # The escalation is in the total and in neither half. Reporting the total as
    # the two halves added up printed "0 escalations in this window" on a window
    # holding one, which is the tile refuting its own sentence.
    assert "1 escalation in this window, 0 unattended" in tile(
        summary, "unattended_wakes"
    )["method"]
    assert "in neither half of it" in tile(summary, "unattended_wakes")["method"]
    assert any("not decidable from this window" in u for u in summary["unknown"])


def test_deferrals_pair_on_the_parent_and_not_on_the_shared_subject():
    deferrals = [a_deferral(1), a_deferral(2), a_deferral(3)]
    entries = [d.to_doc() for d in deferrals] + woken_over(deferrals[:2])

    summary = summarise(entries)
    open_ones = tile(summary, "deferrals_unescalated")

    # All three carry the same subject, so pairing by subject would answer
    # either 0 of 3 or 3 of 3. Only the parent entry says which two were woken.
    assert open_ones["value"] == "1 of 3"
    assert summary["unknown"] == []


def test_an_escalation_naming_a_deferral_outside_the_window_says_so():
    deferrals = [a_deferral(1), a_deferral(2)]
    # Two wakes, and only the first deferral is in the window. So one wake pairs
    # and the deferral it names is not counted as unescalated, which is the
    # "0 of 1", and the other names something this window cannot see.
    entries = [deferrals[0].to_doc()] + woken_over(deferrals)

    summary = summarise(entries)

    assert tile(summary, "deferrals_unescalated")["value"] == "0 of 1"
    assert any("cannot be paired here" in u for u in summary["unknown"])


def test_a_disputed_escalation_pairs_with_nothing_and_the_tile_says_where():
    deferral = a_deferral(1)
    wake = woken_over([deferral])[0]
    # The payload says the subscription woke this, the run id says it happened
    # inside a chore. Sorted into neither, so the deferral it names is left
    # counted as unescalated rather than quietly paired off by one signal.
    wake["run_id"] = "r1"

    summary = summarise(a_run("r1") + [deferral.to_doc(), wake])

    assert tile(summary, "deferrals_unescalated")["value"] == "1 of 1"
    assert tile(summary, "unattended_wakes")["value"] == "0"
    assert any("not decidable from this window" in u for u in summary["unknown"])


def test_no_deferral_in_the_window_is_not_reported_as_zero_open():
    open_ones = tile(summarise(a_run("r1")), "deferrals_unescalated")

    assert open_ones["value"] == "none in this window"
    assert "not visible from here" in open_ones["method"]


# ---------------------------------------------------------------------------
# The two windows that know nothing, which must not print zeros.
# ---------------------------------------------------------------------------


def test_an_empty_thread_renders_and_claims_nothing():
    summary = summarise([])

    assert summary["window"] == {"entries": 0, "runs": 0, "first": None, "last": None}
    assert summary["activity"] == []
    assert [s["count"] for s in summary["funnel"]] == [None] * 5
    assert all("cannot be counted from here" in s["note"] for s in summary["funnel"])
    assert tile(summary, "median_to_card")["value"] == "not enough runs"
    assert tile(summary, "unattended_to_card")["value"] == "none dispatched"
    assert tile(summary, "runs_triggered")["value"] == "0"


def test_a_window_of_only_control_plane_entries_knows_nothing_about_runs():
    deferrals = [a_deferral(1), a_deferral(2)]
    summary = summarise([d.to_doc() for d in deferrals] + woken_over(deferrals))

    assert summary["window"]["runs"] == 0
    assert summary["activity"] == []
    # Nothing here can support "0 pull requests were triggered", so no stage
    # may print a 0.
    assert [s["count"] for s in summary["funnel"]] == [None] * 5
    # These two are counts of entries in the window, and those are real.
    assert tile(summary, "unattended_wakes")["value"] == "2"
    assert tile(summary, "deferrals_unescalated")["value"] == "0 of 2"


def test_summarise_does_not_mutate_the_entries_it_is_given():
    entries = a_run("r1")
    before = [dict(e) for e in entries]

    summarise(entries)

    assert entries == before


# ---------------------------------------------------------------------------
# The sparkline.
# ---------------------------------------------------------------------------


def test_activity_fills_the_empty_days_and_adds_up_to_the_runs():
    entries = (
        a_run("r1", day=0)
        + a_run("r2", start=500, day=0)
        + a_run("r3", day=2)
        + woken_over([a_deferral(1)])
    )
    summary = summarise(entries, now=at(0, days=2))

    assert summary["activity"] == [
        {"day": "2026-08-22", "runs": 2},
        {"day": "2026-08-23", "runs": 0},
        {"day": "2026-08-24", "runs": 1},
    ]
    # The wake entries carry a run id and are not runs, so they add no bar.
    assert sum(d["runs"] for d in summary["activity"]) == summary["window"]["runs"]


def test_a_quiet_stretch_since_the_last_run_is_visible():
    summary = summarise(a_run("r1"), now=at(0, days=3))

    assert [d["runs"] for d in summary["activity"]] == [1, 0, 0, 0]
    assert summary["activity"][-1]["day"] == "2026-08-25"


def test_the_strip_ends_at_the_last_run_when_no_now_is_given():
    summary = summarise(a_run("r1") + a_run("r2", day=1))

    assert [d["day"] for d in summary["activity"]] == ["2026-08-22", "2026-08-23"]


def test_the_writes_caption_follows_the_number_above_it():
    """"The gate holding" is true of a zero and false of anything else.

    A static caption printed it over a write that had actually happened, on the
    most load-bearing tile on the page. A caption that is only true for one
    value is a caption that lies for every other one.
    """
    base = [
        {
            "entry_id": "t", "kind": "trigger.pull_request", "actor": "webhook",
            "run_id": "r1", "recorded_at": "2026-08-23T10:00:00+00:00",
            "payload": {}, "parent_id": None,
        },
        {
            "entry_id": "c", "kind": "plan.proposed", "actor": "architect-leader",
            "run_id": "r1", "recorded_at": "2026-08-23T10:01:00+00:00",
            "payload": {}, "parent_id": "t",
        },
    ]
    written = base + [
        {
            "entry_id": "w", "kind": "write.executed", "actor": "writer",
            "run_id": "r1", "recorded_at": "2026-08-23T10:02:00+00:00",
            "payload": {"published": True}, "parent_id": "c",
        }
    ]

    none = tile(summarise(base), "writes")
    some = tile(summarise(written), "writes")

    assert "gate holding" in none["caption"]
    assert "gate holding" not in some["caption"], (
        "a write happened and the page still called it the gate holding"
    )
    assert "approved by a person" in some["caption"]


def test_no_caption_describes_something_that_did_not_happen():
    """On an empty window every static caption was a false statement.

    "a specialist refused", "an agent asked for the write tool", "the approval
    gate holding": each written for a number greater than zero and each printed
    over a zero, because the static table was consulted before the branch that
    exists to handle exactly this. The tile said one thing and its own method
    line, on the same card, said the opposite.
    """
    summary = summarise([])

    forbidden = (
        "a specialist refused",
        "an agent asked for the write tool",
        "no human touched these",
        "a deferral expired and",
        "gate holding",
    )
    for item in summary["headline"]:
        for phrase in forbidden:
            assert phrase not in item["caption"], (
                f"{item['key']} shows {item['value']!r} and claims {phrase!r}"
            )
        assert item["caption"], item["key"]


def test_zero_writes_only_means_the_gate_held_when_there_was_something_to_hold():
    """Three states, three sentences, and the middle one is the product working.

    Zero writes against three cards is the approval gate holding. Zero writes
    against no cards is nothing having been proposed, and calling that the gate
    holding credits the system for an outcome it never faced.
    """

    def thread(cards: int, writes: int) -> list:
        out = [
            {
                "entry_id": "t", "kind": "trigger.pull_request", "actor": "webhook",
                "run_id": "r1", "recorded_at": "2026-08-23T10:00:00+00:00",
                "payload": {}, "parent_id": None,
            }
        ]
        for i in range(cards):
            out.append({
                "entry_id": f"c{i}", "kind": "plan.proposed", "actor": "architect-leader",
                "run_id": "r1", "recorded_at": "2026-08-23T10:01:00+00:00",
                "payload": {}, "parent_id": "t",
            })
        for i in range(writes):
            out.append({
                "entry_id": f"w{i}", "kind": "write.executed", "actor": "writer",
                "run_id": "r1", "recorded_at": "2026-08-23T10:02:00+00:00",
                "payload": {"published": True}, "parent_id": "t",
            })
        return out

    nothing_proposed = tile(summarise(thread(0, 0)), "writes")["caption"]
    gate_held = tile(summarise(thread(3, 0)), "writes")["caption"]
    approved = tile(summarise(thread(3, 1)), "writes")["caption"]

    assert "gate holding" not in nothing_proposed
    assert "gate holding" in gate_held
    assert "approved by a person" in approved
