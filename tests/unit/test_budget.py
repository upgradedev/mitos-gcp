"""What an anonymous caller is allowed to cost.

The reader is public on purpose and three of its endpoints are not free to
serve: two run a chore, which is four Gemini calls and a dozen appends to the
provenance thread, and one spends from a GitHub rate limit shared by everyone
using the page.

These tests are about the bound holding, and about it being the right bound: a
limiter that also rationed the read-only pages would make the demo look broken
for no saving at all.
"""

from __future__ import annotations

from pathlib import Path

from service.budget import MAX_TRACKED_CLIENTS, RateLimiter, client_of


class _Request:
    def __init__(self, headers=None, host="203.0.113.7"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})()


def test_the_first_calls_are_allowed_and_the_next_are_not():
    limiter = RateLimiter(limit=3, window_s=600)

    allowed = [limiter.check("a", now=float(i)).allowed for i in range(5)]

    assert allowed == [True, True, True, False, False]


def test_the_window_moves_so_a_caller_is_not_barred_forever():
    limiter = RateLimiter(limit=2, window_s=60)
    limiter.check("a", now=0.0)
    limiter.check("a", now=1.0)

    assert limiter.check("a", now=30.0).allowed is False
    assert limiter.check("a", now=61.5).allowed is True


def test_one_caller_running_out_does_not_bar_another():
    """A shared counter would let one script close the demo for everybody."""
    limiter = RateLimiter(limit=1, window_s=60)
    limiter.check("noisy", now=0.0)

    assert limiter.check("noisy", now=1.0).allowed is False
    assert limiter.check("quiet", now=1.0).allowed is True


def test_the_wait_is_the_real_wait_and_not_the_window():
    """Telling somebody to wait ten minutes when they must wait one is a lie
    that gets the page reloaded nine times."""
    limiter = RateLimiter(limit=1, window_s=600)
    limiter.check("a", now=0.0)

    assert 1 <= limiter.check("a", now=599.0).retry_after_s <= 3


def test_recording_and_deciding_are_one_operation():
    """Split, two concurrent requests both read a count below the limit.

    Asserted by checking that `check` itself consumes: a second call with the
    same clock must see the first.
    """
    limiter = RateLimiter(limit=1, window_s=60)

    assert limiter.check("a", now=5.0).allowed is True
    assert limiter.check("a", now=5.0).allowed is False


def test_the_limiter_cannot_be_turned_into_a_memory_leak():
    """Otherwise the defence is the outage.

    Failing open for a forgotten idle caller is the right direction: a limiter
    that starts refusing everybody under load has become the thing it prevents.
    """
    limiter = RateLimiter(limit=5, window_s=1)
    for i in range(MAX_TRACKED_CLIENTS + 200):
        limiter.check(f"client-{i}", now=float(i))

    assert len(limiter._hits) <= MAX_TRACKED_CLIENTS + 1


def test_the_client_comes_from_the_forwarded_header_that_cloud_run_sets():
    forwarded = _Request({"x-forwarded-for": "198.51.100.9, 10.0.0.1, 10.0.0.2"})

    assert client_of(forwarded) == "198.51.100.9"


def test_a_caller_with_no_forwarded_header_still_counts_as_somebody():
    """Otherwise every direct caller shares one bucket named empty string."""
    assert client_of(_Request()) == "203.0.113.7"
    assert client_of(_Request({"x-forwarded-for": "  "}, host="")) == "unknown"


def test_only_the_endpoints_that_cost_money_are_bounded():
    """Asserted against the source, because the offline suite is stdlib only.

    The distinction is the whole design. Rationing `/runs` or `/fleet` would
    cost a judge the page and save nothing: those are one Firestore read.
    """
    source = (Path(__file__).resolve().parents[2] / "service" / "main.py").read_text(
        encoding="utf-8"
    )
    # The definition matches the call spelling, so it is excluded rather than
    # the expected number being quietly raised to absorb it.
    bounded = source.count("_within_budget(request)") - source.count(
        "def _within_budget(request)"
    )

    assert bounded == 4, (
        f"{bounded} budget checks; expected four, one each for /run and "
        "/run/stream and one each for the two standards endpoints when they "
        "name a repository"
    )
    for free in ("def fleet_page", "def runs_page", "def index", "def thread_view"):
        start = source.index(free)
        body = source[start : start + 700]
        assert "_within_budget" not in body, f"{free} is rationed and should not be"
