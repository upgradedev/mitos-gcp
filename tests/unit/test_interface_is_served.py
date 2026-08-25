"""The interface is in the image, and the policy allows exactly it.

Asserted over the source, because the offline suite is standard library only and
importing `service.main` pulls in FastAPI. So these are checks on wiring rather
than on behaviour, and the limit is worth naming: they cannot tell you the page
renders. `deployed.yml` does that against the running service, by fetching / and
then fetching the bundle that document names.

What they do catch is the class of mistake this change introduces. Five pages
were replaced by one application, three URLs became redirects, and a content
policy that refused everything had to be widened for the first time. Each of
those is a line somebody can quietly undo, and each one here says which.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "service" / "main.py").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def _policy() -> str:
    """The content policy as one string, taken from the source.

    Read out of `SECURITY_HEADERS` rather than retyped here, so this file
    cannot agree with a policy the service does not send.
    """
    start = MAIN.index('"Content-Security-Policy"')
    return MAIN[start : MAIN.index("X-Content-Type-Options", start)]


def test_the_application_is_served_at_the_root_and_its_assets_are_mounted():
    assert 'app.mount(\n    "/assets"' in MAIN, "the bundle has nowhere to be served from"
    assert 'WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"' in MAIN, (
        "the build directory is resolved from somewhere other than this file, "
        "so it depends on the working directory uvicorn was started in"
    )


def test_the_root_route_exists_whether_or_not_the_interface_was_built():
    """`openapi.yaml` is generated from these routes and CI fails on drift.

    CI checks out a tree with no `web/dist` in it, because the build happens
    inside the image. A route registered only when that directory exists would
    make the committed document depend on which machine generated it.
    """
    assert "check_dir=False" in MAIN, (
        "StaticFiles raises at construction on a missing directory, which would "
        "take every JSON endpoint down with it in a source checkout"
    )
    assert "NOT_BUILT" in MAIN and "status_code=503" in MAIN, (
        "a missing build should answer 503 and say so, not 200 with an apology"
    )


def test_there_is_no_catch_all_that_answers_for_paths_that_do_not_exist():
    """The usual single-page fallback returns index.html for anything unmatched.

    This application routes on the fragment, which the server never sees, so it
    needs no fallback, and having one would make this service answer 200 for
    URLs it does not have.
    """
    assert "html=True" not in MAIN
    assert '"/{full_path:path}"' not in MAIN


def test_the_policy_allows_the_bundle_and_the_fetches_it_makes():
    policy = _policy()
    for directive in ("script-src 'self'", "style-src 'self'", "connect-src 'self'"):
        assert directive in policy, f"the policy does not allow {directive}"


def test_the_policy_still_refuses_what_it_refused_before():
    """The widening is three directives wide and no wider.

    `connect-src` is the one that reads as generous and is not: without it the
    directive falls back to `default-src 'none'`, and 'none' means no sources
    at all rather than no foreign ones, so the application cannot fetch its own
    /identity.
    """
    policy = _policy()
    for directive in (
        "default-src 'none'",
        "form-action 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ):
        assert directive in policy, f"{directive} was dropped from the policy"
    assert "unsafe-inline" not in policy, "the policy is back to what a scan called MEDIUM"
    assert "unsafe-eval" not in policy
    assert "*" not in policy, "a wildcard source in a policy this narrow is a mistake"
    # The nonce stays for the two pages this service still renders itself.
    assert "'nonce-{nonce}'" in policy


def test_the_pages_that_were_replaced_redirect_rather_than_disappear():
    """These three are in the README, in the recorded demo and in merged pull
    request comments. A 404 tells somebody following one of those that the
    thing is gone rather than that it moved."""
    for path, target in (
        ("/thread/view", "/#/thread"),
        ("/runs", "/#/thread"),
        ("/fleet", "/#/boundary"),
    ):
        assert f'@app.get("{path}"' in MAIN, f"{path} is not served at all"
        assert f'RedirectResponse("{target}"' in MAIN, f"{path} does not lead to {target}"


def test_the_two_pages_the_application_does_not_implement_are_still_rendered():
    """Nothing replaced them, so removing them would be removing function."""
    for path in ("/standards", "/connect"):
        assert f'@app.get("{path}", response_class=HTMLResponse)' in MAIN


def test_the_image_builds_the_interface_in_a_stage_that_does_not_ship():
    stages = [line for line in DOCKERFILE.splitlines() if line.startswith("FROM ")]
    assert len(stages) == 2, f"expected a build stage and a runtime stage, got {stages}"
    assert stages[0].startswith("FROM node:")
    assert stages[1].startswith("FROM python:")
    assert "npm ci" in DOCKERFILE, "npm install would ignore the lockfile"
    assert "COPY --from=interface /web/dist ./web/dist" in DOCKERFILE

    # Instructions only. The comments in that stage explain what is kept out of
    # it by name, and matching those would be matching the explanation rather
    # than the thing explained.
    runtime = "\n".join(
        line
        for line in DOCKERFILE[DOCKERFILE.index(stages[1]) :].splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "npm" not in runtime, "node reached the image that holds the write credential"
    assert "node_modules" not in runtime


def test_the_running_stage_is_the_one_that_carries_the_commit():
    """ARG is per stage. Declared in the build stage only, the running image
    reports `unknown` and the deployed gate refuses it, which is correct and
    also an hour of looking for the wrong thing."""
    runtime = DOCKERFILE[DOCKERFILE.rindex("\nFROM ") :]
    assert "ARG MITOS_BUILD_SHA=unknown" in runtime
    assert "MITOS_BUILD_SHA=${MITOS_BUILD_SHA}" in runtime


def test_the_assets_are_owned_by_the_user_that_serves_them():
    """`chown -R` runs once. Anything copied after it stays owned by root."""
    copied = DOCKERFILE.index("COPY --from=interface")
    chowned = DOCKERFILE.index("chown -R mitos /app")
    assert copied < chowned, "the interface is copied in after the ownership is set"


def test_the_build_context_excludes_the_packages_installed_on_this_machine():
    """rollup and esbuild ship platform-specific binaries. A node_modules built
    on the developer's operating system, copied into a Linux image, does not
    slow the build down, it breaks it."""
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "web/node_modules" in ignored
    assert "web/dist" in ignored, "a stale local build could be served as the image's own"

    # A .gcloudignore replaces .gitignore rather than adding to it, so the same
    # two directories have to be named again or every `gcloud builds submit`
    # uploads them.
    submitted = (ROOT / ".gcloudignore").read_text(encoding="utf-8")
    assert "web/node_modules/" in submitted
    assert "web/dist/" in submitted


def test_nothing_is_inlined_into_the_document_the_policy_would_refuse():
    """An inlined asset becomes a `data:` URI or an inline <style>, and this
    policy refuses both. It is a build setting, so it is checked where it is
    set."""
    vite = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert "assetsInlineLimit: 0" in vite
