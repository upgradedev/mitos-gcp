"""The page renders untrusted text, so it gets tested like it does.

A pull request title is written by whoever opened the pull request. On the
webhook path that is somebody outside the organisation, the title arrives
wrapped in `Tainted` because the fleet already knows not to trust it, and it is
then recorded in the ledger and rendered on this page. The type said untrusted
and the page rendered it raw anyway, which is the gap these tests close.

The interesting one is `json.dumps`. It escapes what JSON requires and nothing
HTML requires, so a title containing a closing script tag passed through it
unchanged and terminated the block it was embedded in. No click needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from service.thread_view import KIND_STYLE, render

# Every entry kind the fleet appends. Built as a plain string rather than typed
# into a shell, because a word-boundary escape once reached this repository as a
# literal control byte and the rule it belonged to could never match.
# Any dotted kind in a `kind=` position, rather than a list of prefixes.
#
# The list named trigger, fleet, specialist, guard, evaluator, item, plan,
# write, finding and injection, so a kind under a NEW prefix was invisible
# to the test named after finding exactly that. Two were added later,
# run.nothing_to_govern and gate.delegated; neither was ever checked and
# neither had a colour in either map.
KIND_PATTERN = (
    r'(?:kind\s*[=:]\s*|record\(\s*)"([a-z_]+\.[a-z_]+)"'
)

# Assembled rather than written out, so a secret scanner reading this file does
# not see a live payload, and so each half is legible on its own.
CLOSE = "</" + "script>"
BREAKOUT = CLOSE + "<img src=x onerror=alert(1)>"


def _page(payload: dict, kind: str = "trigger.webhook") -> str:
    return render(
        [
            {
                "entry_id": "a1",
                "kind": kind,
                "actor": "github",
                "recorded_at": "2026-08-23T10:00:00+00:00",
                "parent_id": None,
                "payload": payload,
            }
        ],
        role="reader",
        wakeups=0,
    )


def _data_literal(page: str) -> str:
    """The exact bytes of the JSON the page hands to the browser.

    Scoped deliberately. Asserting over the whole page catches the template's
    own markup and says nothing about what came from the payload.
    """
    match = re.search(r"const DATA = (.*?), STYLE = ", page, re.S)
    assert match, "the page no longer embeds its data where this test looks"
    return match.group(1)


def _embedded_data(page: str) -> list:
    """The same JSON, parsed back."""
    return json.loads(_data_literal(page))


def test_a_hostile_title_cannot_close_the_script_block() -> None:
    page = _page({"pr": 1, "title": BREAKOUT})

    assert CLOSE + "<img" not in page, (
        "a pull request title closed the script element, so everything after it "
        "is markup the browser will act on"
    )
    assert "<img" not in page


def test_no_angle_bracket_from_a_payload_reaches_the_page_raw() -> None:
    """Broader than the breakout: nothing from a payload should be a tag.

    Asserting only on the one exploit string would pass a fix that special-cased
    that string, which is not a fix.
    """
    data = _data_literal(_page({"pr": 1, "title": "<b>bold</b>", "branch": "<svg onload=x>"}))

    assert "<" not in data
    assert ">" not in data


def test_escaping_does_not_change_the_value() -> None:
    """A page that renders safely but wrongly is still wrong."""
    title = BREAKOUT + " and a quote \" and a backslash \\ and  "
    entries = _embedded_data(_page({"pr": 1, "title": title}))

    assert entries[0]["payload"]["title"] == title


def test_a_title_cannot_steer_the_template() -> None:
    """The placeholders are substituted, and the data goes in last.

    With the data substituted first, a title containing a placeholder name was
    rewritten by the substitution that followed it: untrusted input editing the
    page around it.
    """
    entries = _embedded_data(_page({"pr": 1, "title": "__ROLE__ __STYLE__ __WAKEUPS__"}))

    assert entries[0]["payload"]["title"] == "__ROLE__ __STYLE__ __WAKEUPS__"


def test_every_kind_the_fleet_records_has_a_colour() -> None:
    """Colour carries meaning here, so a kind without one loses its meaning.

    The three webhook kinds were missing, which meant a real delivery rendered
    in the fallback grey and `trigger.failed`, a failure, looked exactly like an
    ordinary step.
    """
    # Anchored to this file, not to the working directory. A relative path here
    # raises FileNotFoundError from wherever pytest happens to be invoked, which
    # reads as a broken test rather than as the answer to the question.
    root = Path(__file__).resolve().parents[2]
    recorded = set()
    for name in ("src/mitos/chore.py", "src/mitos/watcher.py", "service/main.py"):
        text = (root / name).read_text(encoding="utf-8")
        recorded.update(re.findall(KIND_PATTERN, text))

    assert len(recorded) > 10, (
        f"only {len(recorded)} kinds discovered, so this test is not looking "
        "where the fleet actually records them"
    )
    missing = sorted(recorded - set(KIND_STYLE))
    assert not missing, f"kinds the fleet records but the page cannot colour: {missing}"


def test_an_unknown_kind_still_renders() -> None:
    """Forward compatibility: a new kind should degrade, not break the page."""
    page = _page({"pr": 1, "title": "ordinary"}, kind="something.new")

    assert "something.new" in page
