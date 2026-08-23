"""The one strip a buyer reads: what the fleet did with a pull request, and
where it stopped.

`summarise` is a pure function from the entries `/thread` already returns to a
dict a page renders without doing arithmetic of its own. Nothing here fetches
and nothing here estimates. Every figure is a count of entries in the window or
a difference between two timestamps in it, and each one is labelled as literally
what was counted. A number on this page that is not exactly what its label says
would be the product refuting itself.

Three figures are not the obvious `Counter` lookup, and those three are why this
is a module rather than a dict comprehension in the service:

  triggers      a GitHub delivery records `trigger.webhook`, and the chore it
                starts then records `trigger.pull_request` under the same
                delivery id. Adding the two kinds counts those runs twice, so
                runs carrying a trigger are counted instead. The entry count is
                not hidden, it is in the caption.

  escalations   `finding.escalated` is written from two places. `escalate_on_wake`
                writes it unattended and stamps `woken_by`; `run_chore` writes it
                during recall, inside a run that a pull request triggered. Only
                the first is an unattended wake, and the tile says so.

  writes        `chore.execute_write` separates the write passing all three
                checks from bytes landing in the specification repository, and
                the receipt carries `published` for the second. The tile counts
                what executed and the caption says how many published, because
                saying "published" for both is the overclaim `chore.py` already
                refuses to make.

The three empty states are kept apart here as they are in `dashboard`, and this
is where they are decided rather than where they are printed:

  zero     it can happen in this window and has not, and the count is 0
  tail     the window is a slice of the thread and the answer is further back
  unknown  nothing in the window can settle it, and the count is None

A funnel stage counts runs, so when the window holds no run at all every stage
is None rather than 0. A window of nothing but control-plane entries cannot
support the claim that no pull request was triggered.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Imported rather than restated. `dashboard` already decides which run ids are
# not runs, which kinds count as a trigger, and how a guard probe that never ran
# is kept apart from one that was refused. A second copy of those rules is a
# copy that drifts, and the drift would be silent: both modules would keep
# passing their own tests while disagreeing about the same window.
from .dashboard import (
    CONTROL_PLANE_RUNS,
    TRIGGER_KINDS,
    _group_by_run,
    _guard_attempts,
    _guard_unknown,
    _parse,
    _payload,
)

# A median over two runs is one run's timing wearing a statistic's name. The
# figure a buyer leans on hardest is the one that has to refuse to appear.
MEDIAN_MIN_RUNS = 3

FUNNEL_STAGES = (
    "triggered",
    "specialists woken",
    "gate ran",
    "card produced",
    "published",
)


def _kind(entry: dict[str, Any]) -> str:
    return str(entry.get("kind") or "")


def _has(group: list[dict[str, Any]], kind: str) -> bool:
    return any(_kind(e) == kind for e in group)


def _times(group: list[dict[str, Any]], kinds: Optional[tuple[str, ...]] = None) -> list[datetime]:
    """Every readable timestamp in the group, optionally of certain kinds only."""
    out = []
    for entry in group:
        if kinds is not None and _kind(entry) not in kinds:
            continue
        parsed = _parse(entry.get("recorded_at"))
        if parsed is not None:
            out.append(parsed)
    return out


def _duration(seconds: float) -> str:
    """`38s`, `4m 10s`, `1h 6m`. Never `250.0` and never `4.17 minutes`."""
    # `round` is banker's rounding, so round(0.5) is 0 and round(1.5) is 2. A
    # median landing on a half second would then move depending on which side of
    # it the two middle samples happened to sit.
    total = math.floor(seconds + 0.5)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {(total % 3600) // 60}m"


# The first four are the ones somebody reads. The rest matter and are shown
# smaller, because nine equally weighted tiles is a list, and a list is what the
# page before this one was rejected for being.
PRIMARY = ("median_to_card", "runs_triggered", "unattended_to_card", "writes")

# Written, not derived. Truncating an accurate sentence produced captions that
# broke mid-clause and read worse than either the long or the short version.
# These say what the figure means to somebody who arrived from a link; the full
# arithmetic is kept intact in `method` and rendered under the page.
SHORT = {
    "runs_triggered": "pull requests the fleet was woken for",
    "cards": "one per run, addressed by the hash of the bytes it proposes",
    "median_to_card": "from the pull request landing to an approval card",
    "unattended_to_card": "no human touched these before the card existed",
    "parked": "a specialist refused and handed the item to a person",
    # `writes` is deliberately absent. Its short caption depends on the value:
    # "the gate holding" is true of a zero and false of anything else, and a
    # static line here printed it over a write that had actually happened.
    "refusals": "an agent asked for the write tool and was refused at the tool call",
    "unattended_wakes": "a deferral expired and the subscription woke the fleet",
    "deferrals_unescalated": "deferrals still waiting, with nothing recording their expiry",
}


def _short_for(key: str, value: str) -> str:
    """Captions whose truth depends on the number above them.

    Zero writes against approval cards is the gate holding. One write is a
    person having approved something. The same sentence cannot serve both, and
    printing the first over a write that happened is a false statement on the
    most load-bearing tile on the page.
    """
    text = value.strip()
    # A value that is not a number at all is already a refusal to answer, and
    # the tile's own method sentence says why. A cheerful static line over it
    # would be the page talking past its own data.
    nothing = text.startswith("0") or not text[:1].isdigit()
    if key == "writes":
        # Handled by `_writes_tile`, which knows the card count. Reaching
        # here at all means a caller skipped it, so say the safe thing.
        return "" if nothing else "approved by a person, bound to the exact bytes"
    if not nothing:
        return ""
    # Every one of these read as a description of something that happened, over
    # a number saying it did not.
    return {
        "parked": "nothing was refused in this window",
        "refusals": "no write was attempted here, so nothing was refused",
        "unattended_to_card": "no run here reached the point of having a card",
        "unattended_wakes": "no deferral expired in this window",
        "runs_triggered": "no pull request reached the fleet in this window",
        "cards": "no run here produced one",
        "deferrals_unescalated": "no deferral is recorded in this window",
    }.get(key, "")


def _tile(
    key: str,
    label: str,
    value: str,
    unit: str,
    caption: str,
    tone: str,
    short: str = "",
) -> dict[str, Any]:
    """One headline figure.

    `caption` and `method` are split on purpose. A tile carrying its own
    methodological footnote is a paragraph with a big number on top, and nine of
    those is the wall of text this page exists to replace. So the tile says what
    the number means in one line, and the arithmetic that produced it goes
    underneath the page where it can be checked without being in the way.

    Nothing is dropped. The full sentence is still rendered, still on the same
    page, and still says exactly how the figure was counted.
    """
    # A builder that knows something the key and the value do not can say
    # so. Zero writes is the gate holding when there were cards to approve
    # and nothing at all when there were none, and only the builder has the
    # card count to tell those apart.
    short = short or _short_for(key, value) or SHORT.get(key) or caption
    # The long form is always kept, even when it repeats the short one, because
    # the page renders it as the record of how the figure was counted.
    method = caption
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "caption": short,
        "method": method,
        "tone": tone,
        "primary": key in PRIMARY,
    }


def _stage(stage: str, count: Optional[int], note: str) -> dict[str, Any]:
    return {"stage": stage, "count": count, "note": note}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _time_to_card(
    run_ids: list[str], groups: dict[str, list[dict[str, Any]]]
) -> tuple[list[float], int, int]:
    """Seconds from a run's trigger to its approval card, one figure per run.

    Also returns the runs that produced a card with no trigger in the window,
    which are tails rather than instant runs, and the runs whose card is stamped
    before their own trigger, which is not a duration at all.
    """
    durations: list[float] = []
    tails = 0
    skewed = 0
    for run_id in run_ids:
        group = groups[run_id]
        cards = _times(group, ("plan.proposed",))
        if not cards:
            continue
        triggers = _times(group, TRIGGER_KINDS)
        if not triggers:
            tails += 1
            continue
        # Earliest of each. On the webhook path the delivery is recorded before
        # the pull request it opened, and the delivery is where the clock starts:
        # that is the moment at which nobody was watching.
        seconds = (min(cards) - min(triggers)).total_seconds()
        if seconds < 0:
            skewed += 1
            continue
        durations.append(seconds)
    return durations, tails, skewed


def _activity(
    run_ids: list[str], groups: dict[str, list[dict[str, Any]]], now: Optional[str]
) -> list[dict[str, Any]]:
    """Runs per UTC day, with the empty days filled in.

    A run is counted on the day it started, not on every day it touched. Days
    with no run are emitted as zero rather than skipped, because a sparkline
    that closes its own gaps draws a busy week over a quiet fortnight.
    """
    per_day: dict[Any, int] = {}
    for run_id in run_ids:
        times = _times(groups[run_id])
        if not times:
            continue
        day = min(times).astimezone(timezone.utc).date()
        per_day[day] = per_day.get(day, 0) + 1
    if not per_day:
        return []

    first, last = min(per_day), max(per_day)
    stamp = _parse(now)
    if stamp is not None:
        # Trailing empty days are the honest shape of a fleet that has been
        # quiet since Tuesday. A strip that ends at the last run hides that.
        last = max(last, stamp.astimezone(timezone.utc).date())

    out = []
    day = first
    while day <= last:
        out.append({"day": day.isoformat(), "runs": per_day.get(day, 0)})
        day += timedelta(days=1)
    return out


def _wake_split(
    entries: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Escalations nobody asked for, escalations raised inside a run, and how
    many of them the two signals disagree about.

    The definition is the payload: `escalate_on_wake` stamps `woken_by` with the
    mechanism that fired, and that travels with the entry wherever it is copied.
    `run_id == "watch"` is the same fact by convention of one deployment's
    wiring, so it is used to contradict and never to decide. An entry the two
    disagree about is reported as unknown rather than sorted by whichever
    signal happened to be checked first.
    """
    unattended, in_run, disputed = [], [], 0
    for entry in entries:
        if _kind(entry) != "finding.escalated":
            continue
        stamped = "woken_by" in _payload(entry)
        off_run = str(entry.get("run_id") or "") in CONTROL_PLANE_RUNS
        if stamped != off_run:
            disputed += 1
            continue
        (unattended if stamped else in_run).append(entry)
    return unattended, in_run, disputed


def _deferral_pairing(
    entries: list[dict[str, Any]], unattended: list[dict[str, Any]]
) -> tuple[int, int, int]:
    """Deferrals in the window, how many have no escalation naming them, and how
    many unattended escalations name something this window cannot see.

    Paired on `parent_id`, which `escalate_on_wake` sets to the deferral it woke
    for, and never on subject: every deferral in the live thread carries the
    same subject, so pairing by subject would answer twenty-two against
    twenty-two and mean nothing at all.

    `unattended` is passed in rather than recomputed so that this and the wake
    tile cannot end up disagreeing about which escalations were unattended.
    """
    by_id = {str(e.get("entry_id") or ""): e for e in entries}
    deferrals = [e for e in entries if _kind(e) == "finding.deferred"]

    paired: set[str] = set()
    dangling = 0
    for escalation in unattended:
        parent = str(escalation.get("parent_id") or "")
        target = by_id.get(parent)
        if target is not None and _kind(target) == "finding.deferred":
            paired.add(parent)
        else:
            dangling += 1

    unescalated = sum(
        1 for d in deferrals if str(d.get("entry_id") or "") not in paired
    )
    return len(deferrals), unescalated, dangling


def summarise(
    entries: list[dict[str, Any]], now: Optional[str] = None
) -> dict[str, Any]:
    """Everything the summary page prints, counted from one window of entries.

    `entries` is the `entries` list from GET /thread, in any order. `now` is an
    ISO timestamp used only to extend the activity strip to today, so that a
    quiet run of days shows as empty bars rather than as the strip stopping.
    """
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    order, groups = _group_by_run(entries)
    run_ids = [r for r in order if r not in CONTROL_PLANE_RUNS]
    counts = Counter(_kind(e) for e in entries)
    unknown: list[str] = []

    stamped: list[tuple[datetime, str]] = []
    for entry in entries:
        parsed = _parse(entry.get("recorded_at"))
        if parsed is not None:
            stamped.append((parsed, str(entry.get("recorded_at"))))
    if len(stamped) != len(entries):
        missing = len(entries) - len(stamped)
        unknown.append(
            f"the timestamp on {missing} of these entries could not be read, so "
            f"they sit outside the window bounds and the activity strip"
        )

    # Runs, not trigger entries. The webhook path records two triggers for one
    # run, and the module docstring says why that is not a bug to fix upstream.
    triggered = [r for r in run_ids if _times(groups[r], TRIGGER_KINDS)]
    dispatched = [r for r in run_ids if _has(groups[r], "fleet.dispatch")]
    gated = [r for r in run_ids if _has(groups[r], "evaluator.verdict")]
    carded = [r for r in run_ids if _has(groups[r], "plan.proposed")]
    unattended_to_card = [
        r
        for r in dispatched
        if _has(groups[r], "plan.proposed") and not _has(groups[r], "item.parked")
    ]

    trigger_entries = sum(counts[k] for k in TRIGGER_KINDS)
    doubled = trigger_entries - len(triggered)

    durations, tails, skewed = _time_to_card(run_ids, groups)
    if tails:
        unknown.append(
            f"{tails} of the runs here produced a card with no trigger in this "
            f"window, so how long they took cannot be measured from here, and "
            f"the activity strip places them on the day of their earliest "
            f"surviving entry rather than the day they started"
        )
    if skewed:
        unknown.append(
            f"{skewed} runs record their card before their own trigger, so those "
            f"durations were left out rather than counted as negative time"
        )

    settled, unsettled = _guard_attempts(entries)
    denied = sum(1 for e in settled if _payload(e).get("denied"))
    tool_ran = sum(1 for e in settled if _payload(e).get("tool_executed"))
    if unsettled:
        unknown.append(_guard_unknown(len(unsettled)))

    wakes, in_run_escalations, disputed = _wake_split(entries)
    if disputed:
        unknown.append(
            f"{disputed} escalations are stamped as unattended but sit under a "
            f"run id, or the reverse, so whether they were unattended is not "
            f"decidable from this window"
        )

    deferred, unescalated, dangling = _deferral_pairing(entries, wakes)
    if dangling:
        unknown.append(
            f"{dangling} unattended escalations name a deferral that is not in "
            f"this window, so the deferrals they answer cannot be paired here"
        )

    writes = [e for e in entries if _kind(e) == "write.executed"]
    published = sum(1 for e in writes if _payload(e).get("published"))

    headline = [
        _triggers_tile(len(triggered), trigger_entries, doubled),
        _tile(
            "cards",
            "approval cards produced",
            str(counts["plan.proposed"]),
            "cards",
            "one per run that reached the approval, each addressed by the sha256 "
            "of the exact bytes it proposes",
            "plain",
        ),
        _median_tile(durations, len(carded)),
        _unattended_tile(len(unattended_to_card), len(dispatched)),
        _parked_tile(counts["item.parked"]),
        _writes_tile(len(writes), published, counts["plan.proposed"]),
        _refusals_tile(len(settled), denied, tool_ran, len(unsettled)),
        _wakes_tile(len(wakes), len(in_run_escalations)),
        _deferrals_tile(deferred, unescalated),
    ]

    return {
        "window": {
            "entries": len(entries),
            "runs": len(run_ids),
            "first": min(stamped)[1] if stamped else None,
            "last": max(stamped)[1] if stamped else None,
        },
        "headline": headline,
        "funnel": _funnel(
            run_ids,
            groups,
            triggered,
            dispatched,
            gated,
            carded,
            counts,
            len(writes),
            published,
            unknown,
        ),
        "activity": _activity(run_ids, groups, now),
        "unknown": unknown,
    }


def _triggers_tile(runs: int, trigger_entries: int, doubled: int) -> dict[str, Any]:
    caption = f"{trigger_entries} trigger entries over {runs} runs"
    if doubled:
        caption += (
            f"; {doubled} of those runs arrived as a GitHub delivery, which "
            f"records the delivery and then the pull request it opened, so they "
            f"are counted once"
        )
    caption += ". These are runs, and the same pull request can be handled twice."
    return _tile(
        "runs_triggered", "pull requests handled", str(runs), "runs", caption, "plain"
    )


def _median_tile(durations: list[float], carded: int) -> dict[str, Any]:
    if len(durations) < MEDIAN_MIN_RUNS:
        return _tile(
            "median_to_card",
            "median time to a card",
            "not enough runs",
            "",
            f"{len(durations)} runs in this window carry both a trigger and a "
            f"card. A median needs at least {MEDIAN_MIN_RUNS}, and one run's "
            f"timing printed under the word median is the kind of figure this "
            f"product exists to catch.",
            "unknown",
        )
    caption = (
        f"median over the {len(durations)} runs here that carry both endpoints, "
        f"from the trigger entry to the approval card, unattended throughout"
    )
    if carded > len(durations):
        caption += (
            f". {carded - len(durations)} other runs produced a card whose "
            f"trigger is not in this window"
        )
    return _tile(
        "median_to_card",
        "median time to a card",
        _duration(statistics.median(durations)),
        "",
        caption,
        "plain",
    )


def _unattended_tile(unattended: int, dispatched: int) -> dict[str, Any]:
    if not dispatched:
        return _tile(
            "unattended_to_card",
            "reached a card unattended",
            "none dispatched",
            "",
            "0 fleet dispatches in this window, so no run here had the chance to "
            "reach a card.",
            "plain",
        )
    return _tile(
        "unattended_to_card",
        "reached a card unattended",
        f"{unattended} of {dispatched}",
        "runs",
        "runs the fleet was dispatched on that produced an approval card with "
        "nothing parked for a human on the way. A parked item never reaches a "
        "card, which is why the two are counted together.",
        "good" if unattended == dispatched else "warn",
    )


def _parked_tile(parked: int) -> dict[str, Any]:
    tail = (
        "0 in this window, which can happen and did not."
        if not parked
        else "each one names the companion that refused and the reason it gave."
    )
    return _tile(
        "parked",
        "sent to a human",
        str(parked),
        "items",
        f"a specialist may refuse, and a refused item stops there rather than "
        f"being answered anyway. {tail}",
        "plain",
    )


def _writes_tile(executed: int, published: int, cards: int) -> dict[str, Any]:
    short = ""
    if executed == 0 and cards:
        caption = (
            f"0 against {cards} approval cards. That is the approval gate "
            f"holding rather than an outage: every run in this window stopped at "
            f"the card, and nothing was approved through the API."
        )
        short = "the approval gate holding, not an outage"
        tone = "good"
    elif executed == 0:
        caption = (
            "0 in this window, and no approval card was produced here for "
            "anyone to approve."
        )
        # Not "the gate holding". Nothing was proposed, so nothing held.
        short = "nothing was proposed here for anyone to approve"
        tone = "plain"
    else:
        caption = (
            f"{_plural(executed, 'write')} passed the role check, the plan hash "
            f"check and the credential, of which {published} landed bytes in the "
            f"specification repository. The two are not the same claim."
        )
        short = "approved by a person, bound to the exact bytes on the card"
        tone = "plain"
    return _tile(
        "writes", "writes executed", str(executed), "", caption, tone, short=short
    )


def _refusals_tile(
    settled: int, denied: int, tool_ran: int, unsettled: int
) -> dict[str, Any]:
    if not settled and unsettled:
        return _tile(
            "refusals",
            "refusals enforced",
            "not known here",
            "",
            _guard_unknown(unsettled)
            + ", so neither a refusal nor a reachable tool can be claimed from "
            "this window",
            "unknown",
        )
    if not settled:
        return _tile(
            "refusals",
            "refusals enforced",
            "0 probes",
            "",
            "0 write attempts in this window. The interceptor had nothing to "
            "refuse.",
            "plain",
        )
    caption = (
        f"the documentation companion was handed the write tool and told to use "
        f"it. The tool itself ran {tool_ran} times."
    )
    if unsettled:
        verb = "is" if unsettled == 1 else "are"
        caption += (
            f" {_plural(unsettled, 'probe')} could not run and {verb} counted "
            f"neither way."
        )
    return _tile(
        "refusals",
        "refusals enforced",
        f"{denied} of {settled}",
        "probes",
        caption,
        "bad" if tool_ran else ("good" if denied == settled else "warn"),
    )


def _wakes_tile(unattended: int, in_run: int) -> dict[str, Any]:
    caption = (
        f"{unattended + in_run} escalations in this window, {unattended} of them "
        f"unattended: nobody called anything, a deferral reached its expiry and "
        f"the query subscription woke the fleet."
    )
    if in_run:
        caption += (
            f" The other {in_run} were raised during recall inside a run that a "
            f"pull request triggered, so they are not wakes."
        )
    return _tile(
        "unattended_wakes", "unattended wakes", str(unattended), "", caption, "plain"
    )


def _deferrals_tile(deferred: int, unescalated: int) -> dict[str, Any]:
    if not deferred:
        return _tile(
            "deferrals_unescalated",
            "deferrals with no escalation naming them",
            "none in this window",
            "",
            "no finding was deferred in this window. The thread is a slice, so a "
            "deferral recorded before it is not visible from here.",
            "plain",
        )
    return _tile(
        "deferrals_unescalated",
        "deferrals with no escalation naming them",
        f"{unescalated} of {deferred}",
        "",
        "paired on the parent entry each escalation names, not on subject: every "
        "deferral here carries the same subject. An escalation records that a "
        "deferral expired, it does not close it, and a deferral recorded before "
        "this window is not visible from here.",
        "plain",
    )


def _funnel(
    run_ids: list[str],
    groups: dict[str, list[dict[str, Any]]],
    triggered: list[str],
    dispatched: list[str],
    gated: list[str],
    carded: list[str],
    counts: Counter,
    executed: int,
    published: int,
    unknown: list[str],
) -> list[dict[str, Any]]:
    """The product in one strip, counted in runs at every stage.

    Every stage is the same unit on purpose. A strip counting runs at one stage
    and entries at the next reads as a drop-off that never happened, and this
    fleet wakes three specialists per run.
    """
    if not run_ids:
        # Not zero. A window holding only `seed` and `watch` entries, or holding
        # nothing at all, cannot support the claim that no pull request was
        # triggered. It can only say that this slice does not know.
        reason = (
            "no run appears in this window, so what any run did cannot be "
            "counted from here"
        )
        return [_stage(stage, None, reason) for stage in FUNNEL_STAGES]

    missing_trigger = len(run_ids) - len(triggered)
    verdicts = [
        e for r in run_ids for e in groups[r] if _kind(e) == "evaluator.verdict"
    ]
    passed = sum(1 for e in verdicts if _payload(e).get("passed"))

    strip = [
        _stage(
            "triggered",
            len(run_ids),
            f"{len(triggered)} carry their trigger in this window"
            + (
                f", and {missing_trigger} are tails whose trigger is further back"
                if missing_trigger
                else ", and every run here is whole"
            ),
        ),
        _stage(
            "specialists woken",
            len(dispatched),
            f"{counts['specialist.response']} specialist responses across them"
            if counts["specialist.response"]
            else "no specialist responded in this window",
        ),
        _stage(
            "gate ran",
            len(gated),
            f"{len(verdicts)} verdicts, {passed} passed. The gate is regular "
            f"expressions, so a run that reaches it always gets an answer."
            if verdicts
            else "the gate recorded no verdict in this window",
        ),
        _stage(
            "card produced",
            len(carded),
            f"{counts['plan.proposed']} cards, each addressed by the sha256 of "
            f"the bytes it proposes",
        ),
        _stage(
            "published",
            published,
            f"{executed} writes executed, {published} of which landed bytes in "
            f"the specification repository"
            if executed
            else "0 in this window; every run stopped at the approval, which is "
            "the gate holding rather than an outage",
        ),
    ]

    # A window cut through the middle of a run can leave a later stage holding
    # more runs than an earlier one. That is the slice and not a run that
    # skipped a step, and it is worth saying so rather than letting an operator
    # read the strip as broken.
    seen = [s["count"] for s in strip]
    if any(later > earlier for earlier, later in zip(seen, seen[1:])):
        unknown.append(
            "a later stage of the funnel holds more runs than an earlier one, "
            "which means this window starts partway through some runs"
        )
    return strip
