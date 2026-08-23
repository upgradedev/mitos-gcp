"""The operator dashboard: what the fleet did, and what it was not allowed to do.

The service already answers four JSON endpoints and renders one page. The JSON is
complete and unreadable at a glance: `/identity` is the privilege boundary,
`/catalog` is the roster, `/thread` is the provenance, and nothing joins them.
The join is the product. A catalogued companion is a table in a README until you
can see that the one declaring `writes: ["spec-repo"]` is also the one whose
every write attempt was refused.

Three pages, each a pure function from data to one HTML string:

    render_overview   is the privilege boundary holding, and is the fleet awake
    render_fleet      which agents exist, and which of them did anything
    render_runs       what ran, and where each run stopped

The fourth page of the dashboard, `/thread/view`, already exists and does its job
better than a rewrite would, so it is untouched and only linked to.

Nothing here fetches. The caller passes what its own endpoints already returned:

    from .dashboard import render_fleet

    @app.get("/fleet", response_class=HTMLResponse)
    def fleet_page() -> str:
        entries = [e.to_doc() for e in ledger().all()[-300:]]
        return render_fleet(catalog()["companions"], entries, ROLE)

That keeps the service layer thin and lets every page be tested with a dict.

On ADR-004, which asks for two implementations behind one protocol: there is no
second implementation here and that is not an omission. These are pure functions
over plain dicts, with no credential and no I/O, so the offline path and the
deployed path are the same code. A second renderer invented to satisfy the rule
would be ceremony.
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from .thread_view import KIND_STYLE as _THREAD_KIND_STYLE

# One table, not three. Three pages colouring kinds from three copies of the
# vocabulary is how red stops meaning refusal, so this imports the table rather
# than restating it. It still lives in `thread_view` because that file is not
# edited here; the next change to touch it should move the definition into this
# module and leave an import behind.
KIND_STYLE: dict[str, tuple[str, str]] = dict(_THREAD_KIND_STYLE)

TRIGGER_KINDS = ("trigger.pull_request", "trigger.webhook")

# `run_id` is not always a run. The wake path writes "watch", the seeding path
# writes "seed", and `Entry.run_id` defaults to empty for anything appended by
# hand. Listing those as rows in a table of runs invents runs that never
# happened, and there are enough escalations behind "watch" that they would
# dominate the page.
CONTROL_PLANE_RUNS = {
    "watch": "unattended wakes from the query subscription",
    "seed": "history seeded so the thread has a past to recall",
    "": "appended with no run id",
}

# Three empty states, and they are not the same fact.
#
#   zero      it can happen here and has not
#   gap       the thread is a tail and the answer is further back
#   unknown   this process cannot see it at all
#
# One blank cell collapses all three, and a "0" printed for the third is a claim
# the reader has no way to check. Each gets its own wording and its own style.
NOT_ON_GCP = "no metadata server, not running on Google Cloud"
NO_TRIGGER = "trigger before this window"
NOT_SEEN = "not seen in this window"
NO_PARKED = "0 parked in this window"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _zero(text: str) -> str:
    return f"<span class=z>{_esc(text)}</span>"


def _gap(text: str) -> str:
    return f"<span class=g>{_esc(text)}</span>"


def _unknown(text: str) -> str:
    return f"<span class=u>{_esc(text)}</span>"


def _tone(text: str, tone: str) -> str:
    return f'<span class="{_esc(tone)}">{_esc(text)}</span>'


def _row(label: str, value_html: str) -> str:
    return (
        f"<div class=r><div class=l>{_esc(label)}</div>"
        f"<div class=v>{value_html}</div></div>"
    )


def _cell(label: str, value_html: str) -> str:
    return f"<span class=cell><span class=k>{_esc(label)}</span> {value_html}</span>"


def _panel(title: str, body_html: str, note: str = "") -> str:
    tail = f"<div class=note>{_esc(note)}</div>" if note else ""
    return f"<section class=card><h2>{_esc(title)}</h2>{body_html}{tail}</section>"


def _badge(text: str) -> str:
    return f"<span class=badge>{_esc(text)}</span>"


def _chip(kind: str, count: Optional[int] = None) -> str:
    """One entry kind, in the colour it already has on the thread page."""
    colour, label = KIND_STYLE.get(kind, ("#8a8790", kind or "unrecorded kind"))
    suffix = f" {count}" if count is not None else ""
    return (
        f'<span class=chip style="color:{_esc(colour)};border-color:{_esc(colour)}">'
        f"{_esc(label)}{_esc(suffix)}</span>"
    )


def _boolean(value: bool, holding: bool) -> str:
    """A boolean coloured by whether the boundary is holding, not by the boolean.

    On the writer, `write_spec_repo: true` is the boundary working as designed.
    On every other role the same value means it broke. Colouring `true` red
    everywhere would cry wolf on the one service that is supposed to have it.
    """
    return _tone("true" if value else "false", "good" if holding else "bad")


def _stamp(now: Optional[str]) -> str:
    return now or datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(stamp: Optional[str]) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        # A timestamp nobody could parse is printed raw by the caller. Guessing
        # at it here would put a confident "3m ago" beside a value we did not
        # actually read.
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _elapsed(then: Optional[str], now: str) -> str:
    a, b = _parse(then), _parse(now)
    if a is None or b is None:
        return ""
    seconds = int((b - a).total_seconds())
    if seconds < 0:
        return "recorded ahead of now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h ago"


def _when(stamp: Any, now: str) -> str:
    """A timestamp with how long ago it was, degrading to the raw value."""
    if not stamp:
        return _unknown("no timestamp recorded")
    gap = _elapsed(str(stamp), now)
    tail = f" <span class=k>{_esc(gap)}</span>" if gap else ""
    return f"{_esc(stamp)}{tail}"


def _window(shown: int, total: Optional[int]) -> str:
    """How much of the thread this page is looking at.

    `/thread` sets its count to the length of the slice, so it always equals the
    limit and a page reading it alone cannot tell a full ledger from a tail.
    Until the endpoint reports a total, say so rather than implying the window
    is everything.
    """
    if total is None:
        return _unknown(
            f"showing the last {shown} entries; this deployment does not report a total"
        )
    return _esc(f"showing the last {shown} entries of {total}")


def _join(values: Any, empty: str) -> str:
    items = [str(v) for v in (values or []) if str(v)]
    return _esc(", ".join(items)) if items else _zero(empty)


def _payload(entry: Any) -> dict[str, Any]:
    value = (entry or {}).get("payload")
    return value if isinstance(value, dict) else {}


_NAV = (
    ("/", "overview"),
    ("/fleet", "fleet"),
    ("/runs", "runs"),
    ("/standards", "standards"),
    ("/thread/view", "thread"),
)

_CSS = """
:root{--bg:#0f0f12;--fg:#e8e6ea;--dim:#8a8790;--line:#2a2a31;--card:#17171c;
 --good:#5fd75f;--bad:#ff6b6b;--warn:#ffd75f}
/* The index page hardcoded #b3261e and #146c2e, which read well on white and
   are almost invisible on #0f0f12, while the thread palette is the reverse.
   Same two meanings, one token each, one pair of values per theme. */
@media(prefers-color-scheme:light){
  :root{--bg:#fbfbfd;--fg:#1b1b1f;--dim:#66646c;--line:#e0e0e6;--card:#fff;
   --good:#146c2e;--bad:#b3261e;--warn:#7a5c00}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
header{padding:1.5rem 1.25rem .75rem;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:1.1rem;font-weight:600}
.sub{color:var(--dim);font-size:.82rem;margin-top:.3rem}
nav{display:flex;flex-wrap:wrap;gap:1rem;padding:.7rem 1.25rem;
 border-bottom:1px solid var(--line);font-size:.82rem}
nav a{color:var(--dim);text-decoration:none}
nav a.on{color:var(--fg);text-decoration:underline}
main{max-width:60rem;margin:0 auto;padding:1.25rem 1.25rem 4rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;
 padding:1rem 1.1rem;margin:0 0 1rem}
h2{margin:0 0 .7rem;font-size:.75rem;font-weight:600;color:var(--dim);
 letter-spacing:.08em;text-transform:uppercase}
h3{margin:0 0 .4rem;font-size:.9rem;font-weight:600}
.r{display:flex;gap:1rem;padding:.22rem 0;align-items:baseline}
.l{flex:0 0 13rem;color:var(--dim)}
.v{flex:1 1 auto;min-width:0;overflow-wrap:anywhere}
.good{color:var(--good);font-weight:600}
.bad{color:var(--bad);font-weight:600}
.warn{color:var(--warn)}
.z{color:var(--dim)}
.g{color:var(--dim);font-style:italic}
.u{color:var(--dim);border-bottom:1px dotted var(--line)}
.k{color:var(--dim)}
.note{color:var(--dim);font-size:.78rem;margin-top:.85rem;
 border-top:1px solid var(--line);padding-top:.6rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem;
 margin:.3rem 0 0;color:var(--dim)}
.badge{display:inline-block;padding:.05rem .4rem;border-radius:3px;
 background:#8882;font-size:.72rem;margin-left:.4rem}
.chip{display:inline-block;padding:0 .35rem;margin:0 .3rem .3rem 0;
 border:1px solid;border-radius:3px;font-size:.72rem}
.item{border-top:1px solid var(--line);padding:.75rem 0 .55rem}
.item.first{border-top:0;padding-top:0}
.grid{display:flex;flex-wrap:wrap;gap:.3rem 1.4rem;margin-top:.4rem}
.cell{font-size:.8rem}
@media(max-width:40rem){
  main{padding:1rem .85rem 3rem}
  header{padding:1.1rem .85rem .7rem}
  nav{padding:.6rem .85rem}
  .r{display:block;padding:.3rem 0}
  .l{font-size:.78rem}
}
"""


def _guard_attempts(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Guard probes that produced a result, and guard probes that did not.

    `GuardedDocAgent.attempt_write` returns a payload with no `tool_executed`
    key when the run itself raised, and `chore.py` records that dict anyway. It
    draws a three way distinction on purpose: refused, could not run, reachable.

    Reading the missing key as `False` collapsed the middle case into the first
    and printed "the tool ran 0 times", which asserts something about the tool
    from a probe that never reached it. That is the zero-versus-unknown collapse
    this module exists to avoid, in the module itself.
    """
    probes = [e for e in entries if e.get("kind") == "guard.exercised"]
    settled, unknown = [], []
    for entry in probes:
        (settled if "tool_executed" in _payload(entry) else unknown).append(entry)
    return settled, unknown


def _guard_unknown(count: int) -> str:
    noun = "probe" if count == 1 else "probes"
    return (
        f"{count} guard {noun} could not run, so whether the tool was reachable "
        f"is not known from here"
    )


def _shell(
    *,
    title: str,
    heading: str,
    sub_html: str,
    role: str,
    active: str,
    body_html: str,
) -> str:
    """The chrome every dashboard page shares, so the role badge sits in the
    same place on all of them.

    Assembled with f-strings over already-escaped fragments rather than the
    placeholder substitution `thread_view` uses. That file needs the discipline
    because it injects JSON into a `<script>`; these pages carry no script at
    all, and escaped text cannot contain a token a later replace would rewrite.
    """
    nav = "".join(
        f'<a href="{_esc(href)}"{" class=on" if href == active else ""}>'
        f"{_esc(label)}</a>"
        for href, label in _NAV
    )
    return (
        "<!doctype html><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f"<style>{_CSS}</style>"
        f"<header><h1>{_esc(heading)}{_badge(role)}</h1>"
        f"<div class=sub>{sub_html}</div></header>"
        f"<nav>{nav}</nav>"
        f"<main>{body_html}</main>"
    )


# --------------------------------------------------------------------------
# Page 1. Is the privilege boundary holding, and is the control plane awake?
# --------------------------------------------------------------------------


def _deployment_panel(identity: dict[str, Any], role: str) -> str:
    running_as = identity.get("running_as")
    project = identity.get("project")
    model = identity.get("model")
    body = (
        _row("role", _esc(role))
        # `running_as` is null off Google Cloud, and an empty cell there reads as
        # a failure rather than as a different environment.
        + _row(
            "running as",
            _esc(running_as) if running_as else _unknown(NOT_ON_GCP),
        )
        + _row("project", _esc(project) if project else _unknown("no project reported"))
        + _row("model", _esc(model) if model else _unknown("no model reported"))
    )
    return _panel(
        "this deployment",
        body,
        "One of three services running the same image. What differs is the "
        "service account Cloud Run starts it with and the role it boots into, "
        "and neither is model-supplied.",
    )


def _boundary_panel(identity: dict[str, Any], role: str) -> str:
    tools = identity.get("may_call_write_tools")
    if isinstance(tools, dict) and tools:
        rows = "".join(
            _row(
                f"may call {tool}",
                _boolean(bool(tools[tool]), bool(tools[tool]) == (role == "writer")),
            )
            for tool in sorted(tools)
        )
    else:
        rows = _row(
            "write tools",
            _unknown("this deployment did not report a write tool map"),
        )

    credential = identity.get("spec_repo_write_credential")
    if isinstance(credential, dict) and credential:
        reachable = bool(credential.get("reachable"))
        rows += _row(
            "write credential reachable",
            _boolean(reachable, reachable == (role == "writer")),
        )
        detail = credential.get("detail")
        rows += _row(
            "detail", _esc(detail) if detail else _unknown("no detail reported")
        )
        message = credential.get("message")
        if message:
            # Verbatim. A boolean is this process saying "not allowed"; the
            # Secret Manager message is Google refusing, outside this process,
            # which is the claim ADR-005 actually makes. Summarising it would
            # turn the evidence back into an assertion.
            rows += _row("what Google said", f"<pre>{_esc(message)}</pre>")
        elif reachable:
            rows += _row(
                "what Google said",
                _zero("the credential was reachable, so there is no refusal to quote"),
            )
        else:
            rows += _row(
                "what Google said",
                _unknown("refused, and this deployment reported no message"),
            )
    else:
        rows += _row(
            "write credential",
            _unknown("this deployment did not try to reach the write credential"),
        )

    return _panel(
        "the privilege boundary",
        rows,
        "The tool map is enforced inside this process, in the ADK tool "
        "interceptor. The credential is enforced by Google IAM, outside it. "
        "Only the second one survives this service deciding otherwise.",
    )


def _control_plane_panel(watch: dict[str, Any], now: str) -> str:
    if not watch:
        return _panel(
            "the control plane",
            _row("subscribed", _unknown("no watch data was passed to this page")),
        )

    if not watch.get("subscribed"):
        reason = watch.get("reason")
        body = _row("subscribed", _esc("false")) + _row(
            "reason",
            # The endpoint's own string already separates "this role does not
            # hold the subscription", which is correct on two of the three
            # deployments, from a startup exception, which is not. Rewriting it
            # here would lose that distinction.
            _esc(reason) if reason else _unknown("no reason reported"),
        )
        return _panel(
            "the control plane",
            body + _open_deferrals_row(watch),
            "Not coloured, because whether this is a fault depends on the role. "
            "The reason above is what says which it is.",
        )

    wakeups = watch.get("wakeups")
    detail = watch.get("detail") or []
    count = wakeups if isinstance(wakeups, int) else len(detail)
    body = (
        _row("subscribed", _tone("true", "good"))
        + _row(
            "mechanism",
            _esc(watch.get("mechanism")) if watch.get("mechanism")
            else _unknown("no mechanism reported"),
        )
        + _row(
            "watching",
            _esc(watch.get("watching")) if watch.get("watching")
            else _unknown("no query reported"),
        )
        + _row(
            "unattended wake-ups",
            _esc(count) if count
            else _zero("0 in this deployment's lifetime; nothing has expired yet"),
        )
    )
    for wake in detail:
        if not isinstance(wake, dict):
            continue
        body += _row(
            "woke",
            _cell("reason", _esc(wake.get("reason") or "not recorded"))
            + _cell("matched", _esc(wake.get("matched")))
            + _cell("at", _when(wake.get("at"), now)),
        )
    return _panel("the control plane", body + _open_deferrals_row(watch))


def _open_deferrals_row(watch: dict[str, Any]) -> str:
    """What is deferred and still open, which wake-up counts do not answer.

    A wake-up is an event; the open set is a state. Reconstructing the state
    from the entries in the window would over-count, because a deferral resolved
    before the window still sits in it.
    """
    if "open" not in watch:
        return _row(
            "open deferrals",
            _unknown(
                "this deployment reports wake-ups, not the open set, so this "
                "cannot be known from here"
            ),
        )
    value = watch.get("open")
    count = value if isinstance(value, int) else len(value or [])
    return _row(
        "open deferrals",
        _esc(count) if count else _zero("0 open in this deployment"),
    )


def _thread_panel(
    entries: list[dict[str, Any]], total: Optional[int], now: str
) -> str:
    if not entries:
        return _panel(
            "the thread",
            _row("entries", _zero("the thread is empty in this window")),
        )
    kinds = {str(e.get("kind") or "") for e in entries}
    actors = {str(e.get("actor") or "") for e in entries}
    runs = {str(e.get("run_id") or "") for e in entries}
    body = (
        _row("last activity", _when(entries[-1].get("recorded_at"), now))
        + _row("earliest in window", _when(entries[0].get("recorded_at"), now))
        + _row("distinct kinds", _esc(len(kinds)))
        + _row("distinct actors", _esc(len(actors)))
        + _row("distinct run ids", _esc(len(runs)))
    )
    if total is None:
        body += _row(
            "total in the ledger",
            _unknown("this deployment reports the slice length, not the total"),
        )
    else:
        body += _row("total in the ledger", _esc(total))
    return _panel("the thread", body)


def _bounds_panel(config: Optional[dict[str, Any]]) -> str:
    if not config:
        return _panel(
            "the bounds",
            _row(
                "read scope and watched repositories",
                _unknown(
                    "this deployment exposes no config endpoint, so the bounds "
                    "cannot be read from here. They are module-level values in "
                    "the service and in the tool layer."
                ),
            ),
        )
    body = (
        _row("read scope", _join(config.get("read_scope"), "no prefix is readable"))
        + _row(
            "watched repositories",
            _join(config.get("webhook_repositories"), "no repository is accepted"),
        )
        + _row(
            "max bytes per read",
            _esc(config.get("max_bytes_per_read"))
            if config.get("max_bytes_per_read") is not None
            else _unknown("not reported"),
        )
        + _row(
            "max reads per run",
            _esc(config.get("max_reads_per_run"))
            if config.get("max_reads_per_run") is not None
            else _unknown("not reported"),
        )
    )
    if "webhook_secret_configured" in config:
        configured = bool(config.get("webhook_secret_configured"))
        body += _row("webhook secret configured", _boolean(configured, configured))
        if not configured:
            body += _row(
                "consequence",
                _tone(
                    "every delivery is refused with 503, and nothing outside "
                    "this process can currently tell",
                    "bad",
                ),
            )
    else:
        body += _row(
            "webhook secret configured",
            _unknown("not reported by this deployment"),
        )
    return _panel(
        "the bounds",
        body,
        "Reads are bounded in code, not asked for in a prompt. These are the "
        "numbers the guard enforces.",
    )


def _peers_panel(peers: Optional[list[dict[str, Any]]]) -> str:
    if not peers:
        body = _row(
            "the other two deployments",
            _unknown(
                "this deployment cannot read the evaluator's or the writer's "
                "identity; asking would need an authenticated call it does not make"
            ),
        )
    else:
        # Rendered as text rather than as links. Nothing on this page needs to be
        # clickable, and a URL that never reaches an href attribute cannot carry
        # one out of it.
        body = "".join(
            _row(
                str(peer.get("role") or "peer"),
                _esc(peer.get("url")) if peer.get("url")
                else _unknown("no URL configured"),
            )
            for peer in peers
            if isinstance(peer, dict)
        )
    return _panel(
        "the rest of the fleet",
        body,
        "This page describes the one process that answered the request. The "
        "other two run the same image under different service accounts.",
    )


def render_overview(
    identity: dict[str, Any],
    watch: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    total: Optional[int] = None,
    config: Optional[dict[str, Any]] = None,
    peers: Optional[list[dict[str, Any]]] = None,
    now: Optional[str] = None,
) -> str:
    """Page 1. `identity` is GET /identity, `watch` is GET /watch, `entries` is
    the `entries` list from GET /thread."""
    identity = identity or {}
    watch = watch or {}
    entries = list(entries or [])
    role = str(identity.get("role") or "unknown")
    stamp = _stamp(now)
    body = (
        _deployment_panel(identity, role)
        + _boundary_panel(identity, role)
        + _control_plane_panel(watch, stamp)
        + _thread_panel(entries, total, stamp)
        + _bounds_panel(config)
        + _peers_panel(peers)
    )
    return _shell(
        title=f"Mitos · {role}",
        heading="Mitos · overview",
        sub_html=_window(len(entries), total),
        role=role,
        active="/",
        body_html=body,
    )


# --------------------------------------------------------------------------
# Page 2. Which agents exist, and which of them have actually done anything?
# --------------------------------------------------------------------------


def _companion_card(
    companion: dict[str, Any],
    mine: list[dict[str, Any]],
    now: str,
    first: bool,
) -> str:
    name = str(companion.get("name") or "unnamed")
    head = (
        f"<h3>{_esc(name)}{_badge(companion.get('department') or 'no department')}"
        f"{_badge(f'{len(mine)} entries')}</h3>"
    )
    declared = (
        _row(
            "role",
            _esc(companion.get("role")) if companion.get("role")
            else _zero("no role declared"),
        )
        + _row("wakes on", _join(companion.get("wakes_on"), "nothing"))
        + _row("reads", _join(companion.get("reads"), "nothing"))
        + _row("writes", _join(companion.get("writes"), "nothing"))
    )

    if not mine:
        # "not seen in this window" and "never ran" are different claims, and the
        # thread is a tail, so only the first one is available here.
        observed = _row("observed", _gap(NOT_SEEN))
    else:
        counts = Counter(str(e.get("kind") or "") for e in mine)
        chips = "".join(_chip(kind, counts[kind]) for kind in sorted(counts))
        responses = [e for e in mine if e.get("kind") == "specialist.response"]
        last_status = _payload(responses[-1]).get("status") if responses else None
        observed = (
            _row("produced", chips)
            + _row("last seen", _when(mine[-1].get("recorded_at"), now))
            + _row(
                "last response status",
                _tone(str(last_status), "good" if last_status == "ok" else "warn")
                if last_status
                else _zero("no specialist response in this window"),
            )
        )
        attempts, unknown = _guard_attempts(mine)
        denied = sum(1 for e in attempts if _payload(e).get("denied"))
        executed = sum(1 for e in attempts if _payload(e).get("tool_executed"))
        if attempts:
            observed += _row(
                "write attempts",
                _tone(
                    f"{denied} of {len(attempts)} refused, the tool ran "
                    f"{executed} times",
                    "good" if executed == 0 and denied == len(attempts) else "bad",
                ),
            )
        elif not unknown and companion.get("writes"):
            # Only claim zero when there were no probes at all. With a probe
            # that could not run, zero is not what happened.
            observed += _row(
                "write attempts", _zero("0 attempts in this window")
            )
        if unknown:
            observed += _row("guard probes", _unknown(_guard_unknown(len(unknown))))

    return (
        f'<section class="item{" first" if first else ""}">'
        f"{head}{declared}{observed}</section>"
    )


def render_fleet(
    companions: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    role: str,
    *,
    total: Optional[int] = None,
    now: Optional[str] = None,
) -> str:
    """Page 2. `companions` is the list from GET /catalog, joined to the thread
    by actor.

    The join is the page. The catalog on its own is a table in a README; joined
    to the thread it says which companions the router actually reaches, and a
    companion that only ever produces one kind of entry is one the router skips.
    """
    companions = list(companions or [])
    entries = list(entries or [])
    stamp = _stamp(now)

    by_actor: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_actor.setdefault(str(entry.get("actor") or ""), []).append(entry)

    if companions:
        cards = "".join(
            _companion_card(
                c, by_actor.get(str(c.get("name") or ""), []), stamp, index == 0
            )
            for index, c in enumerate(companions)
        )
    else:
        cards = _row("companions", _zero("the catalog reported no companions"))

    catalogued = {str(c.get("name") or "") for c in companions}
    uncatalogued = sorted(a for a in by_actor if a and a not in catalogued)
    if uncatalogued:
        extra = "".join(
            f"{_esc(actor)}{_badge(f'{len(by_actor[actor])} entries')} "
            for actor in uncatalogued
        )
    else:
        extra = _zero("every actor in this window is a catalogued companion")

    body = _panel(
        "catalogued companions, joined to the thread",
        cards,
        "The catalog is queryable rather than documentary: the router reads it "
        "to decide who to wake, so a companion appearing here with only one "
        "kind of entry is one the router never dispatches work to.",
    ) + _panel(
        "actors in the thread that are not companions",
        f"<div class=grid>{extra}</div>",
        "The webhook and the writer append entries without being catalogued "
        "agents. They are listed so the counts above add up.",
    )

    return _shell(
        title="Mitos · fleet",
        heading="Mitos · fleet",
        sub_html=_window(len(entries), total),
        role=role,
        active="/fleet",
        body_html=body,
    )


# --------------------------------------------------------------------------
# Page 3. What ran, and where did each run stop?
# --------------------------------------------------------------------------


def _group_by_run(
    entries: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """Keyed on run_id, deliberately not on subject.

    `subject` means two different things in the same collection: the chore path
    writes a constant service path for every run, and the webhook path writes
    `repository#number`. Grouping on it would merge every chore into one row.
    """
    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        run_id = str(entry.get("run_id") or "")
        if run_id not in groups:
            groups[run_id] = []
            order.append(run_id)
        groups[run_id].append(entry)
    return order, groups


# The statuses that stop an item, taken from `envelope.Status.is_terminal`
# rather than restated. `needs_changes` is documented there as "it can proceed",
# and `chore.py` appends `item.parked` only when `parks_the_item` is true. This
# used to treat any status other than ok as a park, so a run that carried a
# needs_changes response, passed the gate, proposed a plan and published was
# reported as parked. The thread said one thing and the page said another.
_PARKING_STATUSES = ("blocked", "error")


def _is_parked(group: list[dict[str, Any]]) -> Optional[str]:
    """Who parked this run, or None.

    An explicit `item.parked` settles it. Failing that, a specialist response
    carrying a terminal status is the same event seen one entry earlier, which
    matters for a thread truncated by the window.
    """
    for entry in group:
        if entry.get("kind") == "item.parked":
            return str(entry.get("actor") or "a companion")
    for entry in group:
        if entry.get("kind") != "specialist.response":
            continue
        if str(_payload(entry).get("status") or "") in _PARKING_STATUSES:
            return str(entry.get("actor") or "a companion")
    return None


def _totals_panel(entries: list[dict[str, Any]], parked_runs: int) -> str:
    counts = Counter(str(e.get("kind") or "") for e in entries)
    attempts, unknown = _guard_attempts(entries)
    denied = sum(1 for e in attempts if _payload(e).get("denied"))
    executed = sum(1 for e in attempts if _payload(e).get("tool_executed"))
    verdicts = [e for e in entries if e.get("kind") == "evaluator.verdict"]
    passed = sum(1 for e in verdicts if _payload(e).get("passed"))
    writes = counts["write.executed"]

    body = (
        _row("dispatches", _esc(counts["fleet.dispatch"]))
        + _row("specialist responses", _esc(counts["specialist.response"]))
        + _row(
            "guard denials",
            _tone(
                f"{denied} of {len(attempts)} attempts, the tool ran {executed} times",
                # A write attempt that happened and was not refused is never
                # the "has not happened" tone. That style is documented as
                # meaning it can happen here and has not.
                "bad" if executed else ("good" if denied == len(attempts) else "warn"),
            )
            if attempts
            else _zero("0 write attempts in this window"),
        )
        + (
            _row("guard probes", _unknown(_guard_unknown(len(unknown))))
            if unknown
            else ""
        )
        + _row(
            "evaluator verdicts",
            _esc(f"{passed} passed of {len(verdicts)}")
            if verdicts
            else _zero("0 verdicts in this window"),
        )
        + _row("plans proposed", _esc(counts["plan.proposed"]))
        + _row(
            "writes executed",
            _tone(str(writes), "good")
            if writes
            else _zero("0 in this window; every run stopped at the approval"),
        )
        + _row(
            "parked",
            _tone(f"{parked_runs} parked in this window", "warn")
            if parked_runs
            else _zero(NO_PARKED),
        )
        + _row("deferrals", _esc(counts["finding.deferred"]))
        + _row("escalations", _esc(counts["finding.escalated"]))
    )
    return _panel(
        "this window, counted",
        body,
        "Counted over every entry in the window, including the control-plane "
        "ones below. Zero writes with plans proposed is the approval holding, "
        "not a failure.",
    )


def _control_panel(
    order: list[str], groups: dict[str, list[dict[str, Any]]], now: str
) -> str:
    present = [r for r in order if r in CONTROL_PLANE_RUNS]
    if not present:
        return _panel(
            "control plane, not chores",
            _row("entries", _zero("no control-plane entries in this window")),
        )
    body = ""
    for run_id in present:
        group = groups[run_id]
        counts = Counter(str(e.get("kind") or "") for e in group)
        chips = "".join(_chip(kind, counts[kind]) for kind in sorted(counts))
        body += _row(
            run_id or "no run id",
            f"<div>{_esc(CONTROL_PLANE_RUNS[run_id])}</div>"
            f"<div class=grid>{chips}</div>"
            f"<div class=grid>{_cell('last', _when(group[-1].get('recorded_at'), now))}</div>",
        )
    return _panel(
        "control plane, not chores",
        body,
        "These carry a run id but are not runs. Listing them as rows in the "
        "table below would invent chores that never happened.",
    )


def _run_card(
    run_id: str, group: list[dict[str, Any]], now: str, first: bool
) -> str:
    trigger = next((e for e in group if e.get("kind") in TRIGGER_KINDS), None)
    terminal = str(group[-1].get("kind") or "")
    parked_by = _is_parked(group)

    head = (
        f"<h3>run {_esc(run_id or 'no run id')}"
        f"{_badge(str(len(group)) + ' entries')}</h3>"
        f"<div class=grid>{_chip(terminal)}"
        + (
            _chip("item.parked") if parked_by and terminal != "item.parked" else ""
        )
        + "</div>"
    )

    if trigger is None:
        # The thread is a tail. A run whose trigger fell off the front of the
        # window has not lost its trigger; we simply cannot see it from here.
        trigger_html = _gap(NO_TRIGGER)
    else:
        payload = _payload(trigger)
        parts = [_chip(str(trigger.get("kind") or ""))]
        if payload.get("pr") is not None:
            parts.append(_esc(f"PR {payload.get('pr')}"))
        repository = payload.get("repository")
        parts.append(
            _esc(repository)
            if repository
            else _unknown(
                "no repository recorded, so the specialists read the built-in "
                "demo corpus rather than a checkout"
            )
        )
        title = payload.get("title")
        if title:
            # Written by whoever opened the pull request. It is escaped like
            # everything else here, and it is the reason everything else here
            # is escaped.
            parts.append(f"<span class=k>{_esc(title)}</span>")
        trigger_html = " ".join(parts)

    responses = [e for e in group if e.get("kind") == "specialist.response"]
    plans = [e for e in group if e.get("kind") == "plan.proposed"]
    verdicts = [e for e in group if e.get("kind") == "evaluator.verdict"]
    findings = _payload(plans[-1]).get("findings") if plans else None
    last_verdict = _payload(verdicts[-1]).get("passed") if verdicts else None

    cells = (
        _cell("specialists", _esc(len(responses)))
        + _cell(
            "findings on the plan",
            _esc(len(findings)) if isinstance(findings, list)
            else _zero("no plan proposed"),
        )
        + _cell(
            "verdict",
            _tone("passed" if last_verdict else "failed",
                  "good" if last_verdict else "bad")
            if last_verdict is not None
            else _zero("no verdict"),
        )
        + _cell(
            "parked",
            _tone(f"by {parked_by}", "warn") if parked_by else _zero("no"),
        )
    )

    rows = (
        _row("subject", _esc(group[0].get("subject") or "") or _zero("no subject"))
        + _row("trigger", trigger_html)
        + _row("stopped at", _chip(terminal))
        + _row("first entry", _when(group[0].get("recorded_at"), now))
        + _row("last entry", _when(group[-1].get("recorded_at"), now))
        + f"<div class=grid>{cells}</div>"
    )
    return (
        f'<section class="item{" first" if first else ""}">{head}{rows}</section>'
    )


def render_runs(
    entries: list[dict[str, Any]],
    role: str,
    *,
    total: Optional[int] = None,
    now: Optional[str] = None,
) -> str:
    """Page 3. `entries` is the `entries` list from GET /thread."""
    entries = list(entries or [])
    stamp = _stamp(now)
    order, groups = _group_by_run(entries)
    run_ids = [r for r in order if r not in CONTROL_PLANE_RUNS]
    # Newest first: an operator opens this page to see what just happened, and
    # the thread's own order puts that at the bottom.
    run_ids.sort(key=lambda r: str(groups[r][0].get("recorded_at") or ""), reverse=True)

    parked_runs = sum(1 for r in run_ids if _is_parked(groups[r]))

    if run_ids:
        cards = "".join(
            _run_card(r, groups[r], stamp, index == 0)
            for index, r in enumerate(run_ids)
        )
    else:
        cards = _row("runs", _zero("no runs in this window"))

    body = (
        _totals_panel(entries, parked_runs)
        + _control_panel(order, groups, stamp)
        + _panel(
            "runs",
            cards,
            "Grouped by run id. Subject is not the key: the chore path writes a "
            "constant service path for every run and the webhook path writes "
            "repository#number, so the same field means two things here.",
        )
    )

    return _shell(
        title="Mitos · runs",
        heading="Mitos · runs",
        sub_html=_window(len(entries), total),
        role=role,
        active="/runs",
        body_html=body,
    )


# --------------------------------------------------------------------------
# Page 4. What does this repository fail, and what could not be decided here?
# --------------------------------------------------------------------------

# Verdicts, in the order a reader should meet them, with the tone each carries.
# The three that are not a decision get the unknown tone rather than a colour,
# because a compliance page that paints "could not be determined" the same green
# as "passed" is the failure this whole module was built to avoid.
_VERDICT_TONE = {
    "failed": ("bad", "failed"),
    "suspected": ("warn", "suspected"),
    "passed": ("good", "passed"),
    "not_applicable": ("z", "does not apply"),
    # `u`, the class `_unknown` uses, not a new name. Writing "unknown" here
    # produced a class the stylesheet does not define, so the three verdicts
    # that are not a decision rendered with no styling at all and looked like
    # plain text beside a green pass. The page exists to keep those apart.
    "undetermined": ("u", "could not be determined"),
    "needs_judgement": ("u", "needs a reader"),
    "not_checkable": ("u", "leaves no trace here"),
}
_VERDICT_ORDER = tuple(_VERDICT_TONE)


def _finding_card(finding: dict[str, Any]) -> str:
    verdict = str(finding.get("verdict") or "undetermined")
    tone, label = _VERDICT_TONE.get(verdict, ("unknown", verdict))
    severity = str(finding.get("severity") or "")
    # `Finding.as_dict` writes `rule`. Reading `rule_id`, the dataclass field
    # name, printed "None" for every row on the live page. Both are accepted so
    # a caller holding either shape renders, and the tests below build their
    # fixtures from the real producer rather than restating its keys, which is
    # how this got through: the fixture agreed with the bug.
    rule = finding.get("rule") or finding.get("rule_id") or "unnamed rule"
    head = (
        f'<div class=h><span class=n>{_esc(rule)}</span>'
        f"{_badge(severity) if severity else ''}"
        f"<span class=v>{_tone(label, tone)}</span></div>"
    )
    body = _row("looked for", _esc(finding.get("looked_for")))
    looked_at = finding.get("looked_at") or ()
    body += _row(
        "looked at",
        _join(looked_at, "")
        or _unknown("nothing was opened for this rule"),
    )
    body += _row("found", _esc(finding.get("found")))
    if finding.get("limitation"):
        body += _row("limitation", _unknown(str(finding["limitation"])))
    return f'<section class="item">{head}{body}</section>'


def _audit_summary_panel(summary: dict[str, Any], repository: Optional[str]) -> str:
    """The counts, with the undecided ones kept out of the decided ones.

    `passed` and `failed` are a verdict. Everything under "not decided here" is
    not, and the two are never added together. A single compliance percentage
    would have to pick a side for the middle column, and every choice it could
    make would be a lie.
    """
    decided = int(summary.get("passed", 0)) + int(summary.get("failed", 0))
    body = _row(
        "repository",
        _esc(repository)
        if repository
        else _unknown("the built-in demo corpus, not your code"),
    )
    body += _row("rules in the standard", _esc(summary.get("rules", 0)))
    body += _cell("passed", _tone(str(summary.get("passed", 0)), "good"))
    body += _cell(
        "failed",
        _tone(str(summary.get("failed", 0)), "bad" if summary.get("failed") else "good"),
    )
    body += _cell(
        "suspected",
        _tone(str(summary.get("suspected", 0)), "warn" if summary.get("suspected") else "z"),
    )
    body += _row(
        "not decided here",
        _unknown(
            f"{summary.get('undetermined', 0)} could not be determined, "
            f"{summary.get('needs_judgement', 0)} need a reader, "
            f"{summary.get('not_checkable', 0)} leave no trace in a repository, "
            f"{summary.get('not_applicable', 0)} do not apply"
        ),
    )
    return _panel(
        "the audit",
        body,
        note=(
            f"{decided} of {summary.get('rules', 0)} rules were decided here. The rest "
            "are listed as undecided rather than folded into either column, because a "
            "number that counts silence as compliance is worse than no number."
        ),
    )


def render_standards(
    findings: list[dict[str, Any]],
    summary: dict[str, Any],
    role: str,
    *,
    repository: Optional[str] = None,
    note: str = "",
) -> str:
    """Page 4. `findings` and `summary` are what GET /standards.json returns.

    Sorted by verdict rather than by rule id, so the failures are the first
    thing on the page and the six rules that a repository cannot answer are the
    last. Nobody scrolls to find out what is broken.
    """
    findings = list(findings or [])
    order = {v: i for i, v in enumerate(_VERDICT_ORDER)}
    findings.sort(
        key=lambda f: (
            order.get(str(f.get("verdict")), 99),
            str(f.get("rule") or f.get("rule_id") or ""),
        )
    )

    body = _audit_summary_panel(summary or {}, repository)
    if note:
        body += _panel("about this run", _row("note", _unknown(note)))
    body += _panel(
        "every rule, worst first",
        "".join(_finding_card(f) for f in findings)
        or _zero("no rules were evaluated"),
    )
    return _shell(
        title=f"Mitos · standards",
        heading="Mitos · standards",
        sub_html=_esc(repository) if repository else "the demo corpus",
        role=role,
        active="/standards",
        body_html=body,
    )
