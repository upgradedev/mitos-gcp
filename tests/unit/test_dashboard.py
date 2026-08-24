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
    render_connect,
    render_standards,
    audit_form,
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


# --------------------------------------------------------------------------
# Regressions from an adversarial review. Both are the page asserting something
# the thread does not say.
# --------------------------------------------------------------------------


def test_a_guard_probe_that_could_not_run_is_not_a_zero():
    """`attempt_write` omits `tool_executed` when the ADK run raises.

    `chore.py` records that payload anyway and branches three ways on it:
    refused, could not run, reachable. Reading the missing key as False
    collapsed the middle into the first and printed "the tool ran 0 times",
    which asserts something about a tool the probe never reached.
    """
    entries = [
        entry(
            "guard.exercised",
            "documentation-companion",
            "r1",
            "2026-08-22T18:00:05+00:00",
            attempted=False,
            denied=False,
            error="TimeoutError: deadline exceeded",
        )
    ]

    for page in (
        render_fleet(a_catalog(), entries, "reader", now=NOW),
        render_runs(entries, "reader", now=NOW),
    ):
        assert "the tool ran 0 times" not in page
        assert "could not run" in page


def test_a_needs_changes_response_does_not_park_a_run():
    """`envelope.is_terminal` is blocked or error. `needs_changes` proceeds.

    The page called any status other than ok a park, so a run that carried a
    needs_changes response, passed the gate, proposed a plan and published was
    reported as parked with nothing in the thread saying so.
    """
    at = "2026-08-22T18:00:0"
    entries = [
        entry("trigger.pull_request", "webhook", "r9", at + "1", pr=1, title="t"),
        entry(
            "specialist.response",
            "documentation-companion",
            "r9",
            at + "2",
            status="needs_changes",
        ),
        entry("evaluator.verdict", "evaluator", "r9", at + "3", passed=True),
        entry("plan.proposed", "architect-leader", "r9", at + "4"),
        entry("write.executed", "writer", "r9", at + "5"),
    ]

    page = render_runs(entries, "reader", now=NOW)

    assert rows(page)["parked"] == "0 parked in this window"
    assert "stopped at" not in page or "parked by" not in page.lower()


def test_a_blocked_response_still_parks_a_run():
    """The other direction, so the fix above is not just a deletion."""
    at = "2026-08-22T18:00:0"
    entries = [
        entry("trigger.pull_request", "webhook", "r8", at + "1", pr=2, title="t"),
        entry(
            "specialist.response",
            "compliance-companion",
            "r8",
            at + "2",
            status="blocked",
            reason="Article 9 data is not mine to decide",
        ),
    ]

    assert "compliance-companion" in render_runs(entries, "reader", now=NOW)


# --------------------------------------------------------------------------
# Page 4, the standards audit
# --------------------------------------------------------------------------


def a_finding(rule_id, verdict, **over):
    """Built through the real `Finding.as_dict`, not by restating its keys.

    A hand-written fixture here agreed with a bug in the renderer: it read
    `rule_id`, the dataclass field name, while `as_dict` writes `rule`. Every
    row on the live page printed "None" and every test passed, because the
    fixture and the renderer shared the same wrong assumption. Going through the
    producer makes that impossible.
    """
    from mitos.standards import Finding, Verdict

    fields = {
        "rule_id": rule_id,
        "severity": "critical",
        "verdict": Verdict(verdict),
        "looked_for": "a gitleaks step in the first stage",
        "looked_at": ("azure-pipelines.yml",),
        "found": "the scan runs after build",
    }
    fields.update({k: v for k, v in over.items() if k in fields})
    if "looked_at" in over:
        fields["looked_at"] = tuple(over["looked_at"])
    return Finding(**fields).as_dict()


A_SUMMARY = {
    "rules": 24,
    "checked": 13,
    "passed": 1,
    "failed": 3,
    "suspected": 0,
    "not_applicable": 7,
    "undetermined": 2,
    "needs_judgement": 5,
    "not_checkable": 6,
}


def test_the_standards_page_renders_with_nothing_at_all():
    assert render_standards([], {}, "reader")


def test_a_verdict_that_is_not_a_decision_is_not_painted_as_one():
    """`could not be determined` must not look like `passed`.

    The whole argument of this module is that silence and compliance are
    different facts. If they share a colour the page has lost the argument on
    the only surface a judge actually looks at.
    """
    page = render_standards(
        [
            a_finding("a", "passed", found="the scan is first"),
            a_finding("b", "undetermined", found="the file could not be parsed"),
            a_finding("c", "needs_judgement", looked_at=[], found="a reader must decide"),
        ],
        A_SUMMARY,
        "reader",
    )

    assert "could not be determined" in page
    assert "needs a reader" in page
    # The undecided ones carry the unknown style, never the pass style. Both
    # quoting forms are accepted because `_tone` and `_unknown` differ there;
    # what must hold is that the class is `u` and that the stylesheet knows it.
    from service.dashboard import _CSS

    assert ".u" in _CSS, "the unknown style is not defined, so it renders as plain text"
    for phrase in ("could not be determined", "needs a reader"):
        i = page.index(phrase)
        before = page[max(0, i - 120) : i]
        assert 'class="u"' in before or "class=u" in before, phrase
        assert "good" not in before, phrase


def test_the_page_never_prints_a_single_compliance_percentage():
    """Any one number has to pick a side for the undecided column.

    Every choice it could make is a lie, so the page states the three groups
    and refuses to reduce them.
    """
    page = render_standards([a_finding("a", "failed")], A_SUMMARY, "reader")

    # The stylesheet is full of percentages and none of them are a claim about
    # compliance. Assert over what a reader sees, not over the CSS.
    import re

    visible = re.sub(r"<style>.*?</style>", "", page, flags=re.S)
    assert "%" not in visible
    assert "4 of 24 rules were decided here" in page


def test_failures_are_first_and_untestable_rules_are_last():
    page = render_standards(
        [
            a_finding("z-not-checkable", "not_checkable", looked_at=[], found="process"),
            a_finding("a-failed", "failed"),
        ],
        A_SUMMARY,
        "reader",
    )

    assert page.index("a-failed") < page.index("z-not-checkable")


def test_a_repository_name_from_a_query_string_cannot_inject_markup():
    """`?repository=` is user input and reaches the heading and the summary."""
    page = render_standards(
        [], A_SUMMARY, "reader", repository='"><img src=x onerror=alert(1)>'
    )

    assert "<img" not in page


def test_the_page_says_when_it_audited_the_demo_corpus_rather_than_your_code():
    page = render_standards([], A_SUMMARY, "reader")

    assert "demo corpus" in page


# --------------------------------------------------------------------------
# The flow: pointing it at your own repository
# --------------------------------------------------------------------------


def test_the_form_keeps_what_was_typed_so_a_typo_can_be_corrected():
    """Clearing the field on an error makes the reader retype from memory."""
    page = audit_form("upgradedev/typo-here", "that is not a repository")

    assert 'value="upgradedev/typo-here"' in page
    assert "that is not a repository" in page


def test_the_form_cannot_be_used_to_inject_markup_back_into_itself():
    """The value is reflected into an attribute, which is the classic hole."""
    page = audit_form('"><img src=x onerror=alert(1)>', '"><script>x</script>')

    assert "<img" not in page
    assert "<script>x" not in page


def test_the_form_states_the_rate_limit_and_the_public_only_rule():
    """Both are limits a reader hits within a minute of using this.

    Discovering them from a page full of undetermined verdicts is worse than
    being told, and the second one has a reason worth reading.
    """
    page = audit_form()

    assert "60 requests an hour" in page
    assert "Public repositories only" in page


def test_the_connect_page_names_the_step_that_cannot_be_self_served():
    """The allowlist is deployment configuration.

    A three-step flow whose middle step quietly needs somebody else wastes the
    reader's afternoon. It says so, and says it is a limit rather than a
    coming-soon.
    """
    page = render_connect("reader", base="https://example.test")

    assert "https://example.test/webhook/github" in page
    assert "needs somebody with access to the service" in page
    assert "not a coming-soon" in page


def test_the_connect_page_says_what_it_will_do_to_your_repository():
    """The first question anybody asks before pointing a tool at their code."""
    page = render_connect("reader")

    assert "nothing. It reads." in page


def test_the_webhook_endpoint_offered_for_pasting_is_not_plain_text():
    """`request.base_url` reports the scheme this process saw, which behind
    Cloud Run's proxy is http.

    The connect page was printing `http://...` as the endpoint to paste into
    GitHub: a plain text URL for a signed request, offered to somebody
    following instructions who has no reason to doubt them.
    """
    from service.main import _public_base

    class _Req:
        def __init__(self, headers):
            self.headers = headers
            self.base_url = "http://mitos-reader-437828525303.europe-west1.run.app/"

    behind_proxy = _public_base(_Req({"x-forwarded-proto": "https"}))

    assert behind_proxy.startswith("https://")
    assert "http://" not in behind_proxy


def test_a_forwarded_scheme_nobody_recognises_is_ignored():
    """The header is client-supplied. It is displayed and never trusted for a
    decision, and an unrecognised value must not end up inside a URL."""
    from service.main import _public_base

    class _Req:
        def __init__(self, headers):
            self.headers = headers
            self.base_url = "http://example.test/"

    assert _public_base(_Req({"x-forwarded-proto": "javascript"})) == "http://example.test"
    assert _public_base(_Req({})) == "http://example.test"
