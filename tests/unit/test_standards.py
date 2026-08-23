"""The standards audit, asserted in both directions.

Every deterministic check gets a repository that passes it and a repository that
fails it. A check only ever asserted one way is a check that might be a no-op,
and that has already happened in this project: a bare DROP raised no router
signal at all, every test still passed, and a destructive migration woke nobody.

Two things make the pairs mean something. There is a compliant base repository
whose failure set is asserted to be empty, so a variant that fails for the wrong
reason is visible rather than counted as a success. And each variant asserts the
whole failure set, not just that its own rule is in it, so a check that starts
firing on everything fails here.

The hardest case has its own fixture: a GitHub Actions workflow whose scan job is
written first in the file and whose build job carries no `needs:`. Line order
passes it and a first match on the string passes it. Only reading the dependency
graph fails it, which is what the rule actually asks for.
"""

from __future__ import annotations

import pytest

from mitos.envelope import Status
from mitos.standards import (
    ALL_RULES,
    DETERMINISTIC,
    NEEDS_JUDGEMENT,
    NOT_CHECKABLE,
    Audit,
    Finding,
    Unreadable,
    Verdict,
    YamlSubsetError,
    audit,
    check_repository,
    judgement_queue,
    parse_yaml_subset,
    summarise,
    tighten,
)
from mitos.tools import DictCorpus
from tests.synthetic_secrets import AWS_KEY_ID

# Never committed as a literal, for the reason `tests/synthetic_secrets.py`
# gives: this repository runs gitleaks with no ignore file, and allowlisting a
# pattern to make a scan pass is how secret scanners stop working.
FAKE_CLIENT_SECRET = "Tr0ub4dor" + "&3" + "QzWx" + "9v"


# --------------------------------------------------------------------------
# A repository that complies, twice: once on Azure DevOps, once on Actions
# --------------------------------------------------------------------------

GITLEAKS_URL = (
    "https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/"
    "gitleaks_8.18.4_linux_x64.tar.gz"
)

ADO_PIPELINE = f"""\
trigger:
  branches:
    include:
    - integration

stages:
- stage: SecretScan
  displayName: Secret scan
  jobs:
  - job: gitleaks
    steps:
    - script: |
        curl --fail --silent --location --output gitleaks.tar.gz {GITLEAKS_URL}
        tar -xzf gitleaks.tar.gz gitleaks
        ./gitleaks detect --source . --redact --exit-code 1
      displayName: gitleaks detect, pinned
- stage: Build
  dependsOn: SecretScan
  jobs:
  - job: build
    steps:
    - script: npm run build
- stage: Test
  dependsOn: Build
  jobs:
  - job: verify
    steps:
    - script: npm test
    - script: npm run lint
- stage: Deploy
  dependsOn: Test
  jobs:
  - job: deploy
    steps:
    - script: sam deploy --template-file template.yaml --no-confirm-changeset
"""

ACTIONS_PIPELINE = f"""\
name: CI

on:
  push:
  pull_request:

jobs:
  secret-scan:
    name: Secret scan
    runs-on: ubuntu-24.04
    steps:
    - uses: actions/checkout@v4
    - name: gitleaks detect, pinned
      run: |
        curl --fail --silent --location --output gitleaks.tar.gz {GITLEAKS_URL}
        tar -xzf gitleaks.tar.gz gitleaks
        ./gitleaks detect --source . --redact --exit-code 1

  build:
    needs: secret-scan
    runs-on: ubuntu-24.04
    steps:
    - run: npm run build

  test:
    needs: [build]
    runs-on: ubuntu-24.04
    steps:
    - run: npm test
    - run: npm run lint
"""

CLAUDE_MD = """\
# FrontBox IAM

## Architecture Decision Records

### ADR-001: Routing is derived from the pages directory
**Date:** 2026-02-11 | **Status:** Implemented
**Decision:** routes are generated from the file tree, never registered by hand.
**Reason:** one place to look when a URL is wrong.
**Consequence:** renaming a route is renaming a file, so it shows up in review.

## Sprint Status
| Sprint | Item | State |
|---|---|---|
| 41 | IAM token refresh | done |

## Prod vs Repo Drift
| Area | Prod | Repo | Severity |
|---|---|---|---|
| IAM | v4.2.0 | v4.2.0 | none |

## Security Issues
| Issue | Severity | State |
|---|---|---|
| none open | n/a | n/a |

## Branch Health
| Branch | Ahead | Behind | State |
|---|---|---|---|
| integration | 0 | 0 | healthy |
"""

SAM_TEMPLATE = """\
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31

Globals:
  Function:
    Runtime: nodejs20.x
    Timeout: 10
    Environment:
      Variables:
        AWS_NODEJS_CONNECTION_REUSE_ENABLED: 1

Resources:
  IamFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: src/app.handler
      Environment:
        Variables:
          IAM_CLIENT_SECRET: '{{resolve:ssm:/FrontBox/Prod/settings/Iam/ClientSecret}}'
"""

ENV_EXAMPLE = """\
# Product, stage, service, key. Values here are placeholders.
FRONTBOX__PROD__IAM__SENTRY_ENDPOINT=https://sentry.example.test/hook
FRONTBOX__PROD__IAM__TOKEN_TTL_SECONDS=900
AWS_NODEJS_CONNECTION_REUSE_ENABLED=1
"""

APP_JS = """\
const express = require("express");
const { lifecycle } = require("./middleware/lifecycle");
const users = require("./routes/users");

const app = express();
app.use(lifecycle());
app.use("/users", users);

module.exports = { app };
"""

MIDDLEWARE_JS = """\
const Sentry = require("@sentry/node");

function lifecycle() {
  return (req, res, next) => {
    Sentry.configureScope((scope) => {
      scope.setTag("operationName", req.method + " " + req.path);
    });
    res.on("finish", () => {
      Sentry.setHttpStatus(res.statusCode);
    });
    next();
  };
}

module.exports = { lifecycle };
"""

ROUTES_JS = """\
const { Router } = require("express");
const { pool } = require("../db/pool");

const router = Router();

router.get("/", async (req, res) => {
  res.json(await pool.query("select id, display_name from users"));
});

module.exports = router;
"""

OPENAPI = """\
openapi: 3.0.3
info:
  title: FrontBox IAM
  version: 1.0.0
paths:
  /users:
    get:
      responses:
        '200':
          description: ok
"""

SHARED = {
    "CLAUDE.md": CLAUDE_MD,
    "README.md": "# FrontBox IAM\n\nIdentity and access for FrontBox.\n",
    "openapi.yaml": OPENAPI,
    "template.yaml": SAM_TEMPLATE,
    ".env.example": ENV_EXAMPLE,
    "services/api/package.json": '{"name": "@frontbox/iam-api", "version": "1.4.0"}\n',
    "services/api/README.md": "# IAM API\n\nRun with npm start.\n",
    "services/api/src/app.js": APP_JS,
    "services/api/src/middleware/lifecycle.js": MIDDLEWARE_JS,
    "services/api/src/routes/users.js": ROUTES_JS,
    "services/api/src/db/pool.js": "const pool = makePool();\nmodule.exports = { pool };\n",
}

GOOD_ADO = {**SHARED, "azure-pipelines.yml": ADO_PIPELINE}
GOOD_ACTIONS = {**SHARED, ".github/workflows/ci.yml": ACTIONS_PIPELINE}


def variant(base: dict, changes: dict) -> dict:
    """A copy of a repository with paths replaced, or removed when given None."""
    files = dict(base)
    for path, body in changes.items():
        if body is None:
            files.pop(path, None)
        else:
            files[path] = body
    return files


def run(files: dict) -> Audit:
    return check_repository(DictCorpus(files))


def failed(files: dict) -> set[str]:
    return {f.rule_id for f in run(files).failures()}


def verdict_of(files: dict, rule_id: str) -> Verdict:
    found = run(files).by_id(rule_id)
    assert found is not None, rule_id
    return found.verdict


def found_text(files: dict, rule_id: str) -> str:
    found = run(files).by_id(rule_id)
    assert found is not None, rule_id
    return found.found


# --------------------------------------------------------------------------
# The registry, before anything is checked
# --------------------------------------------------------------------------

TRIAGED = frozenset(
    {
        "secret-scan-first-stage",
        "gitleaks-pinned-hard-gate",
        "secrets-never-committed",
        "secret-key-hierarchical-path",
        "stage-not-baked-into-value",
        "openapi-spec-at-repo-root",
        "openapi-matches-real-routes",
        "adr-section-present",
        "adr-entry-format",
        "adr-minimum-topic-coverage",
        "lifecycle-middleware-both-hooks",
        "no-per-handler-observability",
        "shared-client-connection-reuse",
        "aws-nodejs-connection-reuse-env",
        "readme-per-service-package",
        "readme-required-sections",
        "claude-md-operational-tables",
        "heavy-work-offloaded-to-pipeline",
        "session-rescan-protocol",
        "test-count-baseline",
        "branch-naming-convention",
        "commit-message-format",
        "secure-code-companion-pr-gate",
        "pen-test-cadence-sast-loop",
    }
)


def test_every_triaged_rule_is_in_exactly_one_list():
    """A rule dropped from the registry is a rule silently not audited, and no
    other test in this file would notice."""
    deterministic = {c.rule.id for c in DETERMINISTIC}
    judgement = {r.id for r in NEEDS_JUDGEMENT}
    unknowable = {r.id for r in NOT_CHECKABLE}
    assert deterministic | judgement | unknowable == TRIAGED
    assert not deterministic & judgement
    assert not deterministic & unknowable
    assert not judgement & unknowable
    assert len(ALL_RULES) == len(TRIAGED) == 24


def test_the_audit_reports_on_every_rule_including_the_ones_it_cannot_check():
    result = run(GOOD_ADO)
    assert {f.rule_id for f in result.results} == TRIAGED


# --------------------------------------------------------------------------
# The compliant base
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base", [GOOD_ADO, GOOD_ACTIONS], ids=["ado", "actions"])
def test_a_compliant_repository_fails_nothing(base):
    """The anchor for every variant below. Without this, a variant can pass its
    assertion while failing for a reason that has nothing to do with its rule."""
    assert failed(base) == set(), [f.line() for f in run(base).failures()]


def test_a_compliant_repository_is_not_reported_as_fully_determined():
    """Eleven of the twenty four rules are not decidable here, and the summary
    has to say so out loud rather than leaving a reader with a pass rate."""
    summary = run(GOOD_ADO).summary
    assert summary.failed == 0
    assert summary.needs_judgement == 5
    assert summary.not_checkable == 6
    assert summary.could_not_be_determined >= 11


# --------------------------------------------------------------------------
# Section 3, the secret scan
# --------------------------------------------------------------------------

SCAN_SECOND = ADO_PIPELINE.replace(
    "stages:\n- stage: SecretScan", "stages:\n- stage: Lint\n  jobs:\n"
    "  - job: lint\n    steps:\n    - script: npm run lint\n- stage: SecretScan"
)

ACTIONS_BUILD_IN_PARALLEL = ACTIONS_PIPELINE.replace(
    "  build:\n    needs: secret-scan\n", "  build:\n"
)


def test_a_scan_that_is_not_the_first_stage_fails():
    files = variant(GOOD_ADO, {"azure-pipelines.yml": SCAN_SECOND})
    assert failed(files) == {"secret-scan-first-stage"}
    assert "position 2" in found_text(files, "secret-scan-first-stage")


def test_a_build_running_beside_the_scan_fails_even_though_the_scan_is_written_first():
    """The case the rule warns about. The scan job is the first job in the file
    and the string match passes; the dependency graph is what fails it."""
    files = variant(GOOD_ACTIONS, {".github/workflows/ci.yml": ACTIONS_BUILD_IN_PARALLEL})
    assert failed(files) == {"secret-scan-first-stage"}
    assert "does not depend on" in found_text(files, "secret-scan-first-stage")


def test_a_repository_with_no_pipeline_at_all_is_a_finding_and_not_a_pass():
    files = variant(GOOD_ADO, {"azure-pipelines.yml": None})
    assert failed(files) == {
        "secret-scan-first-stage",
        "heavy-work-offloaded-to-pipeline",
    }
    assert verdict_of(files, "gitleaks-pinned-hard-gate") is Verdict.UNDETERMINED


UNPINNED_SCAN = ADO_PIPELINE.replace(
    "    - script: |\n"
    f"        curl --fail --silent --location --output gitleaks.tar.gz {GITLEAKS_URL}\n"
    "        tar -xzf gitleaks.tar.gz gitleaks\n"
    "        ./gitleaks detect --source . --redact --exit-code 1\n",
    "    - script: gitleaks detect --source . --redact\n"
    "      continueOnError: true\n",
)


def test_an_unpinned_gitleaks_behind_continue_on_error_fails():
    files = variant(GOOD_ADO, {"azure-pipelines.yml": UNPINNED_SCAN})
    assert failed(files) == {"gitleaks-pinned-hard-gate"}
    text = found_text(files, "gitleaks-pinned-hard-gate")
    assert "not pinned to v8.18.4" in text
    assert "continue on error" in text


def test_a_scan_without_an_explicit_exit_code_is_still_a_gate():
    """gitleaks already exits non-zero when it finds something.

    The check used to require an explicit `--exit-code 1` and reported a
    correctly gated repository as non compliant. Demanding a redundant flag is
    how a compliance tool earns the reputation that gets it muted.
    """
    no_flag = ADO_PIPELINE.replace(" --exit-code 1", "")
    assert " --exit-code" not in no_flag, "the fixture no longer exercises this"

    files = variant(GOOD_ADO, {"azure-pipelines.yml": no_flag})
    assert verdict_of(files, "gitleaks-pinned-hard-gate") is Verdict.PASSED


def test_a_scan_that_forces_exit_code_zero_is_not_a_gate():
    """The real defect the previous check was reaching for.

    `--exit-code 0` tells gitleaks to succeed whatever it finds, so the step is
    green with a leak sitting in the diff.
    """
    disarmed = ADO_PIPELINE.replace("--exit-code 1", "--exit-code 0")
    assert "--exit-code 0" in disarmed, "the fixture no longer exercises this"

    files = variant(GOOD_ADO, {"azure-pipelines.yml": disarmed})
    assert failed(files) == {"gitleaks-pinned-hard-gate"}
    assert "--exit-code 0" in found_text(files, "gitleaks-pinned-hard-gate")


def test_the_github_spelling_of_continue_on_error_is_caught():
    """`continueOnError` is Azure and `continue-on-error` is GitHub.

    Only the first was checked, so on a GitHub repository, which is most of
    them, this branch could not fire. A check that cannot fail is not a check.
    """
    forgiving = ADO_PIPELINE.replace(
        "    - script: |\n",
        "    - continue-on-error: true\n      script: |\n",
        1,
    )
    files = variant(GOOD_ADO, {"azure-pipelines.yml": forgiving})
    assert verdict_of(files, "gitleaks-pinned-hard-gate") is Verdict.FAILED
    assert "continue on error" in found_text(files, "gitleaks-pinned-hard-gate")


TERRAFORM_NAMING_A_SECRET = """
resource "google_secret_manager_secret" "spec_repo_key" {
  secret_id = "mitos-prod-settings-writer-spec-repo-deploy-key"
  replication { auto {} }
}
"""

# Assembled so a secret scanner reading this test file sees no live-looking
# value, which is the same discipline tests/synthetic_secrets.py follows.
TERRAFORM_HOLDING_A_SECRET = (
    'resource "aws_db_instance" "main" {\n'
    "  password = " + '"' + "hunter" + "2SuperSecretValue!" + '"\n'
    "}\n"
)


def test_naming_a_secret_is_not_committing_one():
    """`secret_id` is the identifier a value will be stored under.

    The check matched the key on the substring "secret" and reported the name as
    a committed credential, which asks the reader to replace a name with a
    reference to itself. The resource exists precisely because the value is not
    in the file.
    """
    files = variant(GOOD_ADO, {"infra/main.tf": TERRAFORM_NAMING_A_SECRET})
    assert verdict_of(files, "secrets-never-committed") is Verdict.PASSED


def test_narrowing_the_key_heuristic_did_not_narrow_the_gate():
    """The value-shape detector is untouched, and it is the one that matters.

    Excluding reference-shaped key names must not let a real credential past, so
    this asserts the same file layout with an actual value in it still fails.
    """
    files = variant(GOOD_ADO, {"infra/main.tf": TERRAFORM_HOLDING_A_SECRET})
    assert "secrets-never-committed" in failed(files)


def test_a_pinned_gitleaks_hard_gate_passes():
    assert verdict_of(GOOD_ADO, "gitleaks-pinned-hard-gate") is Verdict.PASSED


# --------------------------------------------------------------------------
# Section 9, secrets and keys
# --------------------------------------------------------------------------

COMMITTED_ENV = (
    "FRONTBOX__PROD__IAM__CLIENT_SECRET=" + FAKE_CLIENT_SECRET + "\n"
    "FRONTBOX__PROD__IAM__AWS_ACCESS_KEY_ID=" + AWS_KEY_ID + "\n"
)


def test_a_committed_env_with_real_values_fails_while_the_example_does_not():
    assert verdict_of(GOOD_ADO, "secrets-never-committed") is Verdict.PASSED
    files = variant(GOOD_ADO, {".env": COMMITTED_ENV})
    assert failed(files) == {"secrets-never-committed"}
    assert ".env sets" in found_text(files, "secrets-never-committed")


def test_the_finding_never_reproduces_the_secret_it_found():
    """A compliance report that quotes the credential has published it."""
    files = variant(GOOD_ADO, {".env": COMMITTED_ENV})
    response = audit(DictCorpus(files))
    everything = response.assessment + " ".join(response.findings) + response.reason
    assert FAKE_CLIENT_SECRET not in everything
    assert AWS_KEY_ID not in everything
    assert "redacted" in everything


INLINE_SECRET_TEMPLATE = SAM_TEMPLATE.replace(
    "'{{resolve:ssm:/FrontBox/Prod/settings/Iam/ClientSecret}}'",
    FAKE_CLIENT_SECRET,
)


def test_an_iac_secret_parameter_with_an_inline_literal_fails():
    files = variant(GOOD_ADO, {"template.yaml": INLINE_SECRET_TEMPLATE})
    assert "secrets-never-committed" in failed(files)
    assert "resolve:ssm" in found_text(files, "secrets-never-committed")


def test_a_key_with_a_stage_but_no_service_segment_fails_the_convention():
    files = variant(
        GOOD_ADO,
        {".env.example": ENV_EXAMPLE + "PROD_SENTRY_ENDPOINT=https://x.example.test\n"},
    )
    assert failed(files) == {"secret-key-hierarchical-path"}
    assert "no service segment" in found_text(files, "secret-key-hierarchical-path")


def test_hierarchical_keys_in_every_store_form_pass():
    files = variant(
        GOOD_ADO,
        {
            "appsettings.json": (
                '{"FrontBox": {"Prod": {"Iam": {"TokenAudience": "frontbox-api"}}}}'
            )
        },
    )
    assert verdict_of(files, "secret-key-hierarchical-path") is Verdict.PASSED


def test_a_key_with_no_stage_segment_fails_the_stage_rule():
    """It fails the naming convention too, and by construction it has to: the
    hierarchical form contains the stage segment, so a key that has no stage
    cannot satisfy the whole convention either."""
    files = variant(
        GOOD_ADO, {".env.example": ENV_EXAMPLE + "sentry_endpoint=https://x.test\n"}
    )
    assert failed(files) == {
        "stage-not-baked-into-value",
        "secret-key-hierarchical-path",
    }
    assert "sentry_endpoint" in found_text(files, "stage-not-baked-into-value")


def test_a_stage_baked_into_a_value_inside_a_stage_scoped_file_is_suspected_not_failed():
    files = variant(
        GOOD_ADO,
        {"appsettings.Development.json": '{"ServiceBus": {"Host": "sb-dev.example.test"}}'},
    )
    assert verdict_of(files, "stage-not-baked-into-value") is Verdict.SUSPECTED
    assert "false positive" in found_text(files, "stage-not-baked-into-value")


def test_every_key_carrying_its_stage_passes():
    assert verdict_of(GOOD_ADO, "stage-not-baked-into-value") is Verdict.PASSED


# --------------------------------------------------------------------------
# Section 6, the specification
# --------------------------------------------------------------------------


def test_an_http_service_with_no_root_specification_fails():
    files = variant(GOOD_ADO, {"openapi.yaml": None})
    assert failed(files) == {"openapi-spec-at-repo-root"}
    assert "Express" in found_text(files, "openapi-spec-at-repo-root")


def test_a_service_with_a_root_specification_passes():
    assert verdict_of(GOOD_ADO, "openapi-spec-at-repo-root") is Verdict.PASSED


LIBRARY_REPO = {
    "CLAUDE.md": CLAUDE_MD,
    "README.md": "# tariff-math\n\nPure functions for tariff arithmetic.\n",
    "pyproject.toml": '[project]\nname = "tariff-math"\n',
    "src/tariff_math/__init__.py": "def annualise(x):\n    return x * 12\n",
    ".github/workflows/ci.yml": ACTIONS_PIPELINE,
}


def test_a_library_with_no_http_surface_is_out_of_scope_rather_than_failing():
    """Reporting a worker repository as non compliant with an API rule is the
    kind of noise that gets a compliance tool switched off."""
    assert verdict_of(LIBRARY_REPO, "openapi-spec-at-repo-root") is Verdict.NOT_APPLICABLE
    assert "openapi-spec-at-repo-root" not in failed(LIBRARY_REPO)


# --------------------------------------------------------------------------
# Section 2, the decision record
# --------------------------------------------------------------------------

NO_ADR = CLAUDE_MD.replace("## Architecture Decision Records", "## How we work").replace(
    "### ADR-001: Routing is derived from the pages directory", "### Routing"
)


def test_a_claude_md_with_no_adr_section_fails():
    files = variant(GOOD_ADO, {"CLAUDE.md": NO_ADR})
    assert failed(files) == {"adr-section-present"}
    assert verdict_of(files, "adr-entry-format") is Verdict.UNDETERMINED


def test_a_claude_md_with_an_adr_section_passes():
    assert verdict_of(GOOD_ADO, "adr-section-present") is Verdict.PASSED


def test_an_absent_claude_md_is_the_same_failure_and_takes_the_tables_with_it():
    files = variant(GOOD_ADO, {"CLAUDE.md": None})
    assert failed(files) == {"adr-section-present", "claude-md-operational-tables"}
    assert "no CLAUDE.md" in found_text(files, "adr-section-present")


BAD_ADR = CLAUDE_MD.replace(
    "**Reason:** one place to look when a URL is wrong.\n", ""
).replace("**Status:** Implemented", "**Status:** Accepted")


def test_an_adr_missing_a_field_or_carrying_an_unknown_status_fails():
    files = variant(GOOD_ADO, {"CLAUDE.md": BAD_ADR})
    assert failed(files) == {"adr-entry-format"}
    text = found_text(files, "adr-entry-format")
    assert "missing Reason" in text
    assert "'Accepted'" in text


def test_a_complete_adr_entry_passes():
    assert verdict_of(GOOD_ADO, "adr-entry-format") is Verdict.PASSED


# --------------------------------------------------------------------------
# Section 7, observability
# --------------------------------------------------------------------------

HANDLER_WITH_SENTRY = ROUTES_JS.replace(
    "  res.json(await pool.query",
    '  Sentry.configureScope((s) => s.setTag("route", "users"));\n'
    "  res.json(await pool.query",
)


def test_an_observability_call_inside_a_handler_fails():
    files = variant(GOOD_ADO, {"services/api/src/routes/users.js": HANDLER_WITH_SENTRY})
    assert failed(files) == {"no-per-handler-observability"}
    assert "routes/users.js:" in found_text(files, "no-per-handler-observability")


def test_the_same_call_in_the_registered_middleware_passes():
    """The base repository already calls configureScope and setHttpStatus, once,
    in the middleware. If this ever fails the check has started grepping for the
    SDK rather than for where it is called from."""
    assert verdict_of(GOOD_ADO, "no-per-handler-observability") is Verdict.PASSED


# --------------------------------------------------------------------------
# Section 8, connection reuse
# --------------------------------------------------------------------------

NO_REUSE_TEMPLATE = SAM_TEMPLATE.replace(
    "        AWS_NODEJS_CONNECTION_REUSE_ENABLED: 1\n", "        POWERTOOLS_SERVICE: iam\n"
)


def test_a_node_sam_template_without_the_reuse_switch_fails():
    files = variant(GOOD_ADO, {"template.yaml": NO_REUSE_TEMPLATE})
    assert failed(files) == {"aws-nodejs-connection-reuse-env"}
    assert "no AWS_NODEJS_CONNECTION_REUSE_ENABLED" in found_text(
        files, "aws-nodejs-connection-reuse-env"
    )


def test_a_node_sam_template_with_the_reuse_switch_passes():
    assert verdict_of(GOOD_ADO, "aws-nodejs-connection-reuse-env") is Verdict.PASSED


def test_a_repository_with_no_sam_template_is_out_of_scope_rather_than_failing():
    files = variant(GOOD_ADO, {"template.yaml": None})
    assert (
        verdict_of(files, "aws-nodejs-connection-reuse-env") is Verdict.NOT_APPLICABLE
    )


# --------------------------------------------------------------------------
# Documentation
# --------------------------------------------------------------------------


def test_a_package_directory_with_no_sibling_readme_fails():
    files = variant(GOOD_ADO, {"services/api/README.md": None})
    assert failed(files) == {"readme-per-service-package"}
    assert "services/api" in found_text(files, "readme-per-service-package")


def test_a_package_directory_with_a_readme_beside_it_passes():
    assert verdict_of(GOOD_ADO, "readme-per-service-package") is Verdict.PASSED


NO_BRANCH_HEALTH = CLAUDE_MD.split("## Branch Health")[0]


def test_a_claude_md_missing_one_of_the_four_operational_tables_fails():
    files = variant(GOOD_ADO, {"CLAUDE.md": NO_BRANCH_HEALTH})
    assert failed(files) == {"claude-md-operational-tables"}
    assert "Branch Health is absent" in found_text(files, "claude-md-operational-tables")


def test_the_four_tables_pass_but_the_finding_still_says_what_it_cannot_know():
    """Presence is checkable, currency is not, and a pass that does not say so
    reads as a claim that the drift table is correct."""
    found = run(GOOD_ADO).by_id("claude-md-operational-tables")
    assert found.verdict is Verdict.PASSED
    assert "currency is not" in found.limitation
    assert found.needs_attention, "a pass carrying a limitation must still surface"


def test_the_tables_are_not_demanded_of_a_repository_with_no_sprints():
    assert (
        verdict_of(GOOD_ACTIONS, "claude-md-operational-tables")
        is Verdict.NOT_APPLICABLE
    )


NO_LINT = ADO_PIPELINE.replace("    - script: npm run lint\n", "")


def test_a_pipeline_with_no_lint_or_coverage_step_fails():
    files = variant(GOOD_ADO, {"azure-pipelines.yml": NO_LINT})
    assert failed(files) == {"heavy-work-offloaded-to-pipeline"}
    assert "no lint or coverage step" in found_text(
        files, "heavy-work-offloaded-to-pipeline"
    )


def test_a_loose_deploy_script_no_pipeline_calls_fails():
    files = variant(GOOD_ADO, {"scripts/release.sh": "#!/bin/sh\naws lambda update\n"})
    files = variant(files, {"scripts/deploy-prod.sh": "#!/bin/sh\nsam deploy\n"})
    assert failed(files) == {"heavy-work-offloaded-to-pipeline"}
    assert "no pipeline definition calls it" in found_text(
        files, "heavy-work-offloaded-to-pipeline"
    )


def test_a_pipeline_carrying_build_test_lint_and_an_iac_deploy_passes():
    assert verdict_of(GOOD_ADO, "heavy-work-offloaded-to-pipeline") is Verdict.PASSED


# --------------------------------------------------------------------------
# What happens when the repository will not answer
# --------------------------------------------------------------------------


class _RefusingCorpus:
    """A corpus that lists a file and then will not hand it over."""

    def __init__(self, files, refuse):
        self._files = dict(files)
        self._refuse = refuse

    def paths(self):
        return sorted(self._files)

    def read(self, path):
        if path == self._refuse:
            raise OSError("the object store returned 503")
        return self._files[path]


def test_a_file_that_cannot_be_read_is_undetermined_and_never_a_pass():
    """The failure this module is built to prevent. An unreadable pipeline that
    read as an empty string would pass every negative check in it."""
    corpus = _RefusingCorpus(GOOD_ADO, "azure-pipelines.yml")
    result = check_repository(corpus)
    assert verdict_of(GOOD_ADO, "secret-scan-first-stage") is Verdict.PASSED
    assert result.by_id("secret-scan-first-stage").verdict is Verdict.UNDETERMINED
    assert "503" in result.by_id("secret-scan-first-stage").found
    assert result.summary.passed < run(GOOD_ADO).summary.passed


def test_an_unreadable_file_raises_rather_than_reading_as_empty():
    from mitos.standards import _File

    handle = _File("azure-pipelines.yml", None, error="the object store said no")
    assert handle.readable is False
    with pytest.raises(Unreadable) as exc:
        handle.text  # noqa: B018
    assert "azure-pipelines.yml" in str(exc.value)


def test_a_spent_read_budget_produces_undetermined_rather_than_silence():
    from mitos.standards import _Reader, _run

    reader = _Reader(DictCorpus(GOOD_ADO), budget=0)
    check = next(c for c in DETERMINISTIC if c.rule.id == "secret-scan-first-stage")
    result = _run(check, reader)
    assert result.verdict is Verdict.UNDETERMINED
    assert "budget" in result.found


ANCHORED_PIPELINE = """\
stages:
- stage: SecretScan
  jobs: &scanjobs
  - job: gitleaks
    steps:
    - script: gitleaks detect --exit-code 1
- stage: Build
  jobs: *scanjobs
"""


def test_yaml_the_reader_does_not_model_is_undetermined_rather_than_guessed():
    files = variant(GOOD_ADO, {"azure-pipelines.yml": ANCHORED_PIPELINE})
    assert verdict_of(files, "secret-scan-first-stage") is Verdict.UNDETERMINED
    assert "anchors" in found_text(files, "secret-scan-first-stage")


def test_an_unreadable_repository_blocks_and_a_readable_one_does_not():
    class _Dark:
        def paths(self):
            raise ConnectionError("no route to host")

        def read(self, path):
            raise ConnectionError("no route to host")

    blocked = audit(_Dark())
    assert blocked.status is Status.BLOCKED
    assert "could not be listed" in blocked.reason
    assert audit(DictCorpus(GOOD_ADO)).status is Status.OK


def test_an_empty_listing_blocks_rather_than_reporting_a_clean_repository():
    response = audit(DictCorpus({}))
    assert response.status is Status.BLOCKED
    assert "not a compliant repository" in response.reason


# --------------------------------------------------------------------------
# The counting, which is the part a reader actually looks at
# --------------------------------------------------------------------------


def test_the_counts_add_up_to_the_rule_set():
    summary = run(GOOD_ADO).summary
    assert (
        summary.passed
        + summary.failed
        + summary.suspected
        + summary.not_applicable
        + summary.undetermined
        == summary.checked
    )
    assert summary.checked + summary.needs_judgement + summary.not_checkable == (
        summary.rules
    )
    assert summary.rules == len(TRIAGED)


def test_nothing_undetermined_is_counted_as_a_pass():
    before = run(GOOD_ADO).summary
    after = check_repository(_RefusingCorpus(GOOD_ADO, "CLAUDE.md")).summary
    assert after.undetermined > before.undetermined
    assert after.passed + after.failed < before.passed + before.failed
    assert after.rules == before.rules


def test_the_summary_line_names_what_could_not_be_determined():
    line = run(GOOD_ADO).summary.one_line()
    assert "could not be determined" in line
    assert "leave no trace in a repository" in line


def test_summarise_counts_by_verdict_and_not_by_subtraction():
    results = [
        Finding("a", "high", Verdict.PASSED, "x", ("p",), "y"),
        Finding("b", "high", Verdict.UNDETERMINED, "x", ("p",), "y"),
        Finding("c", "high", Verdict.NOT_CHECKABLE, "x", (), "y"),
    ]
    summary = summarise(results)
    assert (summary.rules, summary.checked, summary.passed) == (3, 2, 1)
    assert summary.could_not_be_determined == 2


# --------------------------------------------------------------------------
# The finding contract
# --------------------------------------------------------------------------


def test_a_finding_that_does_not_say_what_it_looked_at_is_rejected():
    with pytest.raises(ValueError) as exc:
        Finding("secrets-never-committed", "critical", Verdict.FAILED, "x", (), "y")
    assert "looked at" in str(exc.value)


@pytest.mark.parametrize(
    "looked_for,found", [("", "something"), ("something", "  ")], ids=["for", "found"]
)
def test_a_finding_must_say_what_it_looked_for_and_what_it_found(looked_for, found):
    with pytest.raises(ValueError):
        Finding("r", "high", Verdict.FAILED, looked_for, ("p",), found)


def test_every_finding_the_audit_produces_names_its_rule_evidence_and_outcome():
    for finding in run(GOOD_ADO).results:
        assert finding.rule_id in TRIAGED
        assert finding.looked_for.strip()
        assert finding.found.strip()
        if finding.verdict not in (Verdict.NEEDS_JUDGEMENT, Verdict.NOT_CHECKABLE):
            assert finding.looked_at, finding.rule_id
        assert finding.rule_id in finding.line()


# --------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------


def test_a_failing_repository_needs_changes_and_says_which_rules():
    response = audit(DictCorpus(variant(GOOD_ADO, {"openapi.yaml": None})))
    assert response.status is Status.NEEDS_CHANGES
    assert "openapi-spec-at-repo-root" in response.reason
    assert any("openapi-spec-at-repo-root" in f for f in response.findings)


def test_failing_every_rule_is_not_blocked_because_blocked_means_unreadable():
    """A repository that fails everything is perfectly legible. Reserving
    blocked for the unreadable case is what keeps it meaning something."""
    response = audit(DictCorpus({"src/main.py": "print(1)\n"}))
    assert response.status is Status.NEEDS_CHANGES
    assert response.findings


def test_confidence_falls_when_the_audit_could_not_settle_what_it_checked():
    clean = audit(DictCorpus(GOOD_ADO)).confidence
    murky = audit(_RefusingCorpus(GOOD_ADO, "azure-pipelines.yml")).confidence
    assert murky < clean <= 1.0


def test_the_response_carries_the_paths_it_opened():
    response = audit(DictCorpus(GOOD_ADO))
    assert "CLAUDE.md" in response.paths_read
    assert "azure-pipelines.yml" in response.paths_read
    assert response.read_log["reads"] == len(response.paths_read)


# --------------------------------------------------------------------------
# The rules a pattern must not answer
# --------------------------------------------------------------------------


def test_the_judgement_rules_have_no_implementation_here():
    """The point of the split. If one of these ever acquires a check function,
    something has written a regular expression that decides whether an ADR
    genuinely decides a routing strategy."""
    implemented = {c.rule.id for c in DETERMINISTIC}
    for rule in NEEDS_JUDGEMENT:
        assert rule.id not in implemented


def test_the_judgement_queue_hands_over_the_five_rules_with_somewhere_to_look():
    queue = judgement_queue(DictCorpus(GOOD_ADO))
    assert [item["rule"] for item in queue] == [r.id for r in NEEDS_JUDGEMENT]
    for item in queue:
        assert item["why_a_reader"].strip()
        assert "cannot return a pass" in item["you_may_only_tighten"]
    spec = next(i for i in queue if i["rule"] == "openapi-matches-real-routes")
    assert "openapi.yaml" in spec["candidate_paths"]


def test_a_deferred_rule_starts_as_needs_judgement_and_is_never_a_pass():
    for rule in NEEDS_JUDGEMENT:
        assert verdict_of(GOOD_ADO, rule.id) is Verdict.NEEDS_JUDGEMENT
    for rule in NOT_CHECKABLE:
        assert verdict_of(GOOD_ADO, rule.id) is Verdict.NOT_CHECKABLE


def test_a_reader_can_add_a_failure():
    result = run(GOOD_ADO)
    after = tighten(
        result,
        [
            {
                "rule": "openapi-matches-real-routes",
                "verdict": "failed",
                "found": "GET /users/{id} is in the spec and no route serves it",
                "looked_at": ["openapi.yaml", "services/api/src/routes/users.js"],
            }
        ],
    )
    finding = after.by_id("openapi-matches-real-routes")
    assert finding.verdict is Verdict.FAILED
    assert "GET /users/{id}" in finding.found
    assert after.summary.failed == result.summary.failed + 1


@pytest.mark.parametrize(
    "judgement",
    [
        {"rule": "openapi-matches-real-routes", "verdict": "passed", "found": "fine"},
        {"rule": "openapi-matches-real-routes", "verdict": "ok", "found": "fine"},
        {"rule": "secret-scan-first-stage", "verdict": "passed", "found": "fine"},
        {"rule": "adr-section-present", "verdict": "passed", "found": "fine"},
        {"rule": "not-a-rule", "verdict": "failed", "found": "invented"},
    ],
    ids=["passed", "ok", "deterministic", "already-passed", "unknown"],
)
def test_a_reader_can_never_turn_anything_into_a_pass(judgement):
    result = run(GOOD_ADO)
    after = tighten(result, [judgement])
    assert after.summary.as_dict() == result.summary.as_dict()
    assert [f.as_dict() for f in after.results] == [
        f.as_dict() for f in result.results
    ]


def test_tighten_has_no_branch_that_produces_a_pass():
    """A structural check on the source, so a refactor that adds a way to accept
    a pass fails here rather than in production. Same artifact, same reason, as
    the evaluator critic's invariant test."""
    import inspect

    from mitos import standards

    body = inspect.getsource(standards.tighten).split('"""')[-1]
    for forbidden in ("PASSED", "NOT_APPLICABLE", ".remove(", "del "):
        assert forbidden not in body, (
            f"tighten now contains {forbidden!r}; a reader may have gained the "
            f"ability to clear a rule this module said it could not decide"
        )
    assert "_READER_VERDICTS" in body
    assert set(standards._READER_VERDICTS.values()) == {
        Verdict.FAILED,
        Verdict.SUSPECTED,
    }


# --------------------------------------------------------------------------
# The YAML subset, since the stage graph rests on it
# --------------------------------------------------------------------------


def test_the_reader_understands_the_shapes_a_pipeline_is_written_in():
    doc = parse_yaml_subset(ADO_PIPELINE)
    assert doc["trigger"]["branches"]["include"] == ["integration"]
    stages = doc["stages"]
    assert [s["stage"] for s in stages] == ["SecretScan", "Build", "Test", "Deploy"]
    script = stages[0]["jobs"][0]["steps"][0]["script"]
    assert script.startswith("curl ")
    assert "--exit-code 1" in script
    assert stages[1]["dependsOn"] == "SecretScan"


def test_the_reader_understands_flow_sequences_and_quoted_keys():
    doc = parse_yaml_subset(ACTIONS_PIPELINE)
    assert doc["jobs"]["test"]["needs"] == ["build"]
    assert doc["jobs"]["build"]["needs"] == "secret-scan"
    assert doc["jobs"]["secret-scan"]["steps"][0]["uses"] == "actions/checkout@v4"


def test_scalars_are_left_as_text_rather_than_coerced():
    doc = parse_yaml_subset("Globals:\n  Function:\n    Reuse: 1\n    On: true\n")
    assert doc["Globals"]["Function"] == {"Reuse": "1", "On": "true"}


@pytest.mark.parametrize(
    "text",
    [
        "a: &base\n  b: 1\nc: *base\n",
        "defaults: &d\n  x: 1\nuse:\n  <<: *d\n",
        "a: 1\n---\nb: 2\n",
        "a:\n\tb: 1\n",
    ],
    ids=["anchor", "merge", "multidoc", "tabs"],
)
def test_the_reader_refuses_what_it_does_not_model(text):
    with pytest.raises(YamlSubsetError):
        parse_yaml_subset(text)


# --------------------------------------------------------------------------
# Regressions from an adversarial review. Each of these passed silently before
# the fix, which is why they are here as inputs rather than as comments.
# --------------------------------------------------------------------------


ANCHORED_INLINE_SECRET = "Defaults: &d\n  Timeout: 10\n" + INLINE_SECRET_TEMPLATE


def test_an_anchor_cannot_turn_a_committed_secret_into_a_pass():
    """The worst outcome this module can produce, reached by two lines of YAML.

    `_template_settings` fell back to the env-file regex when the subset parser
    refused a template. That regex finds nothing in YAML, so an unparseable
    template yielded an empty key list and the check read that as clean. The
    literal credential was still sitting in the file.
    """
    assert verdict_of(
        variant(GOOD_ADO, {"template.yaml": INLINE_SECRET_TEMPLATE}),
        "secrets-never-committed",
    ) is Verdict.FAILED

    verdict = verdict_of(
        variant(GOOD_ADO, {"template.yaml": ANCHORED_INLINE_SECRET}),
        "secrets-never-committed",
    )
    assert verdict is Verdict.UNDETERMINED, (
        "an anchor made the parser refuse the file and the check reported a "
        "pass over a template it never read"
    )


ALIAS_IN_FLOW = ACTIONS_PIPELINE.replace(
    "  build:\n    needs: secret-scan\n",
    "  build:\n    needs: [&scan secret-scan]\n",
    1,
)


def test_an_alias_inside_a_flow_sequence_is_refused_rather_than_misread():
    """A misparse that produces a confident false finding is worse than a refusal.

    `needs: *scan` was refused and `needs: [*scan]` was not, so the alias
    survived as the literal string, the dependency graph came out wrong, and the
    critical rule reported that jobs which do depend on the scan do not.
    """
    files = variant(GOOD_ACTIONS, {".github/workflows/ci.yml": ALIAS_IN_FLOW})

    assert "secret-scan-first-stage" not in failed(files), (
        "a job that does depend on the scan was reported as not depending on it"
    )


def test_a_latest_runner_does_not_count_as_a_test_step():
    """`test` matched inside `ubuntu-latest`, so on any `-latest` runner, which
    is the common case, the missing-test branch could not fire. Every fixture in
    this file pinned `ubuntu-24.04`, so nothing caught it."""
    bare = (
        "name: CI\non:\n  push:\njobs:\n  secret-scan:\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        "    - run: ./gitleaks detect --source . --redact\n"
    )
    files = variant(GOOD_ADO, {"azure-pipelines.yml": None, ".github/workflows/ci.yml": bare})

    assert "no test step" in found_text(files, "heavy-work-offloaded-to-pipeline")


def test_an_action_named_deploy_is_not_a_deploy_step():
    """The same substring bug reading the other way.

    `deploy` matched inside `google-github-actions/deploy-cloudrun@v2`, so a
    repository with no deployment at all was told its pipeline deploys with no
    IaC template to invoke.
    """
    using_action = ACTIONS_PIPELINE + (
        "\n  ship:\n    needs: build\n    runs-on: ubuntu-24.04\n    steps:\n"
        "    - uses: google-github-actions/deploy-cloudrun@v2\n"
    )
    files = variant(
        GOOD_ACTIONS, {".github/workflows/ci.yml": using_action, "template.yaml": None}
    )

    text = found_text(files, "heavy-work-offloaded-to-pipeline")
    assert "no IaC template" not in text


TERRAFORM_REFERENCE = (
    'resource "google_sql_user" "app" {\n'
    "  password = var.db_password\n"
    "}\n"
)


def test_a_terraform_variable_reference_is_not_a_committed_secret():
    """`var.db_password` is the correct pattern and holds nothing.

    Reporting it at critical severity asks the reader to replace a correct
    reference with a different cloud's syntax. Same class as the `secret_id`
    false positive, one provider over.
    """
    files = variant(GOOD_ADO, {"infra/main.tf": TERRAFORM_REFERENCE})

    assert "secrets-never-committed" not in failed(files)


SCAN_NAMED_FOR_ITS_TOOL = ACTIONS_PIPELINE.replace(
    "  secret-scan:\n    name: Secret scan\n", "  gitleaks:\n", 1
).replace("needs: secret-scan", "needs: gitleaks")


def test_a_scan_job_named_after_its_tool_is_still_a_scan():
    """The job id is not part of the standard for GitHub Actions.

    Requiring the literal name `SecretScan` produced the sentence "no secret
    scan job in the same workflow" about a workflow whose first job runs
    gitleaks and gates the rest. That sentence was false.
    """
    files = variant(GOOD_ACTIONS, {".github/workflows/ci.yml": SCAN_NAMED_FOR_ITS_TOOL})

    assert "secret-scan-first-stage" not in failed(files)
    assert verdict_of(files, "gitleaks-pinned-hard-gate") is not Verdict.UNDETERMINED
