"""Terraform declares the secrets the runtime writes to, under the same names.

This is a text comparison of two files and it exists because the two drifted.

`github_app_manifest_callback` stores what GitHub's manifest conversion returns:
a private key, a client secret and a webhook secret, under three ids built from
one prefix. Terraform declared a single secret, under a name nothing reads.

For one deployment that did not matter, because the reader held
`roles/secretmanager.admin` on the whole project and could create anything it
liked. Removing that role — the fix for the reader being able to read the spec
repository deploy key — took the ability away without anyone noticing that the
runtime depended on it.

Nothing would have failed until an App was created for real. By then GitHub has
returned credentials it returns exactly once, the App exists, and the callback
fails while storing them. The recovery is deleting the App and starting again.

Standard library only, like the rest of `tests/unit`: it reads the two files as
text rather than importing either. `service.main` pulls in httpx and a guard in
this directory enforces that no unit test does.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVICE = (REPO / "service" / "main.py").read_text(encoding="utf-8")
TERRAFORM = (REPO / "infra" / "main.tf").read_text(encoding="utf-8")

# The comments in that file quote the role that caused the incident, at length.
# An assertion that the role is absent has to read the configuration, not the
# story about it.
TERRAFORM_CODE = "\n".join(
    line for line in TERRAFORM.splitlines() if not line.lstrip().startswith("#")
)


def _suffixes_the_runtime_writes() -> set[str]:
    """Every `{secret_prefix}-...` id built in `service/main.py`."""
    return set(re.findall(r'\{secret_prefix\}-([a-z-]+)"', SERVICE))


def _suffixes_terraform_declares() -> set[str]:
    """The `github_app_credentials` list in `infra/main.tf`."""
    block = re.search(r"github_app_credentials\s*=\s*\[([^\]]*)\]", TERRAFORM)
    assert block, "the github_app_credentials local is gone or was renamed"
    return set(re.findall(r'"([a-z-]+)"', block.group(1)))


def test_terraform_declares_every_secret_the_runtime_writes():
    written = _suffixes_the_runtime_writes()
    declared = _suffixes_terraform_declares()

    assert written, "no secret ids were found in service/main.py, so this asserts nothing"
    assert written == declared, (
        f"the runtime writes {sorted(written)} and Terraform declares "
        f"{sorted(declared)}. A secret the runtime writes to and Terraform does "
        f"not declare cannot be created at runtime any more, because the reader "
        f"is scoped to named secrets rather than holding admin on the project."
    )


def test_the_prefix_is_built_the_same_way_in_both_files():
    """`mitos-{stage}-github-app` on one side, `mitos-${stage}-github-app` on the
    other. Same string, two languages, and nothing else checks that."""
    python_prefix = re.search(
        r'secret_prefix = f"mitos-\{os\.environ\.get\(\'MITOS_STAGE\', \'prod\'\)\}-([a-z-]+)"',
        SERVICE,
    )
    assert python_prefix, "the prefix in service/main.py is no longer built the way this reads it"

    terraform_id = re.search(
        r'secret_id = "mitos-\$\{var\.stage\}-([a-z-]+)-\$\{each\.value\}"', TERRAFORM
    )
    assert terraform_id, "the secret_id in infra/main.tf is no longer built the way this reads it"

    assert python_prefix.group(1) == terraform_id.group(1)


def test_the_reader_is_granted_both_roles_on_each_of_them():
    """`secretVersionAdder` cannot read and `secretAccessor` cannot write, so
    the flow needs both, and needs them on every one of the three."""
    for role in ("secretVersionAdder", "secretAccessor"):
        binding = re.search(
            r'for_each\s*=\s*google_secret_manager_secret\.github_app\s*\n'
            r'\s*secret_id\s*=\s*each\.value\.id\s*\n'
            r'\s*role\s*=\s*"roles/secretmanager\.' + role + '"',
            TERRAFORM,
        )
        assert binding, f"the reader is not granted {role} across every github_app secret"

    # The role that caused the incident this whole block exists to prevent.
    assert "roles/secretmanager.admin" not in TERRAFORM_CODE
