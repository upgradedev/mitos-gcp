"""Credential-shaped strings that are not credentials.

The evaluator's secret detectors need realistic things to find, and the tests
need to hand them realistic things. None of it may be committed as a literal:
the repository runs gitleaks with no ignore file, and adding an allowlist entry
to make a scan pass is how secret scanners quietly stop working.

So every shape here is assembled at import time. One place, because the same
values are wanted by several test modules and a second copy would eventually be
the one that got committed whole.
"""

from __future__ import annotations

from mitos.fixtures import _FAKE_ENDPOINT

# Azure service bus connection string, already assembled by the product fixture.
SERVICE_BUS = _FAKE_ENDPOINT

# AWS access key id shape: the documented example value, split so the literal
# never appears in the tree.
AWS_KEY_ID = "AKIA" + "IOSFODNN" + "7EXAMPLE"

# PEM header shape.
PRIVATE_KEY_HEADER = "-----BEGIN " + "PRIVATE KEY" + "-----"

# Bearer token shape.
BEARER = "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz0123"

# Credentials embedded in a URL.
URL_WITH_PASSWORD = "postgres://" + "user" + ":" + "hunter2" + "@db.internal:5432/app"

# OpenSSH private key header. The default `ssh-keygen` format since OpenSSH 7.8,
# and the shape of the one credential this architecture is built around. The
# detector matched only the RSA and EC spellings until 2026-08-27.
OPENSSH_KEY_HEADER = "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----"

# GitHub tokens, both spellings, because `gh[pousr]_` cannot match
# `github_pat_` and the fine-grained one is what somebody pastes today.
GITHUB_TOKEN = "ghp" + "_" + "A" * 36
GITHUB_FINE_GRAINED_PAT = "github" + "_pat_" + "B" * 24

# Google API key shape: `AIza` and thirty-five more.
GOOGLE_API_KEY = "AIza" + "C" * 35

ALL_SHAPES = [
    SERVICE_BUS,
    AWS_KEY_ID,
    PRIVATE_KEY_HEADER,
    OPENSSH_KEY_HEADER,
    GITHUB_TOKEN,
    GITHUB_FINE_GRAINED_PAT,
    GOOGLE_API_KEY,
    BEARER,
    URL_WITH_PASSWORD,
]
