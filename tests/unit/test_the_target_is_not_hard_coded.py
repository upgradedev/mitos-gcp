"""Every run in every repository proposed a write to the same file.

`target` was the literal `docs/specs/customer-record.md`. A Go payments API
whose diff touched `api/payments/handler.go` and `api/payments/schema.sql` was
handed a proposed write to a path that does not exist in it:

    repository       : acme/payments-api
    changed files    : ['api/payments/handler.go', 'api/payments/schema.sql']
    proposed target  : docs/specs/customer-record.md

Anyone who installed the App on their own repository met that on their first
pull request. It is the clearest signal available that a thing is a demo rather
than a product, and it was in the one output the product asks a human to approve.

The target is chosen from evidence now: a document inside the diff first,
because a change that already touches its own paperwork tells you which file to
keep in step; then a document a specialist actually opened. Where neither
exists, the run produces a review plan and proposes nothing, because a
governance tool that always finds a file to change will find one for a change it
did not understand, and the first wrong suggestion is the last one anybody reads.

Standard library only, per `test_offline_suite_stays_offline.py`.
"""

from __future__ import annotations

from mitos.chore import choose_target, looks_like_a_document, run_chore
from mitos.fixtures import PR_4471, PullRequest
from mitos.ledger import InMemoryLedger

FOREIGN = PullRequest(
    number=8001,
    title="Add rate limiting to the payments API",
    author="someone",
    files=[
        {
            "path": "api/payments/handler.go",
            "patch": "@@ +1 @@\n+func RateLimit(r *http.Request) {}\n",
            "status": "modified",
        },
        {
            "path": "api/payments/schema.sql",
            "patch": (
                "@@ +1 @@\n+ALTER TABLE invoice ADD COLUMN payer_email VARCHAR(120);\n"
            ),
            "status": "modified",
        },
    ],
)

WITH_ITS_OWN_DOC = PullRequest(
    number=8002,
    title="Add a retention window to the billing service",
    author="someone",
    files=[
        {
            "path": "services/billing/migrations/V7__retention.sql",
            "patch": "@@ +1 @@\n+ALTER TABLE invoice ADD COLUMN retain_until DATE;\n",
            "status": "modified",
        },
        {
            "path": "docs/specs/billing-invoice.md",
            "patch": "@@ +1 @@\n+The invoice record.\n",
            "status": "modified",
        },
    ],
)


def _run(pr, repository):
    led = InMemoryLedger()
    result = run_chore(
        pr, led, run_id="t", repository=repository, approve=lambda card: False
    )
    return result, [e.kind for e in led.all()], led


# ---------------------------------------------------------------------------
# The chooser
# ---------------------------------------------------------------------------


def test_a_document_in_the_diff_is_the_target():
    """The strongest signal available: the change already touches its paperwork."""
    assert (
        choose_target(WITH_ITS_OWN_DOC, []) == "docs/specs/billing-invoice.md"
    )


def test_a_document_the_specialists_opened_is_the_target():
    """Second best: nobody changed it, but a specialist went and read it."""
    assert (
        choose_target(FOREIGN, ["api/payments/handler.go", "docs/api/payments.md"])
        == "docs/api/payments.md"
    )


def test_nothing_is_chosen_when_nothing_was_seen():
    """The reproduction. This returned the customer-record path before."""
    assert choose_target(FOREIGN, ["api/payments/handler.go"]) is None


def test_the_choice_is_deterministic():
    """Two runs over one change must propose the same file, or the digest a
    human approves is not the digest they would get again."""
    read = ["docs/z.md", "docs/a.md", "docs/m.md"]
    assert choose_target(FOREIGN, read) == choose_target(FOREIGN, list(reversed(read)))


def test_source_files_are_never_mistaken_for_documents():
    for path in (
        "api/payments/handler.go",
        "services/customer/migrations/V1.sql",
        "src/mitos/chore.py",
        "README.md",
    ):
        assert looks_like_a_document(path) is False, path


def test_documents_are_recognised_wherever_a_project_keeps_them():
    for path in (
        "docs/specs/customer-record.md",
        "doc/adr/0001-choice.md",
        "registers/processing.md",
        "policies/retention.rst",
    ):
        assert looks_like_a_document(path) is True, path


# ---------------------------------------------------------------------------
# Through run_chore, which is what the webhook calls
# ---------------------------------------------------------------------------


def test_a_foreign_repository_gets_a_review_plan_and_no_invented_write():
    result, kinds, led = _run(FOREIGN, "acme/payments-api")

    assert result.card is None, (
        f"a write was proposed to {result.card.target_path!r} in a repository "
        f"that has no such file"
    )
    assert "plan.review_only" in kinds
    assert "plan.proposed" not in kinds
    assert result.written is False

    entry = next(e for e in led.all() if e.kind == "plan.review_only")
    assert entry.payload["changed"] == FOREIGN.paths()
    assert entry.payload["next"], "the review plan does not say what to do next"


def test_a_change_that_carries_its_own_document_still_proposes_a_write():
    """The counterweight. A fix that proposed nothing would satisfy the test
    above and remove the product."""
    result, kinds, _ = _run(WITH_ITS_OWN_DOC, "acme/billing")

    assert result.card is not None, "no write was proposed for a change that has a spec"
    assert result.card.target_path == "docs/specs/billing-invoice.md"
    assert "plan.proposed" in kinds


def test_the_demo_fixture_still_proposes_the_file_it_always_did():
    """PR 4471 carries `docs/specs/customer-record.md` in its own diff, so the
    file the recorded demo proposes is chosen on evidence rather than by being
    written into the source. The video keeps working, for a better reason."""
    result, _, _ = _run(PR_4471, "upgradedev/mitos-spec")

    assert result.card is not None
    assert result.card.target_path == "docs/specs/customer-record.md"
    assert "docs/specs/customer-record.md" in PR_4471.paths()


def test_the_guard_is_still_exercised_when_nothing_is_proposed():
    """The interceptor refusing the write tool is a property of the role, not of
    the path, and it is the ADK evidence. Losing it on this branch would trade
    one demo artefact for another."""
    from mitos import gemini

    probe_calls = []

    class _DocAgent:
        def attempt_write(self, path, draft):
            probe_calls.append(path)
            return {"denied": True, "tool_executed": False, "detail": "refused"}

    led = InMemoryLedger()
    run_chore(
        FOREIGN,
        led,
        run_id="probe",
        repository="acme/payments-api",
        doc_agent=_DocAgent(),
        approve=lambda card: False,
    )
    exercised = [e for e in led.all() if e.kind == "guard.exercised"]

    assert exercised, "the guard was not exercised on the review-plan path"
    assert exercised[0].payload["was_a_proposal"] is False, (
        "the probe path is recorded as a proposal, which it is not"
    )
    assert probe_calls == ["api/payments/handler.go"]
