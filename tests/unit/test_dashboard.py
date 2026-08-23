"""The dashboard, tested as three pure functions.

Three things have to hold for each page, and they are the three ways a rendered
page lies to an operator.

It has to render with nothing. Every panel has an empty case, and a page that
raises on an empty thread is a page nobody sees on the day the ledger is new.

It has to escape. A repository name and a pull request title arrive from
whoever opened the pull request, travel through the thread wrapped in `Tainted`,
and land in these pages. The assertions here are two sided on purpose: checking
only that `<script>` is absent passes just as well when the field was silently
dropped, which is the other way to get this wrong.

And the numbers have to be the numbers. Each case builds its own small thread
and asserts counts derived from it, rather than the counts observed on one
deployment, which are an observation and not an invariant.
"""

from __future__ import annotations

import re

from service.dashboard import (
    NOT_ON_GCP,
    NOT_SEEN,
    NO_PARKED,
    NO_TRIGGER,
    render_fleet,
    render_overview,
    render_runs,
)

NOW = "2026-08-22T18:30:00+00:00"

# Attacker-controlled in production: the repository comes from the delivery and
# the title is typed by whoever opened the pull request.
HOSTILE_REPO = "evil/<script>alert('r')</script>"
HOSTILE_TITLE = 'feat: add <img src=x onerror="alert(1)"> mobileNumber'
HOSTILE_ACTOR = "<b>not-a-companion</b>"

_ROW = re.compile(
    r"<div class=r><div class=l>(?P<label>.*?)</div><div class=v>(?P<value>.*?)</div>"
)


def _text(fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", fragment).strip()


def rows(page: str) -> dict[str, str]:
    """Label to visible text, for the simple single-value rows.

    Rows whose value nests further markup are truncated at the first close tag,
    so this is used only on the flat ones.
    """
    return {
        _text(m.group("label")): _text(m.group("value"))
        for m in _ROW.finditer(page)
    }


def entry(kind, actor, run_id, at, subject="services/customer", **payload):
    return {
        "kind": kind,
        "actor": actor,
        "subject": subject,
        "payload": dict(payload),
        "parent_id": None,
        "run_id": run_id,
        "entry_id": f"{run_id}-{kind}-{at}",
        "recorded_at": at,
    }


def a_thread():
    """One complete run, one parked run carrying hostile text, one run whose
    trigger fell off the front of the window, and both control-plane buckets."""
    return [
        entry("trigger.pull_request", "webhook", "r1", "2026-08-22T18:00:00+00:00",
              pr=4471, title="add preferredLanguage", repository=None),
        entry("fleet.dispatch", "architect-leader", "r1", "2026-08-22T18:00:01+00:00"),
        entry("specialist.response", "compliance-companion", "r1",
              "2026-08-22T18:00:02+00:00", status="ok"),
        entry("specialist.response", "documentation-companion", "r1",
              "2026-08-22T18:00:03+00:00", status="ok"),
        entry("evaluator.verdict", "evaluator-companion", "r1",
              "2026-08-22T18:00:04+00:00", passed=True),
        entry("guard.exercised", "documentation-companion", "r1",
              "2026-08-22T18:00:05+00:00", denied=True, tool_executed=False,
              detail="write_spec_repo is not callable by the reader role"),
        entry("plan.proposed", "documentation-companion", "r1",
              "2026-08-22T18:00:06+00:00", path="docs/specs/customer-record.md",
              findings=["no retention entry", "no lawful basis"]),

        entry("trigger.webhook", "github", "r2", "2026-08-22T18:10:00+00:00",
              subject=f"{HOSTILE_REPO}#7", pr=7, repository=HOSTILE_REPO,
              title=HOSTILE_TITLE),
        entry("specialist.response", "compliance-companion", "r2",
              "2026-08-22T18:10:01+00:00", subject=f"{HOSTILE_REPO}#7",
              status="blocked", reason="GDPR Article 9 data"),
        entry("item.parked", "compliance-companion", "r2",
              "2026-08-22T18:10:02+00:00", subject=f"{HOSTILE_REPO}#7",
              status="blocked", reason="GDPR Article 9 data"),

        entry("specialist.response", "db-architect-leader", "r3",
              "2026-08-22T17:00:00+00:00", status="ok"),
        entry("plan.proposed", "documentation-companion", "r3",
              "2026-08-22T17:00:01+00:00", findings=["schema drift"]),

        entry("finding.escalated", "compliance-companion", "watch",
              "2026-08-22T18:20:00+00:00", reason="the deferral expired"),
        entry("finding.escalated", "compliance-companion", "watch",
              "2026-08-22T18:20:01+00:00", reason="the deferral expired"),
        entry("finding.deferred", "compliance-companion", "seed",
              "2026-08-20T09:00:00+00:00", finding="no retention entry"),
        entry("finding.raised", HOSTILE_ACTOR, "", "2026-08-20T09:00:01+00:00",
              finding="appended by hand"),
    ]


def a_catalog():
    return [
        {"name": "architect-leader", "department": "Architecture", "role": "router",
         "wakes_on": ["*"], "reads": ["pull-request"], "writes": []},
        {"name": "documentation-companion", "department": "Documentation",
         "role": "spec drift", "wakes_on": ["schema-change"],
         "reads": ["spec"], "writes": ["spec-repo"]},
        {"name": "compliance-companion", "department": "Data Protection",
         "role": "personal-data finding", "wakes_on": ["personal-data"],
         "reads": ["model"], "writes": []},
        {"name": "never-woken-companion", "department": "Architecture",
         "role": "unused", "wakes_on": [], "reads": [], "writes": []},
    ]


REFUSAL = (
    "403 Permission 'secretmanager.versions.access' denied for resource "
    "projects/upgradegr-mitos/secrets/<key>/versions/latest"
)


def an_identity(role="reader", running_as="mitos-reader@upgradegr-mitos.iam"):
    return {
        "role": role,
        "running_as": running_as,
        "project": "upgradegr-mitos",
        "may_call_write_tools": {
            "write_spec_repo": role == "writer",
            "open_pull_request": role == "writer",
        },
        "spec_repo_write_credential": {
            "reachable": role == "writer",
            "detail": "PermissionDenied",
            "message": REFUSAL,
        },
        "model": "gemini-3.7-flash",
    }


def a_watch():
    return {
        "subscribed": True,
        "mechanism": "firestore query subscription (on_snapshot), no scheduler",
        "watching": "kind == finding.deferred",
        "wakeups": 3,
        "detail": [
            {"reason": "a deferral expired", "matched": 2,
             "at": "2026-08-22T18:20:00+00:00"},
        ],
    }


# --------------------------------------------------------------------------
# It renders with nothing
# --------------------------------------------------------------------------


def test_every_page_renders_with_empty_data():
    pages = [
        render_overview({}, {}, [], now=NOW),
        render_fleet([], [], "reader", now=NOW),
        render_runs([], "reader", now=NOW),
    ]
    for page in pages:
        assert page.startswith("<!doctype html>")
        assert "<main>" in page


def test_empty_pages_name_the_emptiness_rather_than_leaving_a_blank():
    overview = render_overview({}, {}, [], now=NOW)
    assert "the thread is empty in this window" in overview
    assert "no watch data was passed to this page" in overview

    fleet = render_fleet([], [], "reader", now=NOW)
    assert "the catalog reported no companions" in fleet

    runs = render_runs([], "reader", now=NOW)
    assert "no runs in this window" in runs
    assert "no control-plane entries in this window" in runs


def test_none_is_rendered_as_a_fact_and_never_as_the_word_none():
    """Off Google Cloud there is no metadata server, which is a different thing
    from a blank cell and a very different thing from the string "None"."""
    page = render_overview(
        an_identity(running_as=None), a_watch(), a_thread(), now=NOW
    )
    assert NOT_ON_GCP in page
    assert ">None<" not in page


def test_a_credential_check_with_no_message_says_which_case_it_is():
    identity = an_identity(role="writer")
    identity["spec_repo_write_credential"] = {"reachable": True, "detail": "secret accessed"}
    page = render_overview(identity, a_watch(), [], now=NOW)
    assert "the credential was reachable, so there is no refusal to quote" in page


def test_an_unreadable_timestamp_degrades_to_the_raw_value():
    entries = [entry("fleet.dispatch", "architect-leader", "r1", "not a timestamp")]
    page = render_runs(entries, "reader", now=NOW)
    assert "not a timestamp" in page


# --------------------------------------------------------------------------
# It escapes what came from outside
# --------------------------------------------------------------------------


def test_the_runs_page_escapes_the_repository_the_title_and_the_subject():
    page = render_runs(a_thread(), "reader", total=200, now=NOW)

    assert "&lt;script&gt;" in page
    assert "<script" not in page.lower()
    assert "&lt;img src=x" in page
    assert "<img" not in page
    assert "evil/&lt;script&gt;alert(&#x27;r&#x27;)&lt;/script&gt;#7" in page


def test_the_fleet_page_escapes_an_actor_name_from_the_thread():
    page = render_fleet(a_catalog(), a_thread(), "reader", now=NOW)
    assert "&lt;b&gt;not-a-companion&lt;/b&gt;" in page
    assert "<b>not-a-companion</b>" not in page


def test_the_overview_escapes_the_message_google_returned():
    identity = an_identity()
    identity["spec_repo_write_credential"]["message"] = (
        "denied for <script>alert('x')</script>"
    )
    page = render_overview(identity, a_watch(), a_thread(), now=NOW)
    assert "denied for &lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in page
    assert "<script" not in page.lower()


def test_no_page_carries_a_script_element_at_all():
    """These pages have no behaviour, so the whole class of injection that needs
    a script context is absent by construction rather than by escaping."""
    hostile = a_thread()
    pages = [
        render_overview(an_identity(), a_watch(), hostile, now=NOW),
        render_fleet(a_catalog(), hostile, "reader", now=NOW),
        render_runs(hostile, "reader", now=NOW),
    ]
    for page in pages:
        assert "<script" not in page.lower()
        assert "javascript:" not in page.lower()


# --------------------------------------------------------------------------
# The overview: the privilege boundary and the control plane
# --------------------------------------------------------------------------


def test_the_overview_prints_the_refusal_verbatim():
    """A boolean is this process saying "not allowed". The Secret Manager message
    is Google refusing, outside the process, so it is printed and not summarised.
    """
    page = render_overview(an_identity(), a_watch(), a_thread(), total=200, now=NOW)
    assert (
        "403 Permission &#x27;secretmanager.versions.access&#x27; denied for "
        "resource projects/upgradegr-mitos/secrets/&lt;key&gt;/versions/latest"
    ) in page


def test_the_boundary_is_green_on_the_reader_when_it_cannot_write():
    page = render_overview(an_identity("reader"), a_watch(), [], now=NOW)
    assert rows(page)["may call write_spec_repo"] == "false"
    assert '<span class="good">false</span>' in page
    assert '<span class="bad">' not in page


def test_the_same_boundary_is_green_on_the_writer_when_it_can():
    """The colour tracks whether the boundary is holding, not the boolean. The
    writer is the one service that is supposed to hold the credential."""
    page = render_overview(an_identity("writer"), a_watch(), [], now=NOW)
    assert rows(page)["may call write_spec_repo"] == "true"
    assert '<span class="good">true</span>' in page
    assert '<span class="bad">' not in page


def test_an_unsubscribed_watch_prints_its_own_reason():
    watch = {
        "subscribed": False,
        "reason": "the writer service does not hold the subscription",
    }
    page = render_overview(an_identity("writer"), watch, [], now=NOW)
    assert "the writer service does not hold the subscription" in page


def test_the_open_deferral_set_is_unknown_until_the_endpoint_reports_it():
    page = render_overview(an_identity(), a_watch(), [], now=NOW)
    assert rows(page)["open deferrals"].startswith(
        "this deployment reports wake-ups, not the open set"
    )

    page = render_overview(an_identity(), {**a_watch(), "open": 4}, [], now=NOW)
    assert rows(page)["open deferrals"] == "4"

    page = render_overview(an_identity(), {**a_watch(), "open": []}, [], now=NOW)
    assert rows(page)["open deferrals"] == "0 open in this deployment"


def test_the_window_says_whether_it_is_a_tail():
    entries = a_thread()
    without = render_overview(an_identity(), a_watch(), entries, now=NOW)
    assert f"showing the last {len(entries)} entries; this deployment does not report a total" in without

    with_total = render_overview(
        an_identity(), a_watch(), entries, total=873, now=NOW
    )
    assert f"showing the last {len(entries)} entries of 873" in with_total


def test_the_bounds_are_reported_or_declared_unreadable():
    page = render_overview(an_identity(), a_watch(), [], now=NOW)
    assert "this deployment exposes no config endpoint" in page

    page = render_overview(
        an_identity(), a_watch(), [], now=NOW,
        config={
            "read_scope": ["docs/", "services/", "registers/"],
            "webhook_repositories": ["upgradedev/mitos-spec"],
            "max_bytes_per_read": 8000,
            "max_reads_per_run": 12,
            "webhook_secret_configured": False,
        },
    )
    assert rows(page)["read scope"] == "docs/, services/, registers/"
    assert rows(page)["max bytes per read"] == "8000"
    assert rows(page)["max reads per run"] == "12"
    assert rows(page)["webhook secret configured"] == "false"
    assert "every delivery is refused with 503" in page


def test_the_other_deployments_are_named_as_unknowable_not_absent():
    page = render_overview(an_identity(), a_watch(), [], now=NOW)
    assert "cannot read the evaluator&#x27;s or the writer&#x27;s identity" in page

    page = render_overview(
        an_identity(), a_watch(), [], now=NOW,
        peers=[{"role": "writer", "url": "https://mitos-writer.example.run.app"}],
    )
    assert "https://mitos-writer.example.run.app" in page
    assert "href=\"https://mitos-writer" not in page


# --------------------------------------------------------------------------
# The fleet page: the catalog joined to the thread
# --------------------------------------------------------------------------


def test_the_fleet_page_counts_each_companions_entries():
    page = render_fleet(a_catalog(), a_thread(), "reader", now=NOW)
    assert "compliance-companion" in page
    # One dispatch for the router. Four for documentation: a response, the
    # refused write attempt and two plans. Six for compliance, including the
    # two escalations the subscription produced with no run behind them.
    assert "<span class=badge>1 entries</span>" in page
    assert "<span class=badge>4 entries</span>" in page
    assert "<span class=badge>6 entries</span>" in page
    assert "<span class=badge>0 entries</span>" in page


def test_a_catalogued_companion_with_no_entries_is_not_claimed_to_have_never_run():
    page = render_fleet(a_catalog(), a_thread(), "reader", now=NOW)
    assert NOT_SEEN in page
    assert "never ran" not in page


def test_the_companion_that_declares_a_write_shows_every_attempt_refused():
    page = render_fleet(a_catalog(), a_thread(), "reader", now=NOW)
    assert rows(page)["write attempts"] == "1 of 1 refused, the tool ran 0 times"


def test_a_write_that_actually_executed_is_red():
    entries = [
        entry("guard.exercised", "documentation-companion", "r1",
              "2026-08-22T18:00:05+00:00", denied=False, tool_executed=True),
    ]
    page = render_fleet(a_catalog(), entries, "reader", now=NOW)
    assert rows(page)["write attempts"] == "0 of 1 refused, the tool ran 1 times"
    assert '<span class="bad">0 of 1 refused' in page


def test_actors_that_are_not_catalogued_are_listed_rather_than_dropped():
    page = render_fleet(a_catalog(), a_thread(), "reader", now=NOW)
    assert "webhook" in page
    assert "github" in page
    assert "&lt;b&gt;not-a-companion&lt;/b&gt;" in page


def test_a_catalog_whose_companions_all_ran_says_so():
    entries = [
        entry("fleet.dispatch", "architect-leader", "r1", "2026-08-22T18:00:01+00:00"),
    ]
    page = render_fleet(a_catalog()[:1], entries, "reader", now=NOW)
    assert "every actor in this window is a catalogued companion" in page


# --------------------------------------------------------------------------
# The runs page: what ran, and where it stopped
# --------------------------------------------------------------------------


def test_the_totals_are_the_totals():
    page = render_runs(a_thread(), "reader", total=200, now=NOW)
    counted = rows(page)
    assert counted["dispatches"] == "1"
    assert counted["specialist responses"] == "4"
    assert counted["plans proposed"] == "2"
    assert counted["evaluator verdicts"] == "1 passed of 1"
    assert counted["guard denials"] == "1 of 1 attempts, the tool ran 0 times"
    assert counted["escalations"] == "2"
    assert counted["deferrals"] == "1"


def test_no_write_executed_is_the_approval_holding_and_says_so():
    page = render_runs(a_thread(), "reader", now=NOW)
    assert (
        rows(page)["writes executed"]
        == "0 in this window; every run stopped at the approval"
    )


def test_a_parked_run_is_counted_and_attributed():
    page = render_runs(a_thread(), "reader", now=NOW)
    assert rows(page)["parked"] == "1 parked in this window"
    assert "by compliance-companion" in page


def test_zero_parked_keeps_the_column_and_prints_the_zero():
    """The parking paths exist and have not fired in this deployment. Dropping
    the column would render the same page as a fleet that cannot refuse."""
    clean = [e for e in a_thread() if e["run_id"] != "r2"]
    page = render_runs(clean, "reader", now=NOW)
    assert rows(page)["parked"] == NO_PARKED


def test_a_run_whose_trigger_is_outside_the_window_says_which_it_is():
    page = render_runs(a_thread(), "reader", now=NOW)
    assert NO_TRIGGER in page
    assert "run r3" in page


def test_the_control_plane_run_ids_are_not_rendered_as_runs():
    page = render_runs(a_thread(), "reader", now=NOW)
    assert "unattended wakes from the query subscription" in page
    assert "history seeded so the thread has a past to recall" in page
    assert "run watch" not in page
    assert "run seed" not in page


def test_runs_are_keyed_on_run_id_and_not_on_subject():
    """Both runs below carry the same subject, which the chore path hardcodes.
    Grouping on it would show one run instead of two."""
    entries = [
        entry("trigger.pull_request", "webhook", "a1", "2026-08-22T18:00:00+00:00",
              pr=1),
        entry("trigger.pull_request", "webhook", "b2", "2026-08-22T18:05:00+00:00",
              pr=2),
    ]
    page = render_runs(entries, "reader", now=NOW)
    assert "run a1" in page
    assert "run b2" in page


def test_a_run_with_no_repository_says_it_read_the_demo_corpus():
    page = render_runs(a_thread(), "reader", now=NOW)
    assert "the specialists read the built-in demo corpus" in page


def test_the_newest_run_is_first():
    page = render_runs(a_thread(), "reader", now=NOW)
    assert page.index("run r2") < page.index("run r1") < page.index("run r3")


# --------------------------------------------------------------------------
# House style
# --------------------------------------------------------------------------


def test_no_page_contains_an_em_dash():
    pages = [
        render_overview(an_identity(), a_watch(), a_thread(), now=NOW),
        render_fleet(a_catalog(), a_thread(), "reader", now=NOW),
        render_runs(a_thread(), "reader", now=NOW),
    ]
    for page in pages:
        # Written as escapes so that a grep for the character over the source
        # tree stays empty, which is how the rule is actually checked.
        assert "\u2014" not in page
        assert "\u2013" not in page
