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


# The check the two above could not be. Both assert that a redirect target
# string appears in `service/main.py`, which is true of a target the interface
# has never heard of.
#
# It was. `RouteId` and `ROUTES` in `web/src/ui/router.ts` listed dashboard,
# pull-requests, repositories, activity and settings. Not `thread`, not
# `boundary`. So `/thread/view`, the one URL the README bolds on its own line
# and the thing the product is named for, redirected to `/#/thread`, the router
# fell through to "dashboard", and a judge following the README read the
# onboarding empty state. `ThreadView.tsx`, 807 lines, was exported from a
# barrel nothing imported and dropped from the bundle by tree shaking.
#
# `judge_uat` reported 47 of 47 green over it, which is the fourth time in this
# repository that a check has passed over the thing it was named after. Asserted
# here rather than in the deployed suite because the deployed suite would have to
# execute the bundle to see it, and this is decidable from two source files.
ROUTER = (ROOT / "web" / "src" / "ui" / "router.ts").read_text(encoding="utf-8")


def _routes_the_interface_knows() -> set[str]:
    """The `ROUTES` array, read from the source of truth for it."""
    body = ROUTER.split("export const ROUTES", 1)[1]
    # After the `=`, not after the declaration. The first `[` in
    # `export const ROUTES: RouteId[] = [` belongs to the TYPE, so slicing from
    # it returns the empty pair in `RouteId[]` and this function quietly
    # reported that the interface knows no routes at all. Which would have made
    # the assertion below pass over everything, in a file whose whole subject is
    # checks that pass over everything.
    body = body.split("=", 1)[1]
    body = body[body.index("[") + 1 : body.index("]")]
    return {piece.strip().strip('"').strip("'") for piece in body.split(",") if piece.strip()}


def _routes_the_service_redirects_to() -> set[str]:
    """Every `/#/x` the service sends a browser to."""
    found = set()
    for chunk in MAIN.split('RedirectResponse("/#/')[1:]:
        found.add(chunk.split('"')[0].split("?")[0].strip("/"))
    for chunk in MAIN.split('RedirectResponse(url="/#')[1:]:
        target = chunk.split('"')[0].lstrip("/")
        found.add(target.split("?")[0].strip("/"))
    return {route for route in found if route}


def test_the_reader_finds_both_lists():
    """A check on the checker. If either parse silently returns nothing, the
    assertion below passes over everything, which is the exact failure this
    file is about."""
    known = _routes_the_interface_knows()
    redirected = _routes_the_service_redirects_to()

    assert "dashboard" in known, f"ROUTES did not parse: {known}"
    assert len(known) >= 5, known
    assert redirected, "no RedirectResponse into the application was found in service/main.py"


def test_every_route_the_service_redirects_to_is_one_the_interface_knows():
    known = _routes_the_interface_knows()
    stranded = sorted(_routes_the_service_redirects_to() - known)

    assert not stranded, (
        f"service/main.py redirects a browser to {stranded}, which "
        f"web/src/ui/router.ts does not list, so the router falls through to "
        f"the dashboard and the page the redirect promised never renders. "
        f"Known routes: {sorted(known)}"
    )


def test_the_thread_is_read_over_the_endpoint_a_stranger_can_read():
    """`/api/workspace/thread` answers 401 to anybody without a session, which
    is every judge following the README. The public `/thread` carries the same
    provenance thread and is what the recorded demo and the deployed checks use.
    """
    client = (ROOT / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    getter = client.split("export const getThread", 1)[1].split(";", 1)[0]

    assert "/api/workspace/thread" not in getter, (
        "the interface reads the thread over the endpoint that requires a "
        "session, so it renders empty for anybody who has not signed in"
    )
    assert "/thread?" in getter, getter


def test_every_route_is_reachable_from_the_navigation():
    """A route nothing links to is a route nobody finds.

    `thread` and `boundary` were absent from `ROUTES` and also from the sidebar,
    so even after the redirect was fixed there would have been no way to reach
    either of them by looking at the product. Asserted over the source rather
    than a screenshot: the sidebar opens only above 1024 pixels, so a headless
    check of the rendered navigation proves nothing about whether the entry
    exists.
    """
    sidebar = (ROOT / "web" / "src" / "shell" / "Sidebar.tsx").read_text(encoding="utf-8")
    linked = {chunk.split('"')[0] for chunk in sidebar.split('{ id: "')[1:]}
    # `settings` has its own pinned entry at the foot of the sidebar rather than
    # a row in ITEMS, which is a layout decision and not an omission.
    linked.add("settings")

    missing = sorted(_routes_the_interface_knows() - linked)

    assert not missing, (
        f"{missing} are routes the interface knows and the navigation does not "
        f"offer, so the only way to reach them is to type the URL"
    )
