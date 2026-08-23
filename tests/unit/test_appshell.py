"""The visual layer, tested as four pure functions and a stylesheet.

Four things have to hold, and they are the four ways this layer lies.

It has to render with nothing, and with half of something. These functions are
fed whatever `metrics.summarise` produced, and the day the ledger is new, or the
day a field arrives as a string because an endpoint changed shape, is not the
day the page should raise.

It has to escape. A pull request title is typed by whoever opened the pull
request and reaches these components through the thread. The assertions are two
sided on purpose: checking only that `<script>` is absent passes just as well
when the field was silently dropped, which is the other way to get this wrong.

A `None` count and a `0` count have to look different. That is the whole reason
the funnel is drawn rather than listed, and a renderer that draws them the same
undoes the distinction the rest of the product is built to keep.

And the page has to be self-contained. No CDN, no font, no tracking pixel: a
dashboard that phones somewhere to draw itself is a dashboard that stops drawing
on the day the judge opens it, and one that reports who opened it.

Two of these are structural rather than behavioural, and both come from a bug
this repository already had: `render_standards` emitted `class="unknown"` for a
verdict nobody had styled, so "could not be determined" rendered as plain text
beside a green pass. A class the stylesheet does not know and a custom property
nobody declared are the same failure, so both are checked by walking what the
markup actually emits.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from service.appshell import (
    APP_CSS,
    NO_ACTIVITY,
    NO_HEADLINE,
    NO_READABLE_DAYS,
    NO_STAGES,
    NO_VALUE,
    STAGE_UNKNOWN,
    STAGE_UNKNOWN_NOTE,
    STAGE_ZERO_NOTE,
    explainer,
    funnel,
    hero_tiles,
    how_counted,
    sparkline,
)
from service.dashboard import _CSS

# Assembled rather than written out, so a secret scanner reading this file does
# not see a live payload and so each half is legible on its own.
CLOSE = "</" + "script>"
BREAKOUT = CLOSE + '<img src=x onerror="alert(1)">'
HOSTILE = '"><img src=x onerror="alert(1)"> mobileNumber'


def a_headline(**over):
    tile = {
        "key": "writes",
        "label": "writes executed",
        "value": "0",
        "unit": "",
        "caption": "nothing was approved through the API in this window",
        "tone": "good",
    }
    tile.update(over)
    return tile


def a_funnel():
    return [
        {"stage": "dispatches", "count": 24, "note": ""},
        {"stage": "plans proposed", "count": 24, "note": ""},
        {"stage": "approvals", "count": None, "note": "decided outside this service"},
        {"stage": "writes executed", "count": 0, "note": "every run stopped at the gate"},
    ]


def an_activity():
    return [
        {"day": "2026-08-21", "runs": 2},
        {"day": "2026-08-22", "runs": 7},
        {"day": "2026-08-23", "runs": 3},
    ]


def every_component() -> list[str]:
    """One rendering of each, with ordinary data."""
    return [
        hero_tiles([a_headline(), a_headline(key="plans", primary=True)]),
        funnel(a_funnel()),
        sparkline(an_activity()),
        explainer(),
        how_counted([a_headline(method="entries whose kind is write.executed")]),
    ]


# --------------------------------------------------------------------------
# It renders with nothing, and with half of something
# --------------------------------------------------------------------------


def test_every_component_renders_with_nothing():
    assert NO_HEADLINE in hero_tiles([])
    assert NO_STAGES in funnel([])
    assert NO_ACTIVITY in sparkline([])
    assert "<section" in explainer(lead="", points=[])

    # None, and the wrong container entirely. A string is iterable and a dict
    # iterates its keys, so both would otherwise become a row of one-character
    # tiles rather than an empty state.
    for empty in (None, "", {}, "specialist.response", 7):
        assert NO_HEADLINE in hero_tiles(empty)
        assert NO_STAGES in funnel(empty)
        assert NO_ACTIVITY in sparkline(empty)


def test_every_component_renders_with_half_of_something():
    tiles = hero_tiles(
        [
            {},
            {"label": "runs"},
            {"value": "12"},
            {"value": "3", "tone": "nonsense"},
            "not a tile at all",
            None,
        ]
    )
    assert "<article" in tiles
    assert tiles.count("<article") == 4

    strip = funnel(
        [
            {},
            {"stage": "dispatches"},
            {"count": 4},
            {"stage": "plans", "count": "24"},
            {"stage": "writes", "count": True},
            "not a stage",
        ]
    )
    assert "<svg" in strip
    assert "unnamed stage" in strip

    shape = sparkline(
        [{}, {"day": "2026-08-23"}, {"runs": 3}, {"day": None, "runs": 1}, "no"]
    )
    assert "<svg" in shape

    assert "<section" in explainer(
        lead=None, points=["a bare line", ("term", "line"), (), None, 5]
    )


def test_a_count_that_is_not_an_int_is_unknown_rather_than_zero():
    """`True` is an int in Python, and `"24"` is not one.

    Both would be read as a number if this were `int(...)` or a truthiness test,
    and a bar drawn from either is a quantity nobody recorded.
    """
    strip = funnel(
        [
            {"stage": "counted", "count": 24},
            {"stage": "a string", "count": "24"},
            {"stage": "a boolean", "count": True},
        ]
    )

    assert counts_drawn(strip) == ["24"]
    assert strip.count(STAGE_UNKNOWN_NOTE) == 2

    shape = sparkline([{"day": "2026-08-23", "runs": "9"}])
    assert NO_READABLE_DAYS in shape


def test_a_day_with_no_readable_count_is_named_rather_than_dropped():
    shape = sparkline(
        [{"day": "2026-08-22", "runs": 4}, {"day": "2026-08-23", "runs": None}]
    )

    assert "1 day shown" in shape
    assert "carried no run count this page could read" in shape


def test_all_zero_activity_is_a_fact_and_not_a_crash():
    """Every day at zero divides by a peak of zero on the way to a height."""
    shape = sparkline([{"day": "2026-08-22", "runs": 0}, {"day": "2026-08-23", "runs": 0}])

    assert "2 days shown" in shape
    assert "no runs on any of them" in shape
    assert "peak" not in shape


# --------------------------------------------------------------------------
# A None count is not a zero count
# --------------------------------------------------------------------------


def counts_drawn(page: str) -> list[str]:
    """The numerals the funnel actually printed, and nothing else.

    Scoped to the count elements deliberately: asserting `"0" not in page` would
    match a coordinate, a viewBox or a date, and would pass for the wrong reason
    on the day the geometry changes.
    """
    return re.findall(r'<text class="fn-count"[^>]*>([^<]*)</text>', page)


def test_a_none_count_and_a_zero_count_cannot_be_confused():
    unknown = funnel([{"stage": "approvals", "count": None}])
    zero = funnel([{"stage": "writes executed", "count": 0}])

    assert unknown != zero

    # The unknown stage prints no numeral at all. This is the assertion the
    # house rule is about: never print 0 for the state that cannot be known.
    assert counts_drawn(unknown) == []
    assert STAGE_UNKNOWN in unknown
    assert STAGE_UNKNOWN_NOTE in unknown

    # The zero stage prints one, because zero happened and is worth reading.
    assert counts_drawn(zero) == ["0"]
    assert STAGE_ZERO_NOTE in zero
    assert STAGE_UNKNOWN not in zero

    # And they are drawn differently, not merely worded differently: an outline
    # against a solid bar.
    assert "fn-unknown" in unknown and "stroke-dasharray" in APP_CSS
    assert "fn-unknown" not in zero
    assert "fn-bar" in zero and "fn-bar" not in unknown


def test_an_unknown_stage_is_joined_to_nothing():
    """A slope down to a stage nobody counted is a quantity nobody has."""
    joined = funnel([{"stage": "a", "count": 4}, {"stage": "b", "count": 2}])
    broken = funnel([{"stage": "a", "count": 4}, {"stage": "b", "count": None}])

    assert "fn-link" in joined
    assert "fn-link" not in broken


def test_a_stage_that_happened_is_taller_than_a_stage_that_did_not():
    """One run out of a hundred must not round down to the floor a zero draws."""
    strip = funnel([{"stage": "many", "count": 400}, {"stage": "one", "count": 1}])
    heights = [float(h) for h in re.findall(r'class="fn-bar"[^>]*height="([\d.]+)"', strip)]
    zero_floor = [
        float(h)
        for h in re.findall(
            r'class="fn-bar"[^>]*height="([\d.]+)"',
            funnel([{"stage": "none", "count": 0}]),
        )
    ]

    assert len(heights) == 2
    assert heights[1] > zero_floor[0]


def test_a_tile_with_no_value_says_so_rather_than_showing_a_zero():
    tiles = hero_tiles([{"label": "open deferrals", "value": None, "tone": "plain"}])

    assert NO_VALUE in tiles
    assert ">0<" not in tiles
    assert "tone-unknown" in tiles


def test_the_headline_slot_holds_a_numeral_a_phrase_and_a_sentence():
    """All three arrive in it, and one size cannot serve all three.

    `24 of 24` is still a reading and should still be loud; `none in this
    window` is a sentence and at the numeral's size it breaks the grid.
    """
    sizes = {
        value: re.search(r'class="tile-value([^"]*)"', hero_tiles([a_headline(value=value)]))
        .group(1)
        .strip()
        for value in ("161", "24 of 24", "none in this window")
    }

    assert sizes == {"161": "", "24 of 24": "is-mid", "none in this window": "is-long"}
    for step in ("--t-kpi", "--t-num", "--t-mid"):
        assert f"{step}:" in APP_CSS


def test_the_rank_comes_from_the_band_and_not_from_the_key():
    """A page where nothing is flagged is one band of equals, not nine demotions.

    Read from `tile["primary"]` instead, every tile on such a page is marked
    `secondary` and the stylesheet quietly shrinks a row that has nothing to be
    quieter than.
    """
    flat = hero_tiles([a_headline(), a_headline(key="plans")])
    ranked = hero_tiles([a_headline(primary=True), a_headline(key="plans")])

    assert flat.count("secondary") == 0
    assert flat.count("primary") == 2
    assert flat.count("<section") == 1

    assert ranked.count("primary") == 1
    assert ranked.count("secondary") == 1
    assert ranked.count("<section") == 2
    assert "hero-rest" in ranked


def test_the_second_band_is_quieter_at_every_length():
    """The demotion has to beat all three size steps, or a long value in the
    quiet row renders larger than a short one beside it."""
    css = re.sub(r"\s+", " ", APP_CSS)
    for step in ("", ".is-mid", ".is-long"):
        assert f".tile.secondary .tile-value{step}" in css, step

    base = APP_CSS.index(".tile-value{")
    demoted = APP_CSS.index(".tile.secondary .tile-value")
    assert demoted > base, "the demotion is declared before what it demotes"


def test_the_arithmetic_band_renders_with_nothing_and_with_hostile_text():
    assert how_counted([]) == ""
    assert how_counted(None) == ""
    # No method on any figure is nothing to show, not an empty disclosure.
    assert how_counted([a_headline()]) == ""

    shown = how_counted([a_headline(method="entries whose kind is write.executed")])
    assert "how each figure is counted" in shown
    assert "write.executed" in shown

    parsed = Parsed(how_counted([{"method": BREAKOUT, "label": HOSTILE}]))
    assert not FORBIDDEN & set(parsed.elements)
    assert BREAKOUT in parsed.content
    assert "unlabelled figure" in how_counted([{"method": "counted from the thread"}])


def test_a_zero_headline_survives_being_falsy():
    """Zero writes against plans proposed is the product working.

    Written as `value or ""` it becomes the unknown state, which is the one
    substitution this whole module exists to prevent.
    """
    tiles = hero_tiles([a_headline(value="0")])

    assert ">0<" in tiles
    assert NO_VALUE not in tiles
    assert "tone-good" in tiles


# --------------------------------------------------------------------------
# It escapes
# --------------------------------------------------------------------------


class Parsed(HTMLParser):
    """What a browser would actually build out of the string.

    Substring assertions are not enough here in either direction. `onerror=`
    appears in correctly escaped text and is inert there, so searching for it
    fails a page that is right; and a page could pass every substring check
    while having dropped the field it was supposed to render. Parsing answers
    both: what elements exist, what attributes they carry, and what is left as
    text.
    """

    def __init__(self, page: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[str] = []
        self.attributes: list[tuple[str, str]] = []
        self.text: list[str] = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.elements.append(tag)
        self.attributes.extend((name, value or "") for name, value in attrs)

    def handle_data(self, data):
        self.text.append(data)

    @property
    def content(self) -> str:
        return "".join(self.text)


# Elements that fetch, execute, or both. None of them is emitted by this module,
# so any of them appearing came from a payload.
FORBIDDEN = {"script", "img", "iframe", "object", "embed", "link", "base", "svg:script"}


def test_no_component_lets_attacker_controlled_text_become_markup():
    pages = [
        hero_tiles(
            [
                {
                    "key": HOSTILE,
                    "label": HOSTILE,
                    "value": HOSTILE,
                    "unit": HOSTILE,
                    "caption": BREAKOUT,
                    "tone": HOSTILE,
                }
            ]
        ),
        funnel([{"stage": HOSTILE, "count": 3, "note": BREAKOUT}]),
        funnel([{"stage": BREAKOUT, "count": None, "note": HOSTILE}]),
        sparkline([{"day": HOSTILE, "runs": 2}, {"day": BREAKOUT, "runs": 5}]),
        explainer(lead=HOSTILE, points=[(HOSTILE, BREAKOUT)], aside=BREAKOUT),
    ]

    for page in pages:
        parsed = Parsed(page)

        assert not FORBIDDEN & set(parsed.elements)
        assert not [name for name, _ in parsed.attributes if name.startswith("on")]
        assert not [
            value
            for _, value in parsed.attributes
            if value.strip().lower().startswith("javascript:")
        ]
        # Two sided, and this is the half that matters. Dropping the field would
        # satisfy every assertion above; here the payload has to come back out
        # of the parser byte for byte, as text.
        assert HOSTILE in parsed.content or BREAKOUT in parsed.content


def test_hostile_text_survives_intact_rather_than_being_stripped():
    tiles = hero_tiles([a_headline(label=HOSTILE, value="4")])

    assert "&quot;&gt;&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in tiles
    assert "mobileNumber" in tiles


def test_a_tone_nobody_wrote_a_style_for_cannot_reach_the_class_attribute():
    """The tone is whitelisted rather than escaped, because it is a code path.

    Escaping would keep it inert and still leave the tile carrying a class the
    stylesheet does not know, which is how a verdict once rendered as plain text
    beside a green pass on the standards page.
    """
    tiles = hero_tiles([a_headline(tone='good" onmouseover="alert(1)')])

    assert "onmouseover" not in tiles
    assert "tone-plain" in classes_used(tiles)


def test_a_stage_label_too_long_for_its_column_ends_in_an_ellipsis():
    """Cut rather than dropped: a label missing its last word means something else."""
    strip = funnel([{"stage": "a " * 40, "count": 1}])

    assert "…" in strip
    assert "<tspan" in strip


# --------------------------------------------------------------------------
# It is self-contained
# --------------------------------------------------------------------------

# Every way a page can be made to fetch something. `//` catches a
# protocol-relative source, which is the one that survives a search for `http`.
EXTERNAL = (
    "http",
    "//",
    "url(",
    "@import",
    "xlink",
    "href",
    "<image",
    "<use",
    "<script",
    "data:",
    "srcset",
)


def test_nothing_rendered_reaches_outside_the_page():
    for page in every_component():
        for token in EXTERNAL:
            assert token not in page, f"{token} in {page[:80]}"


def test_the_stylesheet_reaches_outside_the_page_for_nothing_either():
    for token in ("http", "//", "url(", "@import", "@font-face"):
        assert token not in APP_CSS, token


def test_the_svg_carries_no_script_and_no_event_handler():
    for page in (funnel(a_funnel()), sparkline(an_activity())):
        assert "<svg" in page
        assert not re.search(r"\bon[a-z]+\s*=", page)


# --------------------------------------------------------------------------
# The stylesheet knows every class and every property the markup uses
# --------------------------------------------------------------------------

BOTH = APP_CSS + _CSS


def classes_used(page: str) -> set[str]:
    """Every class the markup emits, in both quoting styles.

    `dashboard.py` writes `class=r` unquoted and this module writes
    `class="tile"`. A pattern that matches only one of them collects nothing
    from the other and passes while checking nothing, which is the failure mode
    this file's own docstring warns about, so it matches both and the caller
    asserts the set is not empty.
    """
    found: set[str] = set()
    for match in re.finditer(r'class=(?:"([^"]*)"|([^\s>"]+))', page):
        found.update((match.group(1) or match.group(2) or "").split())
    return found


def defined(name: str, css: str) -> bool:
    return re.search(r"\." + re.escape(name) + r"(?![\w-])", css) is not None


def test_every_class_the_markup_emits_is_styled_somewhere():
    used: set[str] = set()
    for page in every_component():
        used |= classes_used(page)
    # The empty states, the two funnel notes and the size steps are their own
    # markup and are not reached by the ordinary renderings above.
    for page in (
        hero_tiles([]),
        funnel(a_funnel()),
        hero_tiles([a_headline(value=None)]),
        hero_tiles([a_headline(value="24 of 24"), a_headline(value="none in this window")]),
        sparkline([{"day": "2026-08-23", "runs": None}, {"day": "2026-08-22", "runs": 1}]),
    ):
        used |= classes_used(page)

    assert "tile-value" in used, "this test is not looking at the markup it thinks it is"
    assert len(used) > 15

    unstyled = sorted(name for name in used if not defined(name, BOTH))
    assert not unstyled, (
        f"classes the markup emits that no stylesheet defines, so they render "
        f"with no styling at all: {unstyled}"
    )


def test_every_custom_property_the_stylesheet_uses_is_declared():
    used = set(re.findall(r"var\(\s*(--[\w-]+)", APP_CSS))
    declared = set(re.findall(r"(--[\w-]+)\s*:", BOTH))

    assert "--accent" in used, "this test is not reading the stylesheet it thinks it is"
    assert len(used) > 5

    missing = sorted(used - declared)
    assert not missing, (
        f"properties used with no declaration anywhere, so they fall back to "
        f"nothing: {missing}"
    )


def test_the_palette_is_not_copied_out_of_the_dashboard_stylesheet():
    """One palette, not two. The same colour meaning two things is how red stops
    meaning refusal."""
    for token in ("--fg:", "--bg:", "--dim:", "--line:", "--card:", "--good:", "--bad:"):
        assert token not in APP_CSS, f"{token} is declared twice, in two files"


def test_all_three_empty_states_are_styled_before_this_module_uses_them():
    """`.z`, `.g` and `.u` are the vocabulary, and they live in `_CSS`.

    `.g` is asserted here although nothing in this module emits it: the funnel
    contract carries `int | None` and has no slot for "the thread is a tail", so
    that state can only arrive as prose in a note. The class has to keep working
    for the day it does.
    """
    for name in ("z", "g", "u"):
        assert defined(name, _CSS), f".{name} is not styled, so it renders as plain text"


def test_the_type_scale_is_a_scale_and_not_one_size():
    """One text size is most of why the page it replaces reads as a log file."""
    sizes = {
        name: value
        for name, value in re.findall(r"(--t-[\w-]+)\s*:\s*([^;}]+)", APP_CSS)
    }

    assert len(sizes) >= 4
    assert "clamp" in sizes["--t-kpi"], "the headline numeral does not respond to width"
    assert all(defined_in_use in APP_CSS for defined_in_use in sizes)


# --------------------------------------------------------------------------
# The explainer says what this is, and claims nothing it cannot show
# --------------------------------------------------------------------------


def test_the_explainer_carries_no_number_of_its_own():
    """Every figure on the page is counted from the thread and captioned with
    what it counted. A number typed into the prose is the one nobody can check,
    on the page whose whole subject is unverifiable claims."""
    assert not re.search(r"\d", explainer())


def test_the_explainer_is_plain_language_rather_than_the_vocabulary():
    page = explainer().lower()

    for jargon in ("firestore", "adk", "sha256", "on_snapshot", "interceptor"):
        assert jargon not in page
