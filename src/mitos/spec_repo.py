"""The governed write, for real.

This is the only code in the fleet that changes anything outside the provenance
ledger, and it runs in only one of the three services.

The credential is an SSH **deploy key scoped to a single repository**, not a
personal access token. A token would carry the whole account; this carries write
access to `mitos-spec` and nothing else, which is the difference between saying
least privilege and doing it. It lives in Secret Manager and exactly one service
account can read it:

    gcloud secrets get-iam-policy spec-repo-write-token --project mitos-fleet
    -> serviceAccount:mitos-writer@mitos-fleet.iam.gserviceaccount.com

The reader and the evaluator can ask for it and receive PermissionDenied from
Google IAM. That is why the reader cannot perform this write even if every line
of our own policy code were deleted.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - runs git with fixed argv, never a shell
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

DEFAULT_REPO = "git@github.com:upgradedev/mitos-spec.git"
DEFAULT_BRANCH = "main"
# ORG_STANDARDS #9: /{Product}/{Stage}/settings/{Service}/{Key}, flattened with
# "-" the way Secret Manager and Key Vault both require. The stage is a path
# segment rather than baked into the value, and the service is always present,
# so a flat name like "spec-repo-write-token" cannot tell you whose key it is.
SECRET_NAME = os.environ.get(
    "MITOS_WRITE_SECRET",
    "mitos-prod-settings-writer-spec-repo-deploy-key",
)  # nosec B105 - a name, not a secret


class SpecRepo(Protocol):
    """Where an approved plan goes."""

    def publish(self, *, path: str, body: str, message: str, branch: str) -> dict: ...


class NullSpecRepo:
    """The default. Records what would have been written and writes nothing.

    Used by the offline demo and the whole test suite, so a run needs no
    credential and no network. It is deliberately not called 'DryRun': a class
    that quietly does nothing is a bug waiting to be mistaken for success, so
    `published` is False and every caller has to look at it.
    """

    def publish(self, *, path: str, body: str, message: str, branch: str) -> dict:
        return {
            "published": False,
            "reason": "no spec repository configured; nothing was written",
            "path": path,
            "branch": branch,
            "bytes": len(body.encode("utf-8")),
        }


def _run(cmd: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> str:
    proc = subprocess.run(  # nosec B603 - fixed argv, shell=False
        cmd, cwd=cwd, env=env, capture_output=True, text=True
    )
    if proc.returncode != 0:
        # Never echo the environment: it carries the path to the key file.
        raise RuntimeError(
            f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: "
            f"{proc.stderr.strip()[:400]}"
        )
    return proc.stdout


@dataclass
class GitSpecRepo:
    """Pushes a branch to the specification repository over SSH.

    A branch, not a commit to the default branch. The approved plan becomes a
    proposal a human merges, which keeps the human in the loop twice: once on
    the content-addressed plan and once on the merge.
    """

    remote: str = DEFAULT_REPO
    base: str = DEFAULT_BRANCH
    project: Optional[str] = None

    def _deploy_key(self) -> str:
        from google.cloud import secretmanager  # noqa: PLC0415

        project = self.project or os.environ["GOOGLE_CLOUD_PROJECT"]
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{SECRET_NAME}/versions/latest"
        return client.access_secret_version(name=name).payload.data.decode("utf-8")

    def publish(self, *, path: str, body: str, message: str, branch: str) -> dict:
        key = self._deploy_key()
        workdir = Path(tempfile.mkdtemp(prefix="mitos-spec-"))
        keyfile = workdir / "id"
        try:
            keyfile.write_text(
                key if key.endswith("\n") else key + "\n", encoding="utf-8"
            )
            os.chmod(keyfile, 0o600)

            env = dict(os.environ)
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {keyfile} -o IdentitiesOnly=yes "
                f"-o StrictHostKeyChecking=accept-new "
                f"-o UserKnownHostsFile={workdir / 'known_hosts'}"
            )
            env["GIT_TERMINAL_PROMPT"] = "0"

            repo = workdir / "repo"
            _run(
                ["git", "clone", "--depth", "1", "--branch", self.base, self.remote, str(repo)],
                env=env,
            )
            _run(["git", "checkout", "-b", branch], cwd=repo, env=env)

            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

            _run(["git", "config", "user.email", "mitos-writer@mitos-fleet.iam.gserviceaccount.com"], cwd=repo, env=env)
            _run(["git", "config", "user.name", "Mitos writer"], cwd=repo, env=env)
            _run(["git", "add", "--", path], cwd=repo, env=env)
            _run(["git", "commit", "-m", message], cwd=repo, env=env)
            _run(["git", "push", "origin", branch], cwd=repo, env=env)
            sha = _run(["git", "rev-parse", "HEAD"], cwd=repo, env=env).strip()

            owner_repo = self.remote.split(":")[-1].removesuffix(".git")
            return {
                "published": True,
                "path": path,
                "branch": branch,
                "commit": sha,
                "url": f"https://github.com/{owner_repo}/tree/{branch}",
                "compare": f"https://github.com/{owner_repo}/compare/{self.base}...{branch}?expand=1",
            }
        finally:
            # The key never outlives the request.
            shutil.rmtree(workdir, ignore_errors=True)


def build_spec_repo(project: Optional[str] = None) -> SpecRepo:
    """Real repository only when one is named. Default is write nothing."""
    remote = os.environ.get("MITOS_SPEC_REMOTE")
    if not remote:
        return NullSpecRepo()
    return GitSpecRepo(
        remote=remote,
        base=os.environ.get("MITOS_SPEC_BASE", DEFAULT_BRANCH),
        project=project,
    )
