"""The same facts, given a hierarchy, so a stranger can read them.

`dashboard.py` renders label-and-value rows. That is the right shape for an
operator who already knows what a `guard.exercised` entry is, and the wrong
shape for everybody else: every fact on the page carries the same weight, in the
same type size, so the page reads as a log file and the reader has to already
know which line matters. Somebody arriving from a link learns nothing.

This module is the other half. Five pure functions returning HTML strings, no
framework, no CDN, no build step, no script. They take the shape
`metrics.summarise` returns and give it a scale: one numeral per fact that
matters, a picture of the pipeline, and a band of plain language above both.

    hero_tiles(headline)   the numbers, loudest thing on the page
    funnel(stages)         the pipeline, as one horizontal strip
    sparkline(activity)    the shape of the last N days
    explainer(...)         what this is, for somebody who has never seen it
    APP_CSS                the stylesheet all four need

Nothing here counts anything. A renderer that derives its own numbers is a
second source of truth for them and the two drift, so what arrives is what is
drawn. The only arithmetic is geometry: a bar height is a count divided by the
largest count, which is a fact about the picture and not a claim about the
fleet. In particular there is no label for the difference between two adjacent
stages. It is computable, and it would still be a lie: 72 specialist responses
next to 24 verdicts is 24 runs converging, not 48 things lost, and a `-48`
printed in that gap asserts the second one.

`APP_CSS` expects `dashboard._CSS` on the same page. It takes `--bg --fg --dim
--line --card --good --bad --warn` from there and redefines none of them,
because two copies of a palette is how the same colour comes to mean two
things. What it adds is one accent and a type scale. The accent means "this is
the shape of the thread": the funnel bars, the sparkline, the rule down the
explainer. It is never used for a verdict, because green, red and amber already
mean that and an accent that also means approval is an accent that lies once.

Three empty states stay apart here, as everywhere in this product:

    zero      it can happen and has not, which is a fact about this window
    outside   the thread is a tail and the answer is behind its front edge
    unknown   nothing here can see it, and printing 0 would be a claim

The funnel is where the distinction earns its keep. A stage with `count: None`
is a dashed outline with no numeral inside it; a stage with `count: 0` is a
solid floor with a `0` on it. Neither can be mistaken for the other.

The third state, "outside this window", has no structural slot in the contract:
a stage carries `int | None` and nothing else, so a tail can only arrive as
prose in `note`. That is left as prose deliberately rather than guessed at from
a count, and the note is rendered verbatim. `_CSS` already styles all three
(`.z`, `.g`, `.u`) and this module reuses those classes rather than inventing a
fourth vocabulary for the same three ideas.
"""

from __future__ import annotations

import html
from typing import Any, Optional

# What each empty state says out loud. Constants rather than literals, so a page
# and its test cannot disagree about the wording, which is the whole mechanism
# by which these three stay distinguishable.
NO_HEADLINE = "no headline figures were passed to this page"
NO_STAGES = "no pipeline stages were passed to this page"
NO_ACTIVITY = "no day-by-day activity was passed to this page"
NO_READABLE_DAYS = "no day here carried a run count this page could read"
NO_VALUE = "not known here"
STAGE_UNKNOWN = "not known"
STAGE_UNKNOWN_NOTE = "not knowable from this window"
STAGE_ZERO_NOTE = "0 in this window"

# The tones the contract defines. Whitelisted rather than escaped: this reaches
# a class attribute, which is a code path and not text, and a tone nobody wrote
# a style for should render as an ordinary number rather than as no style at all.
_TONES = ("good", "bad", "warn", "plain", "unknown")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _items(value: Any) -> list[Any]:
    """A list from whatever arrived, without iterating something that is not one.

    A string is iterable and a dict iterates its keys, so both would silently
    become a row of one-character tiles rather than an empty state.
    """
    if value is None or isinstance(value, (str, bytes, dict)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _count(value: Any) -> Optional[int]:
    """An int, or None for anything that is not one.

    `True` is an int in Python and would draw a bar of 1 that nobody counted, so
    booleans are refused along with everything else. Refusing to unknown rather
    than to zero is the point: zero is a claim about the window, and a value
    this function could not read is not evidence for it.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def _num(value: float) -> str:
    """A coordinate, short enough to read in the source of the page."""
    return f"{value:g}"


def _tone(value: Any) -> str:
    name = str(value or "plain")
    return name if name in _TONES else "plain"


def _band(text: str) -> str:
    """The whole component, absent. Not a blank space where one should be."""
    return f'<p class="band-empty">{_esc(text)}</p>'


def _wrap(text: str, width: int, lines: int) -> list[str]:
    """Greedy wrap into at most `lines` lines of at most `width` characters.

    SVG text does not wrap, and a stage label is the part a reader actually
    reads, so the wrapping happens here. Everything past the last line is cut
    with an ellipsis rather than dropped, because a label silently missing its
    last word is a label that means something else.
    """
    words = str(text).split()
    if not words:
        return []
    rows = [""]
    cut = False
    for word in words:
        candidate = (rows[-1] + " " + word).strip()
        if len(candidate) <= width or not rows[-1]:
            rows[-1] = candidate
        elif len(rows) < lines:
            rows.append(word)
        else:
            cut = True
            break
    rows = [row if len(row) <= width else row[: width - 1] + "…" for row in rows]
    if cut and not rows[-1].endswith("…"):
        tail = rows[-1]
        rows[-1] = tail[: width - 1] + "…" if len(tail) >= width else tail + "…"
    return rows


# --------------------------------------------------------------------------
# 1. The numbers
# --------------------------------------------------------------------------


def _tile(tile: dict[str, Any], rank: str = "primary") -> str:
    """One card. `rank` is which band it landed in, not what the data claims.

    Taken from the band rather than from `tile["primary"]` on purpose: a caller
    that flags nothing gets one band of equals, and every tile in it is a
    headline. Read from the key instead, that same page marks all nine tiles
    `secondary` and demotes a row that has nothing to be demoted against.
    """
    raw = tile.get("value")
    # Not `or ""`. A headline whose whole point is a zero, and zero writes
    # against twenty-four plans proposed is exactly that, is falsy, and would
    # have been replaced by the unknown state: the one substitution this
    # product exists to prevent.
    value = "" if raw is None else str(raw).strip()
    tone = _tone(tile.get("tone"))
    unit = str(tile.get("unit") or "").strip()
    caption = str(tile.get("caption") or "").strip()
    label = str(tile.get("label") or tile.get("key") or "").strip()

    if not value:
        value, tone, unit = NO_VALUE, "unknown", ""

    # Three steps, because the contract puts three kinds of thing in this slot:
    # a numeral, a short phrase that is still a reading ("24 of 24"), and a
    # sentence standing in for one ("none in this window"). At 2.7rem the third
    # breaks the grid, and at the third's size the first stops being a headline.
    size = "" if len(value) <= 6 else (" is-mid" if len(value) <= 11 else " is-long")
    unit_html = f'<span class="tile-unit">{_esc(unit)}</span>' if unit else ""
    caption_html = (
        f'<p class="tile-caption">{_esc(caption)}</p>' if caption else ""
    )
    key = tile.get("key")
    handle = f' data-kpi="{_esc(key)}"' if key else ""
    # The arithmetic as a tooltip as well as in the footnotes below, so a
    # reader who doubts one figure does not have to leave the band to check.
    method = str(tile.get("method") or "").strip()
    title = f' title="{_esc(method)}"' if method else ""
    return (
        f'<article class="tile {rank} tone-{tone}"{handle}{title}>'
        f'<div class="tile-label">{_esc(label or "unlabelled figure")}</div>'
        f'<div class="tile-value{size}">{_esc(value)}{unit_html}</div>'
        f"{caption_html}</article>"
    )


def hero_tiles(headline: Any) -> str:
    """The headline figures as cards. `headline` is `summarise()["headline"]`."""
    tiles = [t for t in _items(headline) if isinstance(t, dict)]
    if not tiles:
        return _band(NO_HEADLINE)
    # Four, then the rest, smaller. Nine tiles weighted the same is a list,
    # and a list is what the page before this one was rejected for being.
    lead = [t for t in tiles if t.get("primary")]
    rest = [t for t in tiles if not t.get("primary")]
    if not lead:
        lead, rest = tiles, []
    out = _hero_band(lead, "", "primary")
    if rest:
        out += _hero_band(rest, " hero-rest", "secondary")
    return out


def _hero_band(tiles: list, extra: str, rank: str) -> str:
    return (
        f'<section class="hero{extra}">'
        f'{"".join(_tile(t, rank) for t in tiles)}</section>'
    )


def how_counted(headline) -> str:
    """Every figure, with the arithmetic that produced it.

    The caption on a tile is for reading; this is for checking. Both exist
    because dropping the second leaves nine numbers a reader has to take on
    trust, on a page belonging to a product whose whole argument is that you
    should not have to.
    """
    rows = ""
    for tile in _items(headline):
        if not isinstance(tile, dict):
            continue
        method = str(tile.get("method") or "").strip()
        if not method:
            continue
        rows += (
            f'<div class="method"><div class="method-k">'
            f'{_esc(tile.get("label") or tile.get("key") or "unlabelled figure")}</div>'
            f'<div class="method-v">{_esc(method)}</div></div>'
        )
    if not rows:
        return ""
    return (
        '<details class="counted"><summary>how each figure is counted</summary>'
        f"{rows}</details>"
    )


# --------------------------------------------------------------------------
# 2. The pipeline
# --------------------------------------------------------------------------

# One stage is one column of this many user units, and the SVG is rendered at no
# less than one unit per pixel, so a label set at 12 units is a 12px label on a
# phone rather than a scaled-down smudge. The strip scrolls instead.
_STAGE_W = 118
_BAR_W = 92
_TOP = 34
_BASE = 118
_BAND = _BASE - _TOP
_LABEL_Y = 137
_LINE_H = 14
_FUNNEL_H = 170
_LABEL_CHARS = 15
# A stage that happened, however rarely, must not be a stage that did not: the
# smallest bar for a positive count is taller than the floor a zero draws.
_ZERO_H = 3.0
_MIN_BAR_H = 6.0


def _stage_title(name: str, count: Optional[int], note: str) -> str:
    value = STAGE_UNKNOWN if count is None else f"{count:,}"
    return f"{name}: {value}. {note}" if note else f"{name}: {value}"


def funnel(stages: Any) -> str:
    """The pipeline as one horizontal strip. `stages` is `summarise()["funnel"]`.

    Bars are drawn against the largest count in the strip, and the block between
    two stages is filled, so the shape of the drop is the picture. A stage that
    cannot be known from this window is drawn as an outline and is joined to
    nothing, because a slope down to it would be a quantity nobody has.
    """
    items = [s for s in _items(stages) if isinstance(s, dict)]
    if not items:
        return _band(NO_STAGES)

    counts = [_count(s.get("count")) for s in items]
    names = [
        str(s.get("stage") or "").strip() or "unnamed stage" for s in items
    ]
    peak = max([c for c in counts if c is not None], default=0)
    width = len(items) * _STAGE_W

    tops: list[Optional[float]] = []
    for count in counts:
        if count is None:
            tops.append(None)
        elif count <= 0 or peak <= 0:
            tops.append(_BASE - _ZERO_H)
        else:
            tops.append(_BASE - max(_MIN_BAR_H, round(_BAND * count / peak, 1)))

    body = (
        f'<line class="fn-base" x1="0" y1="{_BASE}" x2="{width}" y2="{_BASE}"/>'
    )

    # Connectors first, so a bar is never drawn under the block leading into it.
    for index in range(len(items) - 1):
        left, right = tops[index], tops[index + 1]
        if left is None or right is None:
            continue
        x1 = index * _STAGE_W + (_STAGE_W + _BAR_W) / 2
        x2 = (index + 1) * _STAGE_W + (_STAGE_W - _BAR_W) / 2
        body += (
            f'<polygon class="fn-link" points="{_num(x1)},{_num(left)} '
            f'{_num(x2)},{_num(right)} {_num(x2)},{_BASE} {_num(x1)},{_BASE}"/>'
        )

    for index, count in enumerate(counts):
        x0 = index * _STAGE_W
        centre = x0 + _STAGE_W / 2
        bar_x = x0 + (_STAGE_W - _BAR_W) / 2
        if count is None:
            mark = (
                f'<rect class="fn-unknown" x="{_num(bar_x)}" y="{_TOP}" '
                f'width="{_BAR_W}" height="{_BAND}" rx="3"/>'
            )
            numeral = (
                f'<text class="fn-nocount" x="{_num(centre)}" y="22">'
                f"{_esc(STAGE_UNKNOWN)}</text>"
            )
        else:
            top = tops[index] or 0.0
            mark = (
                f'<rect class="fn-bar" x="{_num(bar_x)}" y="{_num(top)}" '
                f'width="{_BAR_W}" height="{_num(_BASE - top)}" rx="2"/>'
            )
            numeral = (
                f'<text class="fn-count" x="{_num(centre)}" y="24">'
                f"{_esc(f'{count:,}')}</text>"
            )
        label = "".join(
            f'<tspan x="{_num(centre)}" dy="{0 if line == 0 else _LINE_H}">'
            f"{_esc(text)}</tspan>"
            for line, text in enumerate(_wrap(names[index], _LABEL_CHARS, 2))
        )
        note = str(items[index].get("note") or "").strip()
        body += (
            f"<g><title>{_esc(_stage_title(names[index], count, note))}</title>"
            f"{mark}{numeral}"
            f'<text class="fn-stage" y="{_LABEL_Y}">{label}</text></g>'
        )

    described = ", ".join(
        f"{names[i]} {STAGE_UNKNOWN if c is None else format(c, ',')}"
        for i, c in enumerate(counts)
    )
    svg = (
        f'<svg viewBox="0 0 {width} {_FUNNEL_H}" role="img" '
        f'aria-label="{_esc("the pipeline: " + described)}" '
        f'preserveAspectRatio="xMinYMid meet" '
        f'style="min-width:{width}px;max-width:{int(width * 1.4)}px">'
        f"{body}</svg>"
    )

    # Only the two empty states get a line of prose. A stage with a count in it
    # has already said what it has to say, and repeating every note under the
    # strip would bury the two that need reading.
    notes = ""
    for index, count in enumerate(counts):
        note = str(items[index].get("note") or "").strip()
        if count is None:
            state, text = "u", note or STAGE_UNKNOWN_NOTE
        elif count == 0:
            state, text = "z", note or STAGE_ZERO_NOTE
        else:
            continue
        notes += (
            f'<p class="fn-note"><span class="fn-note-key">'
            f"{_esc(names[index])}</span> "
            f'<span class="{state}">{_esc(text)}</span></p>'
        )
    tail = f'<div class="funnel-notes">{notes}</div>' if notes else ""

    return f'<div class="funnel"><div class="funnel-strip">{svg}</div>{tail}</div>'


# --------------------------------------------------------------------------
# 3. The shape of the last N days
# --------------------------------------------------------------------------

_SP_W = 600
_SP_H = 54
_SP_PAD = 7
_SP_TOP = 8
_SP_FLOOR = 46


def sparkline(activity: Any) -> str:
    """The run count per day. `activity` is `summarise()["activity"]`.

    No axes and no gridlines: the shape is the whole message, and the two values
    worth naming are named in words underneath, where they are still legible on
    a phone.
    """
    days = [d for d in _items(activity) if isinstance(d, dict)]
    series: list[tuple[str, int]] = []
    unreadable = 0
    for day in days:
        runs = _count(day.get("runs"))
        if runs is None:
            unreadable += 1
            continue
        series.append((str(day.get("day") or "").strip(), runs))

    if not series:
        return _band(NO_READABLE_DAYS if unreadable else NO_ACTIVITY)

    total = len(series)
    peak = max(runs for _, runs in series)
    span = _SP_FLOOR - _SP_TOP

    def x_at(index: int) -> float:
        if total == 1:
            return _SP_W / 2
        return _SP_PAD + index * (_SP_W - 2 * _SP_PAD) / (total - 1)

    def y_at(runs: int) -> float:
        if peak <= 0:
            return _SP_FLOOR
        return _SP_FLOOR - span * max(0, runs) / peak

    points = [(x_at(i), y_at(runs)) for i, (_, runs) in enumerate(series)]
    drawn = " ".join(f"{_num(x)},{_num(y)}" for x, y in points)

    body = ""
    if total > 1:
        floor = _SP_FLOOR + 4
        area = (
            f"{_num(points[0][0])},{_num(floor)} {drawn} "
            f"{_num(points[-1][0])},{_num(floor)}"
        )
        body += f'<polygon class="sp-area" points="{area}"/>'
        body += f'<polyline class="sp-line" points="{drawn}"/>'
    if peak > 0:
        for index, (_, runs) in enumerate(series):
            if runs == peak:
                x, y = points[index]
                body += f'<circle class="sp-peak" cx="{_num(x)}" cy="{_num(y)}" r="4.2"/>'
    body += (
        f'<circle class="sp-last" cx="{_num(points[-1][0])}" '
        f'cy="{_num(points[-1][1])}" r="3.4"/>'
    )

    head = f"{total:,} {'day' if total == 1 else 'days'} shown"
    rest: list[str] = []
    if peak <= 0:
        # Not "peak 0". Every day counted and every count was zero, which is a
        # different sentence from a peak, and the one the reader needs.
        rest.append("no runs on any of them")
    else:
        at_peak = [day for day, runs in series if runs == peak]
        if len(at_peak) == 1:
            where = (
                f" on {at_peak[0]}" if at_peak[0] else ", on a day with no date recorded"
            )
            rest.append(f"peak {_plural(peak, 'run')}{where}")
        else:
            rest.append(
                f"peak {_plural(peak, 'run')}, reached on {len(at_peak)} of these days"
            )
        last_day, last_runs = series[-1]
        tail = f" on {last_day}" if last_day else ""
        rest.append(f"latest {_plural(last_runs, 'run')}{tail}")

    dropped = (
        f"{_plural(unreadable, 'day')} carried no run count this page could read"
        if unreadable
        else ""
    )

    caption = f'<span class="spark-key">{_esc(head)}</span>'
    caption += "".join(f" · {_esc(part)}" for part in rest)
    if dropped:
        caption += f' · <span class="u">{_esc(dropped)}</span>'

    # The accessible name is the caption, in the same words. A picture and its
    # description that disagree is two answers to one question.
    spoken = ", ".join([f"runs per day: {head}"] + rest + ([dropped] if dropped else []))
    svg = (
        f'<svg viewBox="0 0 {_SP_W} {_SP_H}" role="img" '
        f'aria-label="{_esc(spoken)}">{body}</svg>'
    )
    return f'<div class="spark">{svg}<p class="spark-caption">{caption}</p></div>'


# --------------------------------------------------------------------------
# 4. What this is
# --------------------------------------------------------------------------

# Plain language, and deliberately free of numbers. Every figure on this page is
# computed from the thread and carries a caption saying what it counted; a
# number typed into the prose here would be the one nobody could check.
LEAD = (
    "Mitos keeps the paperwork attached to a code change. A pull request that "
    "touches personal data wakes a fleet of agents: they read the repository, "
    "work out which specialists the change concerns, draft the updates that "
    "have to follow it, and hand one diff to a human to approve. Every step is "
    "recorded in one thread you can follow back, months later, when somebody "
    "asks who approved what."
)

POINTS: tuple[tuple[str, str], ...] = (
    (
        "nobody polls it",
        "Writing a deferral hands the fleet every open one, and it escalates "
        "the expired ones unattended. The calendar alone wakes nothing: that "
        "would need a durable timer, and this build does not have one.",
    ),
    (
        "its own gate refuses it",
        "A deterministic gate checks the draft before a human sees it. The "
        "model behind the gate may add findings and never remove one.",
    ),
    (
        "one write, and a person holds it",
        "The service that reads production data holds no credential that can "
        "write it. The single write is bound to the exact bytes approved.",
    ),
)


def explainer(
    *,
    lead: str = LEAD,
    points: Any = None,
    aside: str = "",
) -> str:
    """The band at the top, for a reader who arrived from a link.

    `points` is a sequence of (term, line) pairs. A bare string is accepted as a
    line with no term, because the alternative is a page that raises on a
    caller's typo.
    """
    rows = ""
    for point in _items(POINTS if points is None else points):
        if isinstance(point, str):
            term, line = "", point
        elif isinstance(point, (tuple, list)) and len(point) >= 2:
            term, line = str(point[0]), str(point[1])
        elif isinstance(point, (tuple, list)) and point:
            term, line = "", str(point[0])
        else:
            continue
        term_html = (
            f'<span class="explain-term">{_esc(term)}</span>' if term.strip() else ""
        )
        rows += (
            f'<div class="explain-point">{term_html}'
            f'<span class="explain-line">{_esc(line)}</span></div>'
        )

    text = str(lead or "").strip()
    body = f'<p class="explain-lead">{_esc(text)}</p>' if text else ""
    if rows:
        body += f'<div class="explain-points">{rows}</div>'
    note = str(aside or "").strip()
    if note:
        body += f'<p class="explain-aside">{_esc(note)}</p>'
    return f'<section class="explain">{body}</section>'


# --------------------------------------------------------------------------
# 5. The stylesheet
# --------------------------------------------------------------------------

# Sits after `dashboard._CSS` on the same page and takes the palette from it.
# What is added is one accent and a type scale. The scale is most of the fix:
# the existing page has a single text size, which is why it reads as a log file
# even where the content is not one.
APP_CSS = """
.explain,.explain-lead,.tile-label,.tile-caption,.fn-note,.spark-caption,.method-k,.method-v,.counted summary{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;letter-spacing:0}
.explain-lead{max-width:66ch}
@media(min-width:58rem){.explain{display:grid;grid-template-columns:minmax(0,32rem) 1fr;gap:1.6rem;align-items:start}.explain-points{display:grid;gap:.9rem}}
.funnel-strip{mask-image:linear-gradient(90deg,#000 calc(100% - 2.2rem),transparent);-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 2.2rem),transparent);scrollbar-width:thin}
@media(min-width:46rem){.funnel-strip{mask-image:none;-webkit-mask-image:none}}
:root{--accent:#7aa2f7;--accent-soft:#7aa2f722;
 --t-kpi:clamp(1.9rem,6.4vw,2.7rem);--t-num:clamp(1.3rem,4vw,1.7rem);
 --t-mid:1.15rem;--t-lead:.98rem;--t-cap:.76rem;--t-micro:.68rem}
@media(prefers-color-scheme:light){
  :root{--accent:#2f4fd0;--accent-soft:#2f4fd014}}

.band-empty{margin:0 0 1rem;padding:.8rem 1rem;font-size:var(--t-cap);
 color:var(--dim);border:1px dashed var(--line);border-radius:8px}

.hero{display:grid;gap:.7rem;margin:0 0 1rem;
 grid-template-columns:repeat(auto-fit,minmax(12rem,1fr))}
.tile{display:flex;flex-direction:column;gap:.3rem;min-width:0;
 padding:.9rem 1rem 1rem;background:var(--card);border:1px solid var(--line);
 border-radius:8px}
.tile-label{font-size:var(--t-micro);letter-spacing:.1em;text-transform:uppercase;
 color:var(--dim)}
/* The one thing on the page that is allowed to shout. */
.tile-value{font-size:var(--t-kpi);line-height:1.05;font-weight:600;
 letter-spacing:-.02em;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.tile-value.is-mid{font-size:var(--t-num);line-height:1.2}
.tile-value.is-long{font-size:var(--t-mid);line-height:1.3;font-weight:500}
.tile-unit{margin-left:.35rem;font-size:var(--t-cap);font-weight:400;
 letter-spacing:0;color:var(--dim)}
.tile-caption{margin:.15rem 0 0;font-size:var(--t-cap);line-height:1.5;
 color:var(--dim)}
/* The two ranks. These come after the base tile so the demotion actually
   applies, and they are keyed to the rank rather than to the second band: the
   band is where a tile sits, the rank is how loud it is, and a page with one
   band of equals has to leave every tile at full strength. */
.tile.primary .tile-label{color:var(--fg)}
.tile.secondary{padding:.7rem .8rem;background:none}
/* One step below the quietest primary, so the loudest tile in this band never
   matches a tile in the one above it. */
.tile.secondary .tile-value,.tile.secondary .tile-value.is-mid{
 font-size:var(--t-mid)}
.tile.secondary .tile-value.is-long{font-size:var(--t-lead);line-height:1.35}
.tile.secondary .tile-caption{font-size:var(--t-micro);line-height:1.45}
/* Smaller tiles, so more of them fit a row before wrapping. */
.hero-rest{gap:.5rem;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr))}

.tile.tone-plain .tile-value{color:var(--fg)}
.tile.tone-good .tile-value{color:var(--good)}
.tile.tone-bad .tile-value{color:var(--bad)}
.tile.tone-warn .tile-value{color:var(--warn)}
/* Dashed, dim and unbolded: a tile that cannot answer should not look like a
   tile that answered zero, at any distance. */
.tile.tone-unknown{border-style:dashed}
.tile.tone-unknown .tile-value{color:var(--dim);font-weight:400}

.funnel{margin:0 0 1rem}
/* Scrolls rather than shrinks. Scaled to a phone the strip still fits, and
   every label in it becomes unreadable, which is a picture of nothing. */
.funnel-strip{overflow-x:auto;overscroll-behavior-x:contain;padding-bottom:.25rem}
.funnel-strip svg{display:block;width:100%;height:auto;color:var(--accent)}
.funnel .fn-bar{fill:currentColor}
.funnel .fn-link{fill:currentColor;fill-opacity:.16}
.funnel .fn-unknown{fill:none;stroke:var(--dim);stroke-width:1.2;
 stroke-dasharray:5 4}
.funnel .fn-base{stroke:var(--line);stroke-width:1}
.funnel .fn-count{fill:var(--fg);font-size:19px;font-weight:600;
 text-anchor:middle;font-variant-numeric:tabular-nums}
.funnel .fn-nocount{fill:var(--dim);font-size:11px;text-anchor:middle}
.funnel .fn-stage{fill:var(--dim);font-size:12px;text-anchor:middle}
.funnel-notes{margin-top:.5rem}
.fn-note{margin:.15rem 0 0;font-size:var(--t-cap);line-height:1.45}
.fn-note-key{color:var(--fg)}

.spark{margin:0 0 1rem;color:var(--accent)}
/* Scaled uniformly, not stretched to a fixed height: a dot marking the peak
   under a horizontal stretch is an ellipse pointing nowhere. */
.spark svg{display:block;width:100%;height:auto}
.spark .sp-area{fill:currentColor;fill-opacity:.13}
.spark .sp-line{fill:none;stroke:currentColor;stroke-width:1.6;
 stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}
.spark .sp-peak{fill:none;stroke:currentColor;stroke-width:1.6;
 vector-effect:non-scaling-stroke}
.spark .sp-last{fill:var(--fg)}
.spark-caption{margin:.3rem 0 0;font-size:var(--t-cap);color:var(--dim)}
.spark-key{color:var(--fg)}

/* Tinted rather than card-coloured, and the only tinted block on the page: it
   is the one band that is prose for a newcomer instead of a number from the
   thread, and it should not be mistaken for a reading. */
.explain{margin:0 0 1.1rem;padding:1rem 1.15rem;background:var(--accent-soft);
 border:1px solid var(--line);border-left:2px solid var(--accent);
 border-radius:8px}
.explain-lead{margin:0;max-width:56ch;font-size:var(--t-lead);line-height:1.6}
.explain-points{display:grid;gap:.7rem 1.5rem;margin-top:.9rem;padding-top:.85rem;
 border-top:1px solid var(--line);
 grid-template-columns:repeat(auto-fit,minmax(13rem,1fr))}
.explain-point{min-width:0}
.explain-term{display:block;font-size:var(--t-micro);letter-spacing:.09em;
 text-transform:uppercase;color:var(--accent)}
.explain-line{display:block;margin-top:.2rem;font-size:var(--t-cap);
 line-height:1.5;color:var(--dim)}
.explain-aside{margin:.85rem 0 0;font-size:var(--t-cap);color:var(--dim)}

/* The arithmetic behind every figure, folded away. Open by choice, so it costs
   the reader who trusts the numbers nothing and is there for the one who does
   not, which is the reader this product is for. */
.counted{margin:1.4rem 0;font-size:var(--t-cap)}
.counted summary{cursor:pointer;color:var(--dim);padding:.45rem 0}
.method{display:grid;gap:.6rem;padding:.5rem 0;border-top:1px solid var(--line);
 grid-template-columns:minmax(9rem,14rem) 1fr}
.method-k{color:var(--dim)}
.method-v{overflow-wrap:anywhere}

@media(max-width:40rem){
  .method{grid-template-columns:1fr;gap:.15rem}
}
@media(max-width:34rem){
  .hero,.hero-rest{grid-template-columns:repeat(2,minmax(0,1fr));gap:.5rem}
  .tile{padding:.7rem .8rem .8rem;gap:.25rem}
  .explain{padding:.85rem .9rem}
  .explain-points{grid-template-columns:1fr;gap:.55rem}
}
"""
