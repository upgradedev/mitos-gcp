"""The thread, as the thing it actually is.

Mitos is named for Ariadne's thread, and until now the thread was rendered as
lines of text. It is a graph: every entry names the entry it came from, so any
outcome can be walked back to the diff that caused it. Showing that as a list
asks the reader to do the walking in their head.

So this renders it. Click a write and the path back to the trigger lights up.
That is the one claim in the product a picture makes better than a sentence, and
it is the reason the product has the name it has.

Deliberately dependency-free: one self-contained page, no framework, no CDN. It
reads the same `/thread` data the API already serves, so there is nothing here
that could drift from what the fleet actually recorded.
"""

from __future__ import annotations

import html
import json
from typing import Any

# Vocabulary of the thread, in the order a run produces it. Colour carries
# meaning: red is a refusal or a finding, green is the one governed write.
KIND_STYLE = {
    "trigger.pull_request": ("#5fd7d7", "trigger"),
    "fleet.dispatch": ("#7aa2f7", "dispatch"),
    "specialist.response": ("#8a8790", "specialist"),
    "guard.exercised": ("#ff6b6b", "guard"),
    "evaluator.verdict": ("#7aa2f7", "gate"),
    "item.parked": ("#ffd75f", "parked"),
    "plan.proposed": ("#ffffff", "plan"),
    # Findings worth a reviewer, and no document this run could
    # honestly propose editing. Coloured as a finding, not a plan:
    # nothing is being offered for approval.
    "plan.review_only": ("#e0af68", "review"),
    # The router found nothing for this fleet to govern. Quiet, because it
    # is neither a finding nor a step that did work.
    "run.nothing_to_govern": ("#8a8790", "nothing to govern"),
    # The deterministic gate ran in the evaluator service, not here.
    "gate.delegated": ("#7aa2f7", "gate delegated"),
    "write.executed": ("#5fd75f", "write"),
    "finding.raised": ("#ff9e64", "finding"),
    "finding.deferred": ("#ffd75f", "deferred"),
    "finding.escalated": ("#ffd75f", "escalated"),
    # The three the webhook produces. Without them a real delivery rendered in
    # the fallback grey, so a failed trigger looked exactly like a routine step
    # on the one page where colour is the whole point.
    "trigger.webhook": ("#5fd7d7", "trigger"),
    "trigger.ignored": ("#8a8790", "ignored"),
    # Amber, not grey. An incomplete diff is not a delivery that needed no
    # attention; it is a refusal to judge a change the fleet could not
    # prove it had read whole, and it must not read as a quiet skip.
    "trigger.incomplete_diff": ("#b8860b", "diff not whole, refused"),
    "trigger.failed": ("#ff6b6b", "failed"),
    # Refusal red. An instruction planted in a diff is the most
    # interesting thing on the page when it happens.
    "injection.detected": ("#ff6b6b", "injection"),
}


def _json_for_script(value: Any) -> str:
    """JSON safe to embed inside a `<script>` element.

    `json.dumps` escapes what JSON needs and nothing HTML needs, so a string
    containing `</script>` survives it intact and closes the block early. The
    payload here carries a pull request title, which on the webhook path is
    written by whoever opened the pull request, arrives wrapped in `Tainted`,
    and is therefore exactly the input that must not be trusted.

    Escaping to `\\uXXXX` keeps the value byte-identical once parsed while
    leaving nothing the HTML tokenizer will act on. U+2028 and U+2029 are here
    because they terminate a line in JavaScript but not in JSON.
    """
    blob = json.dumps(value)
    for char, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        (" ", "\\u2028"),
        (" ", "\\u2029"),
    ):
        blob = blob.replace(char, escaped)
    return blob


def render(
    entries: list[dict[str, Any]], role: str, wakeups: int, nonce: str = ""
) -> str:
    """One page. `entries` is exactly what GET /thread returns."""
    # The data substitution goes last. Done first, a pull request titled
    # `__ROLE__` would be rewritten by the next replace, which is untrusted
    # input steering the template.
    return (
        _PAGE.replace("__ROLE__", html.escape(role))
        .replace("__WAKEUPS__", str(wakeups))
        .replace("__STYLE__", _json_for_script(KIND_STYLE))
        .replace("__DATA__", _json_for_script(entries))
        # Escaped, though the value is generated here and never reaches this
        # function from a request. A nonce interpolated into an attribute is
        # still an interpolation into an attribute.
        .replace("__NONCE__", html.escape(nonce, quote=True))
    )


_PAGE = """<!doctype html><meta charset=utf-8>
<title>Mitos · the thread</title>
<style nonce="__NONCE__">
:root{--bg:#0f0f12;--fg:#e8e6ea;--dim:#8a8790;--line:#2a2a31;--card:#17171c}
@media(prefers-color-scheme:light){
  :root{--bg:#fbfbfd;--fg:#1b1b1f;--dim:#66646c;--line:#e0e0e6;--card:#fff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
header{padding:1.5rem 1.5rem .75rem;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:1.1rem;font-weight:600}
.sub{color:var(--dim);font-size:.82rem;margin-top:.3rem}
.wrap{display:flex;gap:0;height:calc(100vh - 92px)}
.list{flex:1 1 60%;overflow-y:auto;padding:1rem 0 4rem}
.side{flex:0 0 40%;border-left:1px solid var(--line);overflow-y:auto;
 padding:1.25rem;background:var(--card)}
.row{display:flex;gap:.75rem;align-items:flex-start;padding:.3rem 1.5rem;
 cursor:pointer;border-left:3px solid transparent}
.row:hover{background:#8881}
.row.lit{background:#7aa2f722;border-left-color:#7aa2f7}
.row.dimmed{opacity:.25}
.dot{width:9px;height:9px;border-radius:50%;margin-top:.45rem;flex:0 0 auto}
.k{width:9.5rem;flex:0 0 auto}
.who{color:var(--dim);flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.t{color:var(--dim);font-size:.78rem;flex:0 0 auto}
.rail{position:relative}
.rail:before{content:"";position:absolute;left:calc(1.5rem + 4px);top:0;bottom:0;
 width:1px;background:var(--line)}
pre{white-space:pre-wrap;word-break:break-word;font-size:.8rem;margin:.5rem 0 0;
 color:var(--dim)}
.hint{color:var(--dim);padding:1rem 1.5rem;font-size:.82rem}
b{font-weight:600}
.badge{display:inline-block;padding:.05rem .4rem;border-radius:3px;
 background:#8882;font-size:.72rem;margin-left:.4rem}
</style>
<header>
  <h1>Mitos &middot; the thread <span class=badge>__ROLE__</span></h1>
  <div class=sub>Every entry names the entry it came from. Click one to light
   the path back to the pull request that caused it.
   <b>__WAKEUPS__</b> unattended wake-ups from the query subscription.</div>
</header>
<div class=wrap>
  <div class="list rail" id=list></div>
  <div class=side id=side><div class=hint>Select an entry.</div></div>
</div>
<script nonce="__NONCE__">
const DATA = __DATA__, STYLE = __STYLE__;
const byId = Object.fromEntries(DATA.map(e => [e.entry_id, e]));
const list = document.getElementById('list'), side = document.getElementById('side');

// Everything below builds markup by concatenation, so anything interpolated
// has to come through here first. Most of these values are set by the fleet,
// but the entry kind reaches a colour lookup and the row is one refactor away
// from carrying a title, and the cost of being consistent is one function.
function esc(v){
  return String(v == null ? '' : v).replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}

function ancestry(id){
  const path = [], seen = new Set();
  let cur = id;
  while (cur && byId[cur] && !seen.has(cur)) { seen.add(cur); path.push(cur); cur = byId[cur].parent_id; }
  return path;
}

DATA.forEach(e => {
  const [colour, label] = STYLE[e.kind] || ['#8a8790', e.kind];
  const row = document.createElement('div');
  row.className = 'row'; row.dataset.id = e.entry_id;
  row.innerHTML =
    `<span class=dot style="background:${esc(colour)}"></span>` +
    `<span class=k style="color:${esc(colour)}">${esc(label)}</span>` +
    `<span class=who>${esc(e.actor||'')}</span>` +
    `<span class=t>${esc((e.recorded_at||'').slice(11,19))}</span>`;
  row.onclick = () => select(e.entry_id);
  list.appendChild(row);
});

function select(id){
  const path = new Set(ancestry(id));
  document.querySelectorAll('.row').forEach(r => {
    r.classList.toggle('lit', path.has(r.dataset.id));
    r.classList.toggle('dimmed', !path.has(r.dataset.id));
  });
  const e = byId[id];
  const chain = ancestry(id).reverse().map(x => {
    const [, l] = STYLE[byId[x].kind] || ['', byId[x].kind];
    return l;
  }).join(' -> ');
  side.innerHTML =
    `<div><b>${esc(e.kind)}</b></div>` +
    `<div class=sub>${esc(e.actor)} &middot; ${esc(e.recorded_at)}</div>` +
    `<div class=sub style="margin-top:.6rem">retraced: ${esc(chain)}</div>` +
    `<pre></pre>`;
  // textContent, not innerHTML. The payload carries the pull request title,
  // which on the webhook path is written by whoever opened it.
  side.querySelector('pre').textContent = JSON.stringify(e.payload, null, 1);
}

const last = DATA.filter(e => e.kind === 'write.executed').pop() || DATA[DATA.length-1];
if (last) select(last.entry_id);
</script>
"""
