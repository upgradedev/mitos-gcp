"""Auditing a repository against the engineering standards.

Twenty four rules were triaged before any of this was written, and the triage is
the design. Thirteen of them are decidable from the contents of a repository,
five need somebody who can read, and six leave no trace in a repository at all.
Those three groups are three lists in this module, and nothing moves between
them at runtime.

That split is the whole point. A compliance tool earns its keep by being trusted,
and the fastest way to lose that is to report a pass on evidence that does not
exist. `commit-message-format` is a good example: commit messages live in git
history, a working tree audit sees none, and a committed commitlint config would
prove an intent to enforce rather than any commit conforming. Reporting the first
as the second is exactly the substitution that makes a tool start lying, so the
rule is declared not checkable and stays that way.

So the vocabulary a reader is given is deliberately wider than pass and fail:

    passed          the check ran and found nothing wrong
    failed          the check ran and found something wrong
    suspected       a pattern with known false positives matched
    not_applicable  the rule has a precondition this repository does not meet
    undetermined    the check ran and could not decide
    needs_judgement decidable, but not by a pattern, so it goes to a reader
    not_checkable   nothing in a repository records this

The last four are all "could not be determined", they are counted separately in
the summary, and none of them is ever folded into the pass count. A number that
quietly counts silence as compliance is worse than no number.

Reads are bounded here for the same reason they are bounded in `tools.py`, and
the bound is in code rather than in a prompt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from .envelope import Response, Status
from .tools import Corpus, build_corpus

COMPANION = "standards-companion"

# A standards audit is about the root of the tree: the pipeline definition, the
# SAM template and CLAUDE.md all sit there, and the default corpus scope would
# hide every one of them.
AUDIT_SCOPE: tuple[str, ...] = ("",)

# Deliberately not `tools.MAX_READS_PER_RUN`, which is 12. That budget bounds an
# agent that decides where to look, and twelve reads is generous for a question
# about one diff. This is a sweep over a whole tree by thirteen checks, so the
# number has to be larger, and it still has to be finite: a repository with a
# hundred thousand files must not be able to turn an audit into a crawl.
MAX_FILES_PER_AUDIT = 300
# Larger than `tools.MAX_BYTES_PER_READ` for the same reason. That cap exists to
# bound what a model can be made to swallow; nothing read here reaches a model,
# so the only job left is to stop one pathological file being loaded whole.
MAX_BYTES_PER_FILE = 64_000
# Probing for an HTTP surface means opening source files until one matches. If
# the probe runs out before matching, "this repository exposes no HTTP" is not a
# conclusion, it is a timeout, and the checks that depend on it say so.
MAX_ROUTER_PROBE = 120

# What a finding points at when the thing it examined was the path listing
# itself, rather than the contents of any file.
LISTING = "<repository listing>"
BY_A_READER = "<named by a reader, no path given>"


class Unreadable(Exception):
    """A file this audit needed and could not get."""


class YamlSubsetError(Exception):
    """The pipeline reader met YAML it does not model."""


# --------------------------------------------------------------------------
# A YAML subset, because the offline path is standard library only
#
# The unit suite installs pytest and nothing else, so PyYAML is not available
# where these checks are exercised, and a check that only runs in CI is a check
# that rots. The subset covers what pipeline and template files actually use.
# Everything outside it raises, and a raise becomes `undetermined` rather than a
# guess: a wrong answer about a stage graph is worse than no answer.
# --------------------------------------------------------------------------

_UNMODELLED = re.compile(r"(?m)(^\s*<<\s*:|(?::|^\s*-)[ \t]+[&*][A-Za-z_])")
_DOC_MARKER = re.compile(r"(?m)^---\s*$")
_BLOCK_SCALAR = ("|", "|-", "|+", ">", ">-", ">+")


def _prepare(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if _UNMODELLED.search(text):
        raise YamlSubsetError("anchors, aliases and merge keys are not modelled")
    markers = [i for i, ln in enumerate(lines) if _DOC_MARKER.fullmatch(ln)]
    if markers:
        first_content = next(
            (i for i, ln in enumerate(lines) if ln.strip() and not ln.startswith("#")),
            0,
        )
        if len(markers) > 1 or markers[0] != first_content:
            raise YamlSubsetError("multiple documents in one file are not modelled")
        lines = lines[markers[0] + 1 :]
    for ln in lines:
        if "\t" in ln[: len(ln) - len(ln.lstrip(" \t"))]:
            raise YamlSubsetError("tab indentation is not modelled")
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _skip_blank(lines: list[str], i: int) -> int:
    while i < len(lines) and _blank(lines[i]):
        i += 1
    return i


def _strip_comment(text: str) -> str:
    out: list[str] = []
    quote = ""
    prev = " "
    for ch in text:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and prev in " \t":
            break
        else:
            out.append(ch)
        prev = ch
    return "".join(out).rstrip()


def _split_key(body: str) -> Optional[tuple[str, str]]:
    if body[:1] in ('"', "'"):
        quote = body[0]
        end = body.find(quote, 1)
        if end == -1 or not body[end + 1 :].lstrip().startswith(":"):
            return None
        return body[1:end], body[end + 1 :].lstrip()[1:].strip()
    depth = 0
    for idx, ch in enumerate(body):
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0 and body[idx + 1 : idx + 2] in ("", " ", "\t"):
            return body[:idx].strip(), body[idx + 1 :].strip()
    return None


def _split_flow(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for ch in body:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return [p.strip() for p in parts]


def _scalar(raw: str) -> Any:
    """Scalars stay text. Nothing here coerces `1` to an integer or `on` to a
    boolean, because every check in this module compares text, and a check that
    depended on YAML's coercion rules would be testing the parser."""
    text = raw.strip()
    if not text:
        return ""
    if text[0] == "[" and text[-1] == "]":
        return [_scalar(p) for p in _split_flow(text[1:-1])]
    if text[0] == "{" and text[-1] == "}":
        out: dict[str, Any] = {}
        for part in _split_flow(text[1:-1]):
            pair = _split_key(part)
            if pair is None:
                raise YamlSubsetError(f"flow mapping entry not understood: {part!r}")
            out[pair[0]] = _scalar(pair[1])
        return out
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        # Inside single quotes YAML writes one apostrophe as two. Left doubled,
        # a committed value would not match the value in the store it is
        # compared against.
        return inner.replace("''", "'") if text[0] == "'" else inner
    return text


def _fold(body: list[str]) -> str:
    """A `>` block joins its lines with a space, a `|` block keeps the breaks.

    Joining a folded block with newlines produced a string no author wrote,
    which is the quiet kind of wrong: nothing raises and a check reads a value
    that differs from the one the pipeline runs. Lines indented deeper than the
    first stay literal under the specification, and that part is not modelled,
    so meeting one refuses rather than guesses.
    """
    out: list[str] = []
    for line in body:
        if not line:
            out.append("\n")
            continue
        if line.startswith(" "):
            raise YamlSubsetError(
                "a folded block with more indented lines in it is not modelled"
            )
        if out and out[-1] != "\n":
            out.append(" ")
        out.append(line)
    return "".join(out)


def _parse_block_scalar(
    lines: list[str], i: int, parent: int, folded: bool = False
) -> tuple[str, int]:
    body: list[str] = []
    while i < len(lines):
        if not lines[i].strip():
            body.append("")
            i += 1
            continue
        if _indent(lines[i]) <= parent:
            break
        body.append(lines[i])
        i += 1
    while body and not body[-1]:
        body.pop()
    pad = min((_indent(b) for b in body if b.strip()), default=0)
    text = [b[pad:] if b.strip() else "" for b in body]
    return (_fold(text) if folded else "\n".join(text)), i


def _continue_plain(
    lines: list[str], i: int, indent: int, first: str
) -> tuple[str, int]:
    """A plain value that runs on to the next line, folded the way YAML folds it.

    Without this, a pipeline whose displayName wraps is a document this reader
    cannot parse at all, and both critical secret scan rules land as
    undetermined over a line break in prose. The continuation stops at anything
    that could be structure, so a mapping entry or a sequence item indented by
    mistake still refuses instead of being swallowed into a string.
    """
    parts = [first]
    while i < len(lines) and lines[i].strip() and _indent(lines[i]) > indent:
        body = _strip_comment(lines[i].strip())
        if not body or body == "-" or body.startswith("- ") or _split_key(body):
            break
        parts.append(body)
        i += 1
    return " ".join(parts), i


def _parse_mapping(lines: list[str], i: int, indent: int) -> tuple[dict, int]:
    out: dict[str, Any] = {}
    while True:
        i = _skip_blank(lines, i)
        if i >= len(lines):
            break
        here = _indent(lines[i])
        if here < indent:
            break
        if here > indent:
            raise YamlSubsetError(f"unexpected indentation on line {i + 1}")
        body = _strip_comment(lines[i].strip())
        if body == "-" or body.startswith("- "):
            break
        pair = _split_key(body)
        if pair is None:
            raise YamlSubsetError(f"line {i + 1} is not a mapping entry: {body!r}")
        key, rest = pair
        i += 1
        if rest in _BLOCK_SCALAR:
            out[key], i = _parse_block_scalar(
                lines, i, indent, folded=rest.startswith(">")
            )
            continue
        if rest:
            value = _scalar(rest)
            # A quoted or flow value is complete where it starts. Only a plain
            # one can continue on the next line.
            if isinstance(value, str) and rest[0] not in "\"'[{":
                value, i = _continue_plain(lines, i, indent, value)
            out[key] = value
            continue
        child = _skip_blank(lines, i)
        if child >= len(lines):
            out[key] = None
            i = child
            break
        depth = _indent(lines[child])
        if depth > indent:
            out[key], i = _parse_block(lines, child, depth)
        elif depth == indent and lines[child].lstrip(" ").startswith("-"):
            out[key], i = _parse_sequence(lines, child, indent)
        else:
            out[key] = None
    return out, i


def _parse_sequence(lines: list[str], i: int, indent: int) -> tuple[list, int]:
    out: list[Any] = []
    while True:
        i = _skip_blank(lines, i)
        if i >= len(lines) or _indent(lines[i]) != indent:
            break
        stripped = _strip_comment(lines[i].lstrip(" "))
        if stripped != "-" and not stripped.startswith("- "):
            break
        content = stripped[1:].lstrip(" ")
        if not content:
            child = _skip_blank(lines, i + 1)
            if child < len(lines) and _indent(lines[child]) > indent:
                value, i = _parse_block(lines, child, _indent(lines[child]))
            else:
                value, i = None, i + 1
            out.append(value)
            continue
        column = indent + 1 + (len(stripped) - 1 - len(content))
        if _split_key(content) is None:
            out.append(_scalar(content))
            i += 1
            continue
        # The dash is rewritten as whitespace so the rest of the item is an
        # ordinary mapping starting in the column the dash pointed at.
        lines[i] = " " * column + content
        value, i = _parse_mapping(lines, i, column)
        out.append(value)
    return out, i


def _parse_block(lines: list[str], i: int, indent: int) -> tuple[Any, int]:
    i = _skip_blank(lines, i)
    if i >= len(lines):
        return None, i
    stripped = lines[i].lstrip(" ")
    if stripped == "-" or stripped.startswith("- "):
        return _parse_sequence(lines, i, indent)
    return _parse_mapping(lines, i, indent)


def parse_yaml_subset(text: str) -> Any:
    lines = _prepare(text)
    start = _skip_blank(lines, 0)
    if start >= len(lines):
        return None
    value, _ = _parse_block(lines, start, _indent(lines[start]))
    return value


def _mapping(node: Any) -> dict:
    return node if isinstance(node, dict) else {}


def _sequence(node: Any) -> list:
    return node if isinstance(node, list) else []


def _flatten(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(_flatten(item) for item in node)
    if isinstance(node, dict):
        return "\n".join(f"{k}: {_flatten(v)}" for k, v in node.items())
    return str(node)


def _values_for_key(node: Any, key: str) -> list[Any]:
    out: list[Any] = []
    if isinstance(node, dict):
        for k, value in node.items():
            if _norm(str(k)) == _norm(key):
                out.append(value)
            out.extend(_values_for_key(value, key))
    elif isinstance(node, list):
        for item in node:
            out.extend(_values_for_key(item, key))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# --------------------------------------------------------------------------
# The verdicts, the findings and the arithmetic
# --------------------------------------------------------------------------


class Verdict(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SUSPECTED = "suspected"
    NOT_APPLICABLE = "not_applicable"
    UNDETERMINED = "undetermined"
    NEEDS_JUDGEMENT = "needs_judgement"
    NOT_CHECKABLE = "not_checkable"

    @property
    def settles_the_rule(self) -> bool:
        """Whether a reader can stop here.

        `suspected` deliberately does not settle anything. A pattern with known
        false positives has raised a hand, which is not the same as an answer.
        """
        return self in (Verdict.PASSED, Verdict.FAILED, Verdict.NOT_APPLICABLE)


_DEFERRED = (Verdict.NEEDS_JUDGEMENT, Verdict.NOT_CHECKABLE)


@dataclass(frozen=True)
class Finding:
    """One rule, one answer, and the evidence for it.

    A finding that does not say what it looked at cannot be acted on: the reader
    has to be able to open the same file and disagree. So the three evidence
    fields are validated at construction, in the same place and for the same
    reason `Response` validates that a refusal carries a reason.
    """

    rule_id: str
    severity: str
    verdict: Verdict
    looked_for: str
    looked_at: tuple[str, ...]
    found: str
    limitation: str = ""

    def __post_init__(self) -> None:
        if not self.looked_for.strip():
            raise ValueError(f"{self.rule_id}: a finding must say what it looked for")
        if not self.found.strip():
            raise ValueError(f"{self.rule_id}: a finding must say what it found")
        if not self.looked_at and self.verdict not in _DEFERRED:
            raise ValueError(
                f"{self.rule_id}: a {self.verdict.value} finding must say what it "
                f"looked at"
            )

    @property
    def needs_attention(self) -> bool:
        """A silent pass is the only outcome a reader can skip, and only when the
        pass means what it looks like it means."""
        return self.verdict is not Verdict.PASSED or bool(self.limitation)

    def line(self) -> str:
        where = ", ".join(self.looked_at) if self.looked_at else "nothing was opened"
        text = (
            f"[{self.severity}] {self.rule_id}: {self.verdict.value}. "
            f"Looked for {self.looked_for}. Looked at {where}. Found {self.found}."
        )
        if self.limitation:
            text += f" Limit of this check: {self.limitation}"
        return text

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule_id,
            "severity": self.severity,
            "verdict": self.verdict.value,
            "looked_for": self.looked_for,
            "looked_at": list(self.looked_at),
            "found": self.found,
            "limitation": self.limitation,
        }


@dataclass(frozen=True)
class Summary:
    rules: int
    checked: int
    passed: int
    failed: int
    suspected: int
    not_applicable: int
    undetermined: int
    needs_judgement: int
    not_checkable: int

    @property
    def could_not_be_determined(self) -> int:
        return (
            self.suspected
            + self.undetermined
            + self.needs_judgement
            + self.not_checkable
        )

    def one_line(self) -> str:
        return (
            f"{self.rules} rules, {self.checked} checked here: {self.passed} passed, "
            f"{self.failed} failed, {self.not_applicable} not applicable, "
            f"{self.could_not_be_determined} could not be determined "
            f"({self.suspected} suspected, {self.undetermined} undetermined, "
            f"{self.needs_judgement} need a reader, {self.not_checkable} leave no "
            f"trace in a repository)"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rules": self.rules,
            "checked": self.checked,
            "passed": self.passed,
            "failed": self.failed,
            "suspected": self.suspected,
            "not_applicable": self.not_applicable,
            "undetermined": self.undetermined,
            "needs_judgement": self.needs_judgement,
            "not_checkable": self.not_checkable,
            "could_not_be_determined": self.could_not_be_determined,
        }


def summarise(results: Iterable[Finding]) -> Summary:
    """Count by verdict and never by subtraction.

    Deriving passes as "everything that did not produce a finding" is how an
    unreadable file becomes a clean bill of health, so every count here comes
    from a verdict that something actually recorded.
    """
    results = list(results)
    tally = {v: 0 for v in Verdict}
    for result in results:
        tally[result.verdict] += 1
    deferred = tally[Verdict.NEEDS_JUDGEMENT] + tally[Verdict.NOT_CHECKABLE]
    return Summary(
        rules=len(results),
        checked=len(results) - deferred,
        passed=tally[Verdict.PASSED],
        failed=tally[Verdict.FAILED],
        suspected=tally[Verdict.SUSPECTED],
        not_applicable=tally[Verdict.NOT_APPLICABLE],
        undetermined=tally[Verdict.UNDETERMINED],
        needs_judgement=tally[Verdict.NEEDS_JUDGEMENT],
        not_checkable=tally[Verdict.NOT_CHECKABLE],
    )


# --------------------------------------------------------------------------
# Bounded reading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _File:
    path: str
    body: Optional[str]
    truncated: bool = False
    error: str = ""

    @property
    def readable(self) -> bool:
        return not self.error

    @property
    def text(self) -> str:
        """Raises rather than returning empty.

        An unreadable file that reads as `""` turns every negative check into a
        pass: no gitleaks invocation found, no committed secret found, no
        observability call found. That is the exact failure this module exists
        to avoid, so a check that forgets to test `readable` breaks loudly and
        lands as `undetermined`.
        """
        if not self.readable:
            raise Unreadable(f"{self.path} could not be read: {self.error}")
        return self.body or ""


class _Reader:
    """Every read the audit makes, bounded and attributed to a rule."""

    def __init__(
        self,
        corpus: Corpus,
        budget: int = MAX_FILES_PER_AUDIT,
        cap: int = MAX_BYTES_PER_FILE,
    ) -> None:
        self._corpus = corpus
        self._budget = budget
        self._cap = cap
        self._cache: dict[str, _File] = {}
        self._paths: Optional[list[str]] = None
        self._touched: dict[str, list[str]] = {}
        self.current = ""
        self.denied = 0

    def paths(self) -> list[str]:
        if self._paths is None:
            self._paths = list(self._corpus.paths())
        return self._paths

    def _touch(self, path: str) -> None:
        seen = self._touched.setdefault(self.current, [])
        if path not in seen:
            seen.append(path)

    def exists(self, path: str) -> bool:
        self._touch(path)
        return path in self.paths()

    def read(self, path: str) -> _File:
        self._touch(path)
        if path in self._cache:
            return self._cache[path]
        if path not in self.paths():
            result = _File(path, None, error="no such file")
        elif len(self._cache) >= self._budget:
            result = _File(
                path,
                None,
                error=f"the audit read budget of {self._budget} files is spent",
            )
        else:
            try:
                body = self._corpus.read(path)
            except Exception as exc:  # noqa: BLE001
                result = _File(path, None, error=f"{type(exc).__name__}: {exc}")
            else:
                result = _File(path, body[: self._cap], truncated=len(body) > self._cap)
        if not result.readable:
            self.denied += 1
        self._cache[path] = result
        return result

    def touched(self, rule_id: str) -> tuple[str, ...]:
        return tuple(self._touched.get(rule_id, ()))

    def truncated_for(self, rule_id: str) -> tuple[str, ...]:
        return tuple(
            p
            for p in self.touched(rule_id)
            if p in self._cache and self._cache[p].truncated
        )

    @property
    def opened(self) -> list[str]:
        return sorted(p for p, f in self._cache.items() if f.readable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reads": len(self.opened),
            "denied": self.denied,
            "budget": self._budget,
            "budget_spent": len(self._cache) >= self._budget,
        }


# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    id: str
    section: str
    severity: str
    statement: str
    looked_for: str
    how: str


@dataclass(frozen=True)
class Check:
    rule: Rule
    run: Callable[["_Reader", Rule], Optional[Finding]]


def _finding(
    reader: _Reader,
    rule: Rule,
    verdict: Verdict,
    found: str,
    looked_for: str = "",
    looked_at: Optional[Iterable[str]] = None,
    limitation: str = "",
) -> Finding:
    paths = tuple(looked_at) if looked_at is not None else reader.touched(rule.id)
    return Finding(
        rule_id=rule.id,
        severity=rule.severity,
        verdict=verdict,
        looked_for=looked_for or rule.looked_for,
        looked_at=paths or (LISTING,),
        found=found,
        limitation=limitation,
    )


# --------------------------------------------------------------------------
# Pipeline discovery, shared by four checks
# --------------------------------------------------------------------------

ADO_PIPELINES = ("azure-pipelines.yml", "azure-pipelines.yaml")
_ACTIONS_WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
UNMODELLED_PIPELINES = (
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    "Jenkinsfile",
    "bitbucket-pipelines.yml",
    ".circleci/config.yml",
)


@dataclass(frozen=True)
class _Pipelines:
    ado: tuple[str, ...]
    actions: tuple[str, ...]
    unmodelled: tuple[str, ...]

    @property
    def modelled(self) -> tuple[str, ...]:
        return self.ado + self.actions

    @property
    def any_at_all(self) -> bool:
        return bool(self.modelled or self.unmodelled)


def _pipelines(reader: _Reader) -> _Pipelines:
    paths = reader.paths()
    return _Pipelines(
        ado=tuple(p for p in paths if p in ADO_PIPELINES),
        actions=tuple(p for p in paths if _ACTIONS_WORKFLOW.match(p)),
        unmodelled=tuple(p for p in paths if p in UNMODELLED_PIPELINES),
    )


def _parsed(reader: _Reader, path: str) -> tuple[Any, str]:
    """The document, or the reason there is no document to work with."""
    handle = reader.read(path)
    if not handle.readable:
        return None, f"{path} could not be read: {handle.error}"
    try:
        return parse_yaml_subset(handle.text), ""
    except YamlSubsetError as exc:
        return None, f"{path} could not be parsed: {exc}"


def _stage_name(entry: Any) -> str:
    if isinstance(entry, dict):
        for key in ("stage", "name", "displayName", "template"):
            if entry.get(key):
                return str(entry[key])
        return "<unnamed stage>"
    return str(entry)


def _is_scan(name: str, node: Any) -> bool:
    return "secretscan" in _norm(name) or "secretscan" in _norm(
        _mapping(node).get("name", "")
    )


_GATED_WORK = ("build", "test")


def _is_gated_work(name: str, node: Any) -> bool:
    labels = _norm(name) + _norm(_mapping(node).get("name", ""))
    return any(word in labels for word in _GATED_WORK)


def _needs(job: Any) -> list[str]:
    value = _mapping(job).get("needs")
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _upstream(jobs: dict[str, Any], start: str) -> set[str]:
    seen: set[str] = set()
    stack = list(_needs(jobs.get(start)))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(_needs(jobs.get(current)))
    return seen


def _scan_subtrees(reader: _Reader) -> tuple[list[tuple[str, Any]], list[str]]:
    """Every SecretScan stage or secret scan job in the tree, and what stopped
    the search where it was stopped."""
    found: list[tuple[str, Any]] = []
    problems: list[str] = []
    pipes = _pipelines(reader)
    for path in pipes.ado:
        doc, error = _parsed(reader, path)
        if error:
            problems.append(error)
            continue
        for stage in _sequence(_mapping(doc).get("stages")):
            if _is_scan(_stage_name(stage), stage):
                found.append((path, stage))
    for path in pipes.actions:
        doc, error = _parsed(reader, path)
        if error:
            problems.append(error)
            continue
        for job_id, job in _mapping(_mapping(doc).get("jobs")).items():
            if _is_scan(job_id, job):
                found.append((path, job))
    return found, problems


# --------------------------------------------------------------------------
# Section 3, the secret scan
# --------------------------------------------------------------------------


def _check_secret_scan_first(reader: _Reader, rule: Rule) -> Optional[Finding]:
    pipes = _pipelines(reader)
    if not pipes.any_at_all:
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            "no pipeline definition at all: neither azure-pipelines.yml, nor any "
            ".github/workflows/*.yml, nor a recognised equivalent is in the tree",
            looked_at=(LISTING,),
        )
    if not pipes.modelled:
        return _finding(
            reader,
            rule,
            Verdict.UNDETERMINED,
            f"the only pipeline definition is {pipes.unmodelled[0]}, and this audit "
            f"models the Azure DevOps and GitHub Actions stage graphs only",
            looked_at=pipes.unmodelled,
        )

    problems: list[str] = []
    scan_anywhere = False

    for path in pipes.ado:
        doc, error = _parsed(reader, path)
        if error:
            return _finding(reader, rule, Verdict.UNDETERMINED, error)
        stages = _sequence(_mapping(doc).get("stages"))
        if not stages:
            problems.append(f"{path} declares no stages: block")
            continue
        names = [_stage_name(s) for s in stages]
        if _is_scan(names[0], stages[0]):
            scan_anywhere = True
        else:
            positions = [i for i, n in enumerate(names) if _is_scan(n, stages[i])]
            if positions:
                scan_anywhere = True
                problems.append(
                    f"the first stage in {path} is {names[0]!r} and SecretScan is at "
                    f"position {positions[0] + 1} of {len(stages)}"
                )
            else:
                problems.append(
                    f"{path} has no SecretScan stage; its stages are "
                    f"{', '.join(names)}"
                )
        for index, stage in enumerate(stages):
            if index == 0 or _is_scan(names[index], stage):
                continue
            if _mapping(stage).get("dependsOn") == []:
                problems.append(
                    f"stage {names[index]!r} in {path} declares an empty dependsOn, "
                    f"so it starts beside the scan rather than after it"
                )

    for path in pipes.actions:
        doc, error = _parsed(reader, path)
        if error:
            return _finding(reader, rule, Verdict.UNDETERMINED, error)
        jobs = {str(k): v for k, v in _mapping(_mapping(doc).get("jobs")).items()}
        scans = [j for j in jobs if _is_scan(j, jobs[j])]
        work = [j for j in jobs if j not in scans and _is_gated_work(j, jobs[j])]
        if not scans:
            if work:
                problems.append(
                    f"{path} runs {', '.join(work)} with no secret scan job in the "
                    f"same workflow"
                )
            continue
        scan_anywhere = True
        for scan in scans:
            upstream = _needs(jobs[scan])
            if upstream:
                problems.append(
                    f"the scan job {scan!r} in {path} waits on "
                    f"{', '.join(upstream)}, so it is not first"
                )
        for job in work:
            if not _upstream(jobs, job) & set(scans):
                problems.append(
                    f"job {job!r} in {path} does not depend on {scans[0]!r}, so it "
                    f"runs beside the secret scan rather than after it"
                )

    if not scan_anywhere:
        problems.append("no SecretScan stage or secret scan job exists in any pipeline")
    if problems:
        return _finding(reader, rule, Verdict.FAILED, "; ".join(problems))
    return None


def _check_gitleaks_gate(reader: _Reader, rule: Rule) -> Optional[Finding]:
    subtrees, problems = _scan_subtrees(reader)
    if not subtrees:
        return _finding(
            reader,
            rule,
            Verdict.UNDETERMINED,
            "no SecretScan stage or secret scan job could be located, so there were "
            "no steps to read"
            + (f" ({'; '.join(problems)})" if problems else ""),
        )
    missing: list[str] = []
    for path, node in subtrees:
        text = _flatten(node)
        lowered = text.lower()
        if "gitleaks" not in lowered:
            missing.append(f"the scan in {path} never invokes gitleaks")
            continue
        if "8.18.4" not in text:
            missing.append(
                f"gitleaks in {path} is not pinned to v8.18.4; no such version "
                f"appears in the installer input, the container tag or the "
                f"download URL"
            )
        # Asked as "is the gate disabled", not "is the flag present". gitleaks
        # already exits non-zero when it finds something, so demanding an
        # explicit --exit-code 1 reports a repository that is correctly gated.
        # A compliance tool that asks for redundant incantations gets muted, and
        # a muted tool catches nothing.
        if re.search(r"--exit-code[ =]+0\b", text):
            missing.append(
                f"gitleaks in {path} is run with --exit-code 0, so a leak does "
                f"not fail the build"
            )
        # Both spellings. `continueOnError` is Azure Pipelines and
        # `continue-on-error` is GitHub Actions, and checking only the first
        # meant this could never fire on a GitHub repository.
        flags = _values_for_key(node, "continueOnError") + _values_for_key(
            node, "continue-on-error"
        )
        if any(str(flag).strip().lower() == "true" for flag in flags):
            missing.append(
                f"the scan in {path} is allowed to continue on error, which "
                f"turns the hard gate into advice"
            )
    if missing:
        return _finding(reader, rule, Verdict.FAILED, "; ".join(missing))
    return None


# --------------------------------------------------------------------------
# Section 9, secrets and config keys
# --------------------------------------------------------------------------

_ENV_LIVE = re.compile(
    r"(^|/)\.env(\.(local|dev|development|qa|test|staging|prod|production))?$"
)
_ENV_SAMPLE = re.compile(r"(^|/)\.env\.(example|sample|template|dist)$")
_APPSETTINGS = re.compile(r"(^|/)(appsettings[\w.]*\.json|local\.settings\.json)$")
_SECRET_NAMED_FILE = re.compile(r"(key|secret|token|password|credential)", re.I)
_SECRET_KEY = re.compile(
    r"(password|passwd|pwd|secret|token|apikey|api_key|accesskey|access_key"
    r"|privatekey|private_key|connectionstring|connection_string|clientsecret"
    r"|client_secret|sas)",
    re.I,
)
# Keys that name a secret rather than hold one. `secret_id` in a Terraform
# `google_secret_manager_secret` is the identifier the value will later be
# stored under, and reporting it as a committed credential is both wrong and
# corrosive: it asks the reader to replace a name with a reference to itself.
# The whole point of the resource is that the value is not in the file.
_SECRET_REFERENCE_KEY = re.compile(
    r"(^|[._-])(id|ids|name|names|arn|uri|url|ref|reference|path|key_?vault"
    r"|version|alias|label|prefix|namespace)$",
    re.I,
)
_SOURCE_SUFFIX = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".cs",
    ".go",
    ".rb",
    ".php",
    ".sh",
    ".ps1",
    ".yml",
    ".yaml",
    ".json",
    ".tf",
    ".bicep",
    ".env",
    ".ini",
    ".cfg",
    ".properties",
)
_VENDORED = ("node_modules/", "vendor/", ".venv/", "site-packages/", "dist/", "build/")

_ENV_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][\w.]*)\s*=\s*(.*)$")
_JSON_ASSIGN = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')
_PLACEHOLDER_WORD = re.compile(
    r"^(changeme|change_me|placeholder|example|sample|dummy|todo|tbd|none|null|"
    r"redacted|replace|replaceme|your\w*|xxx+|\*+|-+|\.\.\.)$",
    re.I,
)
_REFERENCE = re.compile(
    r"^(\$\{?[\w.]+\}?|<[^>]*>|\{\{[^}]*\}\}|@Microsoft\.KeyVault\(|!\w+\s|%\w+%)"
)
_CREDENTIAL_SHAPE = (
    (re.compile(r"AKIA[0-9A-Z]{16}"), "an AWS access key id"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "a GitHub personal access token"),
    (re.compile(r"xox[bapr]-[0-9A-Za-z-]{10,}"), "a Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key block"),
)
_IAC_TEMPLATE = re.compile(
    r"(^|/)(template\.ya?ml|.*\.bicep|azuredeploy\.json|.*\.tf)$", re.I
)


def _vendored(path: str) -> bool:
    return any(marker in path for marker in _VENDORED)


def _redact(value: str) -> str:
    return f"<{len(value)} characters, redacted>"


def _is_placeholder(value: str) -> bool:
    text = value.strip().strip("\"'").strip()
    if len(text) < 8:
        return True
    if _PLACEHOLDER_WORD.match(text) or _REFERENCE.match(text):
        return True
    return len(set(text)) <= 2


def _assignments(path: str, text: str) -> list[tuple[str, str]]:
    if path.endswith(".json"):
        return [(k, v) for k, v in _JSON_ASSIGN.findall(text)]
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _ENV_ASSIGN.match(line)
        if match:
            out.append((match.group(1), match.group(2).strip()))
    return out


def _check_secrets_never_committed(reader: _Reader, rule: Rule) -> Optional[Finding]:
    paths = [p for p in reader.paths() if not _vendored(p)]
    candidates = [
        p
        for p in paths
        if _ENV_LIVE.search(p)
        or _APPSETTINGS.search(p)
        or (
            _SECRET_NAMED_FILE.search(p.rsplit("/", 1)[-1])
            and p.endswith(_SOURCE_SUFFIX)
        )
    ]
    templates = [p for p in paths if _IAC_TEMPLATE.search(p)]
    if not candidates and not templates:
        return _finding(
            reader,
            rule,
            Verdict.PASSED,
            "no .env, appsettings, local.settings.json, secret-named source file or "
            "IaC template exists in this tree, so there was nothing to open",
            looked_at=(LISTING,),
            limitation=(
                "a hardcoded secret in a file shape this sweep does not search would "
                "not have been seen"
            ),
        )

    # Split by strength of evidence, because the two are not the same claim.
    # A credential shape in the text means the thing itself was seen. A
    # secret-shaped key name assigned to something means a pattern matched, and
    # the commonest match is a test fixture. Reporting the second as critical
    # failure alongside the first is how a report stops being read.
    committed: list[str] = []
    resembling: list[str] = []
    unresolved: list[str] = []
    quiet_env: list[str] = []

    for path in candidates:
        handle = reader.read(path)
        if not handle.readable:
            return _finding(
                reader,
                rule,
                Verdict.UNDETERMINED,
                f"{path} matched the search but {handle.error}",
            )
        text = handle.text
        seen: list[str] = []
        for pattern, what in _CREDENTIAL_SHAPE:
            if pattern.search(text):
                seen.append(f"{path} contains {what}")
        resembles: list[str] = []
        for key, value in _assignments(path, text):
            if _SECRET_REFERENCE_KEY.search(key):
                continue
            if _SECRET_KEY.search(key) and not _is_placeholder(value):
                resembles.append(f"{path} sets {key} to {_redact(value.strip())}")
        committed.extend(seen)
        resembling.extend(resembles)
        if not seen and not resembles:
            if _ENV_LIVE.search(path) and not _ENV_SAMPLE.search(path):
                quiet_env.append(path)

    for path in templates:
        handle = reader.read(path)
        if not handle.readable:
            return _finding(
                reader,
                rule,
                Verdict.UNDETERMINED,
                f"{path} is an IaC template and {handle.error}",
            )
        for key, value in _template_settings(path, handle.text):
            if not _SECRET_KEY.search(key) or _SECRET_REFERENCE_KEY.search(key):
                continue
            if _SSM_REF.search(value) or "@Microsoft.KeyVault(" in value:
                continue
            if _is_placeholder(value):
                continue
            unresolved.append(
                f"{path} gives {key} the inline value {_redact(value)} rather than a "
                f"{{{{resolve:ssm:...}}}} or @Microsoft.KeyVault(...) reference"
            )

    # Evidence decides the verdict; nothing found is ever dropped. A confirmed
    # sighting makes this a failure and the weaker matches are still reported
    # under it, because the reader fixing the first one is the reader who should
    # see the rest.
    confirmed = committed + unresolved
    if confirmed:
        detail = "; ".join(confirmed)
        if resembling:
            detail += ". Also, on the weaker evidence of a key name: " + "; ".join(
                resembling
            )
        return _finding(reader, rule, Verdict.FAILED, detail)
    if resembling:
        return _finding(
            reader,
            rule,
            Verdict.SUSPECTED,
            "; ".join(resembling)
            + ". The key names look like credentials and the values are not "
            "placeholders, but nothing matched the shape of a real one, so this "
            "is a pattern and not a sighting",
        )
    if quiet_env:
        return _finding(
            reader,
            rule,
            Verdict.SUSPECTED,
            f"{', '.join(quiet_env)} is committed and is not a .example file. No "
            f"value in it matched a secret shape, which is not the same as it "
            f"holding none",
        )
    return None


# --------------------------------------------------------------------------
# Config key naming
# --------------------------------------------------------------------------

_SSM_REF = re.compile(r"\{\{resolve:(?:ssm|ssm-secure|secretsmanager):([^:}]+)")
_KV_REF = re.compile(r"@Microsoft\.KeyVault\(([^)]*)\)")
_KV_SECRET_NAME = re.compile(r"SecretName\s*=\s*([\w-]+)")
_KV_SECRET_URI = re.compile(r"SecretUri\s*=\s*https?://[^/]+/secrets/([\w-]+)")

# Names the platform owns. The standard is about the product's own configuration
# namespace, and demanding a hierarchical path for PATH or for the AWS runtime's
# own switch would be inventing a rule the document does not contain.
PLATFORM_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "PORT",
        "HOSTNAME",
        "TZ",
        "CI",
        "NODE_ENV",
        "PYTHONPATH",
        "LOG_LEVEL",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_NODEJS_CONNECTION_REUSE_ENABLED",
        "ASPNETCORE_ENVIRONMENT",
        "DOTNET_ENVIRONMENT",
        "FUNCTIONS_WORKER_RUNTIME",
        "AzureWebJobsStorage",
        "WEBSITE_RUN_FROM_PACKAGE",
        "AllowedHosts",
    }
)
_FRAMEWORK_PREFIX = ("Logging:", "Serilog:", "IsEncrypted")

_STAGES = ("dev", "qa", "prod")
_FORM_SSM = re.compile(
    r"^/[A-Za-z0-9][\w.-]*/(dev|qa|prod)/settings/[\w.-]+/.+$", re.I
)
_FORM_KEYVAULT = re.compile(r"^[a-z0-9]+-(dev|qa|prod)-[a-z0-9]+-[a-z0-9-]+$", re.I)
_FORM_APPCONFIG = re.compile(r"^[A-Za-z0-9]+:(dev|qa|prod):[A-Za-z0-9]+:.+$", re.I)
_FORM_ENVVAR = re.compile(r"^[A-Z0-9]+__(DEV|QA|PROD)__[A-Z0-9]+__.+$")
_STAGE_IN_VALUE = re.compile(r"[-_.](dev|qa|prod|production|development)([-_./]|$)", re.I)
_STAGE_SCOPED_FILE = re.compile(
    r"(^|/|\.)(dev|development|qa|test|uat|staging|prod|production)(\.|/|$)", re.I
)


@dataclass(frozen=True)
class _Key:
    name: str
    value: str
    path: str


def _template_settings(path: str, text: str) -> list[tuple[str, str]]:
    """Key and value pairs out of an IaC template, without resolving anything.

    `!Ref` and `!Sub` are left as the text they are. This audit is about what the
    template says, and a reader who wants to know what it evaluates to has to run
    the deployment, not this.
    """
    if path.endswith(".json"):
        return _assignments(path, text)
    if path.endswith((".bicep", ".tf")):
        pairs: list[tuple[str, str]] = []
        for line in text.splitlines():
            match = re.match(r"\s*(?:param|var)?\s*([\w.-]+)\s*[:=]\s*(.+?)\s*$", line)
            if match:
                pairs.append((match.group(1), match.group(2).strip("'\"")))
        return pairs
    try:
        doc = parse_yaml_subset(text)
    except YamlSubsetError:
        return _assignments(path, text)
    pairs = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    pairs.append((str(key), value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return pairs


def _flatten_json(node: Any, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            out.extend(_flatten_json(value, f"{prefix}{key}:"))
    elif isinstance(node, list):
        for item in node:
            out.extend(_flatten_json(item, prefix))
    else:
        out.append((prefix.rstrip(":"), "" if node is None else str(node)))
    return out


def _config_keys(reader: _Reader) -> tuple[list[_Key], list[str]]:
    """Every configuration key this audit can see, and what it could not read."""
    keys: list[_Key] = []
    problems: list[str] = []
    paths = [p for p in reader.paths() if not _vendored(p)]

    for path in paths:
        if _ENV_SAMPLE.search(path) or _ENV_LIVE.search(path):
            handle = reader.read(path)
            if not handle.readable:
                problems.append(f"{path}: {handle.error}")
                continue
            for name, value in _assignments(path, handle.text):
                keys.append(_Key(name, value, path))
        elif _APPSETTINGS.search(path):
            handle = reader.read(path)
            if not handle.readable:
                problems.append(f"{path}: {handle.error}")
                continue
            try:
                doc = json.loads(handle.text)
            except ValueError as exc:
                problems.append(f"{path} is not valid JSON: {exc}")
                continue
            for name, value in _flatten_json(doc):
                keys.append(_Key(name, value, path))
        elif _IAC_TEMPLATE.search(path):
            handle = reader.read(path)
            if not handle.readable:
                problems.append(f"{path}: {handle.error}")
                continue
            text = handle.text
            for ref in _SSM_REF.findall(text):
                keys.append(_Key(ref.strip(), "", path))
            for ref in _KV_REF.findall(text):
                name = _KV_SECRET_NAME.search(ref) or _KV_SECRET_URI.search(ref)
                if name:
                    keys.append(_Key(name.group(1), "", path))

    return [
        k
        for k in keys
        if k.name
        and k.name not in PLATFORM_KEYS
        and not k.name.startswith(_FRAMEWORK_PREFIX)
    ], problems


def _has_stage_segment(name: str) -> bool:
    segments = re.split(r"[/:_.-]+", name)
    return any(seg.lower() in _STAGES for seg in segments)


def _hierarchical(name: str) -> bool:
    return bool(
        _FORM_SSM.match(name)
        or _FORM_KEYVAULT.match(name)
        or _FORM_APPCONFIG.match(name)
        or _FORM_ENVVAR.match(name)
    )


def _why_not_hierarchical(name: str) -> str:
    if not _has_stage_segment(name):
        return "no stage segment"
    if len(re.split(r"[/:]|__", name.strip("/"))) < 4:
        return "no service segment"
    return "the segments are not in the order the convention requires"


def _check_key_hierarchy(reader: _Reader, rule: Rule) -> Optional[Finding]:
    keys, problems = _config_keys(reader)
    if problems and not keys:
        return _finding(reader, rule, Verdict.UNDETERMINED, "; ".join(problems))
    if not keys:
        return _finding(
            reader,
            rule,
            Verdict.NOT_APPLICABLE,
            "no .env.example, appsettings*.json or IaC parameter reference exists in "
            "this tree, so there are no configuration keys to name",
            looked_at=(LISTING,),
        )
    bad = [k for k in keys if not _hierarchical(k.name)]
    if bad:
        shown = ", ".join(
            f"{k.name!r} in {k.path} ({_why_not_hierarchical(k.name)})" for k in bad[:8]
        )
        more = f" and {len(bad) - 8} more" if len(bad) > 8 else ""
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            f"{len(bad)} of {len(keys)} keys are outside the convention: {shown}{more}",
        )
    return None


def _check_stage_segment(reader: _Reader, rule: Rule) -> Optional[Finding]:
    keys, problems = _config_keys(reader)
    if problems and not keys:
        return _finding(reader, rule, Verdict.UNDETERMINED, "; ".join(problems))
    if not keys:
        return _finding(
            reader,
            rule,
            Verdict.NOT_APPLICABLE,
            "no configuration key was found in .env.example, appsettings*.json or an "
            "IaC parameter file",
            looked_at=(LISTING,),
        )
    missing = [
        k
        for k in keys
        if not _has_stage_segment(k.name) and not _STAGE_SCOPED_FILE.search(k.path)
    ]
    if missing:
        shown = ", ".join(f"{k.name!r} in {k.path}" for k in missing[:8])
        more = f" and {len(missing) - 8} more" if len(missing) > 8 else ""
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            f"{len(missing)} keys carry no stage segment: {shown}{more}",
        )
    baked = [
        k
        for k in keys
        if not _has_stage_segment(k.name) and _STAGE_IN_VALUE.search(k.value or "")
    ]
    if baked:
        shown = ", ".join(f"{k.name!r} in {k.path}" for k in baked[:8])
        return _finding(
            reader,
            rule,
            Verdict.SUSPECTED,
            f"the stage looks baked into the value of {shown}, under a key with no "
            f"stage segment. The file is itself stage scoped, which is the known "
            f"false positive for this pattern, so this is reported and not failed",
        )
    return None


# --------------------------------------------------------------------------
# Section 6, the specification
# --------------------------------------------------------------------------

SPEC_NAMES = (
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    "swagger-definition.yaml",
    "swagger-definition.yml",
    "swagger.yaml",
    "swagger.yml",
    "swagger.json",
)
_HTTP_MARKERS = (
    (re.compile(r"\bFastAPI\s*\("), "a FastAPI application"),
    (re.compile(r"\bAPIRouter\s*\("), "a FastAPI router"),
    (re.compile(r"\bFlask\s*\("), "a Flask application"),
    (re.compile(r"@app\.(?:route|get|post|put|delete|patch)\b"), "an app route"),
    (re.compile(r"@router\.(?:get|post|put|delete|patch)\b"), "a router route"),
    (re.compile(r"\bexpress\s*\(\s*\)"), "an Express application"),
    (re.compile(r"\bRouter\s*\(\s*\)"), "an Express router"),
    (re.compile(r"@RestController\b"), "a Spring REST controller"),
    (re.compile(r"\[ApiController\]"), "an ASP.NET API controller"),
    (re.compile(r"\bMapControllers\s*\("), "ASP.NET controller mapping"),
    (re.compile(r"\bhttp\.HandleFunc\s*\("), "a Go HTTP handler registration"),
    (re.compile(r"\bhttpTrigger\b"), "an Azure Functions HTTP trigger"),
)
_PROBE_SUFFIX = (".py", ".js", ".ts", ".tsx", ".java", ".cs", ".go", ".json")


def _http_surface(reader: _Reader) -> tuple[Optional[tuple[str, str]], bool]:
    """(where the HTTP surface is, whether the probe ran out before deciding)."""
    probed = 0
    for path in reader.paths():
        if _vendored(path) or not path.endswith(_PROBE_SUFFIX):
            continue
        if path.endswith(".json") and not path.endswith("function.json"):
            continue
        if probed >= MAX_ROUTER_PROBE:
            return None, True
        handle = reader.read(path)
        probed += 1
        if not handle.readable:
            continue
        for pattern, what in _HTTP_MARKERS:
            if pattern.search(handle.text):
                return (path, what), False
    return None, False


def _check_openapi_at_root(reader: _Reader, rule: Rule) -> Optional[Finding]:
    spec = [p for p in reader.paths() if "/" not in p and p in SPEC_NAMES]
    if spec:
        return None
    surface, exhausted = _http_surface(reader)
    if surface:
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            f"{surface[1]} in {surface[0]}, and none of {', '.join(SPEC_NAMES[:4])} "
            f"is at the repository root",
        )
    if exhausted:
        return _finding(
            reader,
            rule,
            Verdict.UNDETERMINED,
            f"no specification at the root, and the router probe stopped after "
            f"{MAX_ROUTER_PROBE} files without deciding whether this repository "
            f"exposes HTTP endpoints",
        )
    return _finding(
        reader,
        rule,
        Verdict.NOT_APPLICABLE,
        "no FastAPI, Flask, Express, Spring, ASP.NET, Go or Azure Functions HTTP "
        "surface was found, so this repository is a library or a worker and the rule "
        "does not bite",
    )


# --------------------------------------------------------------------------
# Section 2, the decision record
# --------------------------------------------------------------------------

CLAUDE_MD = "CLAUDE.md"
_ADR_HEADING = re.compile(r"(?m)^(#{1,6})[^\n]*\bADR[-\s]?(\d{2,4})\b[^\n]*$")
_ADR_SECTION = re.compile(r"(?i)architecture\s+decision\s+record")
_ANY_HEADING = re.compile(r"(?m)^#{1,6}\s")
_ADR_FIELDS = ("Date", "Status", "Decision", "Reason", "Consequence")
ADR_STATUSES = ("proposed", "implemented", "superseded")


def _field_pattern(name: str) -> re.Pattern:
    return re.compile(rf"\*{{0,2}}\s*{name}\s*\*{{0,2}}\s*:", re.I)


def _claude_md(reader: _Reader, rule: Rule) -> tuple[str, Optional[Finding]]:
    if not reader.exists(CLAUDE_MD):
        return "", _finding(
            reader,
            rule,
            Verdict.FAILED,
            f"there is no {CLAUDE_MD} at the repository root",
            looked_at=(LISTING,),
        )
    handle = reader.read(CLAUDE_MD)
    if not handle.readable:
        return "", _finding(
            reader, rule, Verdict.UNDETERMINED, f"{CLAUDE_MD} {handle.error}"
        )
    return handle.text, None


def _check_adr_section(reader: _Reader, rule: Rule) -> Optional[Finding]:
    text, failure = _claude_md(reader, rule)
    if failure:
        return failure
    if _ADR_HEADING.search(text) or _ADR_SECTION.search(text):
        return None
    headings = _ANY_HEADING.findall(text)
    return _finding(
        reader,
        rule,
        Verdict.FAILED,
        f"{CLAUDE_MD} exists with {len(headings)} headings and none of them is an "
        f"ADR-NNN entry or an Architecture Decision Records section",
    )


def _adr_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    matches = list(_ADR_HEADING.finditer(text))
    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else _next_heading(text, match.end())
        )
        blocks.append((f"ADR-{match.group(2)}", text[match.end() : end]))
    return blocks


def _next_heading(text: str, start: int) -> int:
    nxt = _ANY_HEADING.search(text, start)
    return nxt.start() if nxt else len(text)


def _check_adr_format(reader: _Reader, rule: Rule) -> Optional[Finding]:
    text, failure = _claude_md(reader, rule)
    if failure:
        # The absent file is `adr-section-present`'s finding, not this one's.
        # Reporting the same fact twice as two failures inflates the count.
        return _finding(
            reader,
            rule,
            Verdict.UNDETERMINED,
            f"no ADR entry could be read, so the format of none of them could be "
            f"checked ({failure.found})",
        )
    blocks = _adr_blocks(text)
    if not blocks:
        return _finding(
            reader,
            rule,
            Verdict.UNDETERMINED,
            f"{CLAUDE_MD} contains no ADR-NNN heading, so there is no entry whose "
            f"fields could be checked",
        )
    problems: list[str] = []
    for name, body in blocks:
        absent = [f for f in _ADR_FIELDS if not _field_pattern(f).search(body)]
        if absent:
            problems.append(f"{name} is missing {', '.join(absent)}")
        status = re.search(
            r"\*{0,2}\s*Status\s*\*{0,2}\s*:\**\s*([A-Za-z]+)", body, re.I
        )
        if status and status.group(1).lower() not in ADR_STATUSES:
            problems.append(
                f"{name} has Status {status.group(1)!r}, which is not one of "
                f"{', '.join(ADR_STATUSES)}"
            )
    if problems:
        return _finding(reader, rule, Verdict.FAILED, "; ".join(problems))
    return None


# --------------------------------------------------------------------------
# Section 7, observability
# --------------------------------------------------------------------------

_OBSERVABILITY_CALL = (
    re.compile(r"\bconfigureScope\s*\("),
    re.compile(r"\bsetHttpStatus\s*\("),
    re.compile(r"\btelemetryClient\.track\w+\s*\("),
    re.compile(r"\bDT\.customAction\b"),
    re.compile(r"\bSentry\.\w+\s*\("),
    re.compile(r"\bappInsights\.\w+\s*\("),
)
_HANDLER_PATH = re.compile(
    r"(^|/)(handlers?|controllers?|routes?|endpoints?|views|api)([/._-]|$)", re.I
)
_MIDDLEWARE_PATH = re.compile(r"middleware", re.I)
_MIDDLEWARE_REGISTRATION = re.compile(
    r"(add_middleware\s*\(|@app\.middleware\b|app\.use\s*\(|UseMiddleware\s*<)"
)


def _check_no_per_handler_observability(
    reader: _Reader, rule: Rule
) -> Optional[Finding]:
    surface, exhausted = _http_surface(reader)
    if not surface and not exhausted:
        return _finding(
            reader,
            rule,
            Verdict.NOT_APPLICABLE,
            "no HTTP surface was found, so there are no handlers that could be "
            "calling an observability SDK",
        )
    hits: list[str] = []
    for path in reader.paths():
        if _vendored(path) or not path.endswith(_SOURCE_SUFFIX):
            continue
        if not _HANDLER_PATH.search(path):
            continue
        if _MIDDLEWARE_PATH.search(path):
            continue
        handle = reader.read(path)
        if not handle.readable:
            continue
        text = handle.text
        if _MIDDLEWARE_REGISTRATION.search(text):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for pattern in _OBSERVABILITY_CALL:
                if pattern.search(line):
                    hits.append(f"{path}:{number} calls {pattern.pattern}")
                    break
    if hits:
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            "; ".join(hits[:10])
            + (f" and {len(hits) - 10} more" if len(hits) > 10 else ""),
        )
    return None


# --------------------------------------------------------------------------
# Section 8, connection reuse
# --------------------------------------------------------------------------

_SAM_MARKER = re.compile(r"(AWS::Serverless|Transform\s*:)")


def _sam_templates(reader: _Reader) -> list[tuple[str, Any, str]]:
    out: list[tuple[str, Any, str]] = []
    for path in reader.paths():
        if _vendored(path) or not re.search(r"(^|/)template\.ya?ml$", path):
            continue
        handle = reader.read(path)
        if not handle.readable:
            out.append((path, None, handle.error))
            continue
        if not _SAM_MARKER.search(handle.text):
            continue
        doc, error = _parsed(reader, path)
        out.append((path, doc, error))
    return out


def _check_aws_nodejs_reuse(reader: _Reader, rule: Rule) -> Optional[Finding]:
    templates = _sam_templates(reader)
    if not templates:
        return _finding(
            reader,
            rule,
            Verdict.NOT_APPLICABLE,
            "no SAM template is in this tree, so there is no Globals.Function "
            "environment to set the switch in",
            looked_at=(LISTING,),
        )
    problems: list[str] = []
    node_seen = False
    for path, doc, error in templates:
        if error:
            return _finding(reader, rule, Verdict.UNDETERMINED, error)
        runtimes = [str(r) for r in _values_for_key(doc, "Runtime")]
        if not any(r.lower().startswith("nodejs") for r in runtimes):
            continue
        node_seen = True
        variables = _mapping(
            _mapping(
                _mapping(_mapping(_mapping(doc).get("Globals")).get("Function")).get(
                    "Environment"
                )
            ).get("Variables")
        )
        value = variables.get("AWS_NODEJS_CONNECTION_REUSE_ENABLED")
        if value is None:
            problems.append(
                f"{path} declares a Node runtime and its "
                f"Globals.Function.Environment.Variables has no "
                f"AWS_NODEJS_CONNECTION_REUSE_ENABLED"
            )
        elif str(value).strip() != "1":
            problems.append(
                f"{path} sets AWS_NODEJS_CONNECTION_REUSE_ENABLED to "
                f"{str(value)!r} rather than 1"
            )
    if not node_seen:
        return _finding(
            reader,
            rule,
            Verdict.NOT_APPLICABLE,
            f"{', '.join(p for p, _, _ in templates)} declares no Node runtime, so "
            f"this is not a Node Lambda project",
        )
    if problems:
        return _finding(reader, rule, Verdict.FAILED, "; ".join(problems))
    return None


# --------------------------------------------------------------------------
# Documentation
# --------------------------------------------------------------------------

MANIFESTS = (
    "package.json",
    "pom.xml",
    "go.mod",
    "pyproject.toml",
    "host.json",
    "build.gradle",
    "build.gradle.kts",
)


def _directory(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _check_readme_per_package(reader: _Reader, rule: Rule) -> Optional[Finding]:
    paths = [p for p in reader.paths() if not _vendored(p)]
    packages = sorted(
        {_directory(p) for p in paths if p.rsplit("/", 1)[-1] in MANIFESTS}
    )
    if not packages:
        return _finding(
            reader,
            rule,
            Verdict.NOT_APPLICABLE,
            f"no directory in this tree holds any of {', '.join(MANIFESTS[:5])}, so "
            f"there is no service or package that owes a README",
            looked_at=(LISTING,),
        )
    readmes = {
        _directory(p) for p in paths if p.rsplit("/", 1)[-1].lower() in ("readme.md",)
    }
    missing = [p for p in packages if p not in readmes]
    if missing:
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            f"{len(missing)} of {len(packages)} package directories have no sibling "
            f"README.md: " + ", ".join(m or "<repository root>" for m in missing),
            looked_at=(LISTING,),
        )
    return None


_OPERATIONAL_TABLES = (
    ("Sprint Status", re.compile(r"(?i)sprint\s+status")),
    ("Prod vs Repo Drift", re.compile(r"(?i)prod\s+vs\.?\s+repo\s+drift")),
    ("Security Issues", re.compile(r"(?i)security\s+issues")),
    ("Branch Health", re.compile(r"(?i)branch\s+health")),
)
_TABLE_ROW = re.compile(r"(?m)^\s*\|")


def _check_operational_tables(reader: _Reader, rule: Rule) -> Optional[Finding]:
    pipes = _pipelines(reader)
    if not pipes.ado:
        return _finding(
            reader,
            rule,
            Verdict.NOT_APPLICABLE,
            "there is no azure-pipelines.yml, so this is not an ADO managed project "
            "and a Sprint Status table has no sprint to report",
            looked_at=(LISTING,),
        )
    text, failure = _claude_md(reader, rule)
    if failure:
        return failure
    missing: list[str] = []
    for name, pattern in _OPERATIONAL_TABLES:
        match = pattern.search(text)
        if not match:
            missing.append(f"{name} is absent")
            continue
        body = text[match.end() : _next_heading(text, match.end())]
        if not _TABLE_ROW.search(body):
            missing.append(f"{name} has a heading but no table under it")
    if missing:
        return _finding(reader, rule, Verdict.FAILED, "; ".join(missing))
    return _finding(
        reader,
        rule,
        Verdict.PASSED,
        "all four sections are present with a table under each",
        limitation=(
            "presence is checkable and currency is not. Whether the Prod vs Repo "
            "Drift table reflects today's production depends on state that leaves no "
            "trace in the tree, and a stale CRITICAL drift row is the documented "
            "cause of production regressions. This audit cannot say the tables are "
            "correct, only that they exist. The column headers were not compared "
            "against the standard either, because the standard's column list is not "
            "carried in this repository"
        ),
    )


_DEPLOY_SCRIPT = re.compile(r"(^|/)[\w.-]*deploy[\w.-]*\.(sh|ps1|bat|cmd)$", re.I)
_BUILD_WORDS = (
    "build",
    "npm run build",
    "dotnet build",
    "mvn package",
    "go build",
    "docker build",
)
_TEST_WORDS = ("test", "pytest", "npm test", "go test", "dotnet test", "jest")
_QUALITY_WORDS = (
    "lint",
    "ruff",
    "eslint",
    "flake8",
    "coverage",
    "--cov",
    "sonar",
    "checkstyle",
)


def _check_heavy_work_offloaded(reader: _Reader, rule: Rule) -> Optional[Finding]:
    pipes = _pipelines(reader)
    if not pipes.any_at_all:
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            "no pipeline definition exists, so build, test and lint are being carried "
            "by whoever remembers to run them",
            looked_at=(LISTING,),
        )
    # Raw text, not the parsed tree. Whether a build step exists is a question a
    # grep settles, and grepping keeps this rule answerable on a pipeline file
    # the subset reader cannot parse.
    blob = ""
    for path in pipes.modelled + pipes.unmodelled:
        handle = reader.read(path)
        if handle.readable:
            blob += handle.text.lower() + "\n"
    if not blob.strip():
        return _finding(
            reader,
            rule,
            Verdict.UNDETERMINED,
            "a pipeline definition exists but none of its files could be read",
        )
    absent = []
    if not any(word in blob for word in _BUILD_WORDS):
        absent.append("no build step")
    if not any(word in blob for word in _TEST_WORDS):
        absent.append("no test step")
    if not any(word in blob for word in _QUALITY_WORDS):
        absent.append("no lint or coverage step")

    paths = [p for p in reader.paths() if not _vendored(p)]
    iac = [p for p in paths if _IAC_TEMPLATE.search(p)]
    loose = [
        p
        for p in paths
        if _DEPLOY_SCRIPT.search(p) and p.rsplit("/", 1)[-1].lower() not in blob
    ]
    if loose:
        absent.append(
            f"{', '.join(loose)} deploys but no pipeline definition calls it"
        )
    if "deploy" in blob and not iac:
        absent.append(
            "the pipeline deploys and no IaC template (SAM, Bicep, ARM or Terraform) "
            "is in the tree for it to invoke"
        )
    if absent:
        return _finding(
            reader,
            rule,
            Verdict.FAILED,
            f"{', '.join(pipes.modelled + pipes.unmodelled)} carries "
            + "; ".join(absent),
        )
    return None


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

DETERMINISTIC: tuple[Check, ...] = (
    Check(
        Rule(
            id="secret-scan-first-stage",
            section="3",
            severity="critical",
            statement=(
                "Every project pipeline must define a SecretScan stage, and it must "
                "be the first stage, before build and test."
            ),
            looked_for=(
                "a SecretScan stage that is genuinely first: the first entry under "
                "stages: for Azure DevOps, or a job with no upstream needs: that "
                "every build and test job depends on for Actions"
            ),
            how=(
                "The dependency graph is read rather than the line order, because a "
                "first match on the string passes a workflow where the scan runs "
                "beside the build."
            ),
        ),
        _check_secret_scan_first,
    ),
    Check(
        Rule(
            id="gitleaks-pinned-hard-gate",
            section="3",
            severity="critical",
            statement=(
                "The SecretScan stage must run gitleaks pinned to v8.18.4 as a hard "
                "gate that fails the pipeline on any secret found."
            ),
            looked_for=(
                "a gitleaks invocation inside the scan stage, the version v8.18.4 "
                "pinned somewhere concrete, --exit-code 1, and no continueOnError"
            ),
            how="Reads the script steps inside the scan stage only.",
        ),
        _check_gitleaks_gate,
    ),
    Check(
        Rule(
            id="secrets-never-committed",
            section="9",
            severity="critical",
            statement=(
                "Secret values must never be hardcoded or committed; they are "
                "injected at deploy time via IaC references."
            ),
            looked_for=(
                "literal secret values in .env, appsettings*.json, "
                "local.settings.json and source files named for keys or tokens, and "
                "IaC secret parameters that resolve to an inline literal rather than "
                "{{resolve:ssm:...}} or @Microsoft.KeyVault(...)"
            ),
            how=(
                "A .env.example carrying placeholders is not a finding. No value is "
                "ever reproduced in the output."
            ),
        ),
        _check_secrets_never_committed,
    ),
    Check(
        Rule(
            id="secret-key-hierarchical-path",
            section="9",
            severity="high",
            statement=(
                "Every secret and config key must follow the hierarchical "
                "/{Product}/{Stage}/settings/{Service}/{Key} convention in the form "
                "its store uses, and the service segment must always be present."
            ),
            looked_for=(
                "each key matching its store's form of the convention: an SSM path, "
                "a Key Vault name, an App Configuration key or a double underscore "
                "environment variable"
            ),
            how="Keys are collected from .env.example, appsettings*.json and IaC.",
        ),
        _check_key_hierarchy,
    ),
    Check(
        Rule(
            id="stage-not-baked-into-value",
            section="9",
            severity="medium",
            statement=(
                "Stage (Dev, QA, Prod) must always appear as a segment of the config "
                "key, never baked into the value."
            ),
            looked_for=(
                "a stage segment in every key path, and no stage marker in a value "
                "under a key that has none"
            ),
            how=(
                "The key half is exact. The value half is a pattern with known false "
                "positives in genuinely stage scoped files, so it is reported as "
                "suspected."
            ),
        ),
        _check_stage_segment,
    ),
    Check(
        Rule(
            id="openapi-spec-at-repo-root",
            section="6",
            severity="high",
            statement=(
                "Every service that exposes HTTP endpoints must have an OpenAPI 3.0 "
                "or Swagger 2.0 specification committed at the repo root."
            ),
            looked_for=(
                "openapi.yaml or swagger-definition.yaml at the root, but only once a "
                "router, controller or HTTP trigger proves there are endpoints"
            ),
            how="A library or worker repository is out of scope, not failing.",
        ),
        _check_openapi_at_root,
    ),
    Check(
        Rule(
            id="adr-section-present",
            section="2",
            severity="high",
            statement=(
                "Every project CLAUDE.md must contain an Architecture Decision Record "
                "section."
            ),
            looked_for=(
                "an ADR-NNN heading or an Architecture Decision Records section in "
                "CLAUDE.md at the repository root"
            ),
            how="An absent CLAUDE.md is the same failure.",
        ),
        _check_adr_section,
    ),
    Check(
        Rule(
            id="adr-entry-format",
            section="2",
            severity="medium",
            statement=(
                "Each ADR entry must carry Date, Status (Proposed, Implemented or "
                "Superseded), Decision, Reason and Consequence."
            ),
            looked_for=(
                "the five labelled fields under every ADR-NNN heading, and a Status "
                "value that is one of the three allowed words"
            ),
            how="A literal field presence check over text already in the file.",
        ),
        _check_adr_format,
    ),
    Check(
        Rule(
            id="no-per-handler-observability",
            section="7",
            severity="medium",
            statement=(
                "Individual handlers must never call observability APIs directly; the "
                "middleware fires once for all routes."
            ),
            looked_for=(
                "configureScope, setHttpStatus, telemetryClient.track*, "
                "DT.customAction or Sentry.* in a handler, controller or route file "
                "that is not the registered middleware"
            ),
            how="The negative half of the middleware rule, which a pattern settles.",
        ),
        _check_no_per_handler_observability,
    ),
    Check(
        Rule(
            id="aws-nodejs-connection-reuse-env",
            section="8",
            severity="high",
            statement=(
                "Node.js and AWS Lambda projects must set "
                "AWS_NODEJS_CONNECTION_REUSE_ENABLED=1 in every SAM "
                "Globals.Function.Environment."
            ),
            looked_for=(
                "Globals.Function.Environment.Variables."
                "AWS_NODEJS_CONNECTION_REUSE_ENABLED set to 1 in every SAM template "
                "that declares a Node runtime"
            ),
            how="On an Azure or on premises repository this rule does not apply.",
        ),
        _check_aws_nodejs_reuse,
    ),
    Check(
        Rule(
            id="readme-per-service-package",
            section="Documentation and README Standard",
            severity="high",
            statement=(
                "Every service and package must have a README, and it must live "
                "beside the code."
            ),
            looked_for=(
                "a README.md beside every directory holding package.json, pom.xml, "
                "go.mod, pyproject.toml or a function host config"
            ),
            how="A single package repository is satisfied by one root README.",
        ),
        _check_readme_per_package,
    ),
    Check(
        Rule(
            id="claude-md-operational-tables",
            section="CLAUDE.md Living Audit Protocol",
            severity="medium",
            statement=(
                "Every project CLAUDE.md must maintain a Sprint Status table, a Prod "
                "vs Repo Drift table, a Security Issues table and a Branch Health "
                "table."
            ),
            looked_for=(
                "the four sections in CLAUDE.md, each with a table under it, in a "
                "repository that is ADO managed"
            ),
            how="Presence is checkable. Currency is not, and the finding says so.",
        ),
        _check_operational_tables,
    ),
    Check(
        Rule(
            id="heavy-work-offloaded-to-pipeline",
            section="Offload-to-Pipeline Principle",
            severity="medium",
            statement=(
                "Deterministic, heavy or repeatable work must be carried by committed "
                "pipeline definitions, with infrastructure codified as IaC the "
                "pipeline invokes."
            ),
            looked_for=(
                "a pipeline definition carrying build, test and lint or coverage "
                "steps, and no loose deploy script that no pipeline calls"
            ),
            how=(
                "A library repository with nothing to deploy owes only the build and "
                "test half."
            ),
        ),
        _check_heavy_work_offloaded,
    ),
)


# --------------------------------------------------------------------------
# The rules a pattern must not pretend to answer
#
# These five are decidable, and not by anything in this file. Whether an ADR
# actually decides a routing strategy rather than mentioning one in passing,
# whether a documented endpoint and a mounted sub-router describe the same path,
# whether a factory called from a handler ultimately returns a cached client:
# each of those is a reading, and a regular expression that produced a verdict
# on it would be wrong in both directions while looking exactly as confident as
# the checks above.
#
# So they are a separate list with no implementation. A caller hands them to a
# reader, human or model, together with the paths worth opening. What comes back
# may add a failure and may never turn one of these into a pass, which is
# ADR-002 applied here: `tighten` below is the only way in, and it has no branch
# that produces `passed`.
# --------------------------------------------------------------------------

NEEDS_JUDGEMENT: tuple[Rule, ...] = (
    Rule(
        id="openapi-matches-real-routes",
        section="6",
        severity="medium",
        statement=(
            "The committed specification must match the endpoints the code actually "
            "exposes."
        ),
        looked_for="the spec paths block set against the route registrations",
        how=(
            "Route registration differs by framework, prefixes are composed from "
            "mounted sub-routers and decorators, and a path parameter spelled "
            "differently in each place is a match rather than a drift. The presence "
            "of the two artifacts is mechanical; deciding that they agree is not."
        ),
    ),
    Rule(
        id="adr-minimum-topic-coverage",
        section="2",
        severity="high",
        statement=(
            "The ADR set must cover routing strategy, state management, API layer "
            "pattern, authentication and token storage, and any decision that would "
            "surprise a new contributor."
        ),
        looked_for="each of the four named topics actually decided, not mentioned",
        how=(
            "The fifth clause has no textual marker whatsoever and can only be judged "
            "by a reader who knows the codebase well enough to notice what is "
            "missing. A repository with neither routing nor auth should be scoped "
            "down rather than failed."
        ),
    ),
    Rule(
        id="lifecycle-middleware-both-hooks",
        section="7",
        severity="high",
        statement=(
            "Every HTTP service must instrument request lifecycle events via "
            "middleware, with a start hook at route entry after auth and an end hook "
            "at response flush."
        ),
        looked_for=(
            "one hook capturing operationName, method, path, tenantId, userId and "
            "clientInfo, and one capturing HTTP status, semantic status and duration"
        ),
        how=(
            "The vendor call names differ across Sentry, App Insights, Dynatrace and "
            "plain structured logging, so a reader has to map whatever is there onto "
            "the two hooks rather than grep for fixed identifiers."
        ),
    ),
    Rule(
        id="shared-client-connection-reuse",
        section="8",
        severity="high",
        statement=(
            "Every service making outbound calls must reuse connections across "
            "invocations; declaring a new client per request is always wrong."
        ),
        looked_for="where each infrastructure client is constructed",
        how=(
            "A constructor inside a handler is the failure, but recognising that a "
            "factory called from a handler ultimately returns a cached instance takes "
            "reading, not grepping."
        ),
    ),
    Rule(
        id="readme-required-sections",
        section="Documentation and README Standard",
        severity="medium",
        statement=(
            "Each README must state purpose, what to read first, a quickstart, "
            "configuration, deploy, ownership and links to canonical guides."
        ),
        looked_for="the seven elements genuinely present, whatever they are titled",
        how=(
            "Headings vary in wording so a fixed list will both miss and misfire, and "
            "a stale quickstart that no longer matches the build script is a failure "
            "even though the section exists."
        ),
    ),
)


# --------------------------------------------------------------------------
# The rules a repository cannot answer at all
#
# Six rules about calendars, review events and git history. A working tree audit
# sees none of them, and the tempting substitutions are all worse than silence: a
# committed commitlint config shows an intent to enforce rather than any commit
# conforming, a branch policy file shows a gate is configured rather than that a
# pull request passed it, and a findings report in the tree dates one past run
# without establishing any cadence. Every one of those would let this tool report
# green on evidence it does not have.
# --------------------------------------------------------------------------

NOT_CHECKABLE: tuple[Rule, ...] = (
    Rule(
        id="session-rescan-protocol",
        section="1",
        severity="low",
        statement=(
            "Run git log, git status, a test file count and an env var check at the "
            "start of every session before making changes."
        ),
        looked_for="nothing: there is no artifact to open",
        how=(
            "Operator behaviour at session start. A repository whose team follows it "
            "and one whose team ignores it are byte identical."
        ),
    ),
    Rule(
        id="test-count-baseline",
        section="1",
        severity="medium",
        statement=(
            "Stop and investigate if the test file count drops below the last known "
            "baseline, or if env vars have been removed."
        ),
        looked_for="a baseline that the repository does not record",
        how=(
            "The count is trivially countable and the baseline it must be compared "
            "against is written nowhere. The standard states no minimum test count, "
            "so inventing a threshold would be fabricating the rule rather than "
            "auditing it."
        ),
    ),
    Rule(
        id="branch-naming-convention",
        section="PR and Branching Standards",
        severity="low",
        statement=(
            "Branches must be named bugfix/<WI-ID>-<service-short>-<kebab-description>"
            " or feature/<...>, and always cut from the active integration branch."
        ),
        looked_for="a branch name and a fork point, which live in git refs",
        how="Deterministic for a tool with git history, and not from a path listing.",
    ),
    Rule(
        id="commit-message-format",
        section="PR and Branching Standards",
        severity="low",
        statement=(
            "Commits must be formatted fix(<service-short>): <what was wrong and what "
            "was done>, with a Fixes AB #<WI-ID> line and Root cause and Fix lines."
        ),
        looked_for="commit messages, which a working tree audit never sees",
        how=(
            "A committed commit-msg hook would be evidence of intent to enforce, "
            "which is a different claim from the commits conforming."
        ),
    ),
    Rule(
        id="secure-code-companion-pr-gate",
        section="4",
        severity="high",
        statement=(
            "Every PR touching production source must pass secure-code-companion "
            "review; FAIL findings block the PR with no exceptions."
        ),
        looked_for="a review event on a pull request, which is not in the tree",
        how=(
            "Nothing in the repository records whether the gate ran, what it "
            "returned, or whether a FAIL was overridden."
        ),
    ),
    Rule(
        id="pen-test-cadence-sast-loop",
        section="5",
        severity="medium",
        statement=(
            "pen-test-companion runs quarterly, pre-major-release, "
            "post-infrastructure-change and post-Sev1, and its SAST Rule Updates must "
            "be incorporated within 5 business days."
        ),
        looked_for="a calendar and an incident history, neither of which is a file",
        how=(
            "A cadence rule graded on whether some report happens to be in the tree "
            "is the clearest way for a compliance tool to start lying."
        ),
    ),
)

ALL_RULES: tuple[Rule, ...] = (
    tuple(c.rule for c in DETERMINISTIC) + NEEDS_JUDGEMENT + NOT_CHECKABLE
)


# --------------------------------------------------------------------------
# Running the thing
# --------------------------------------------------------------------------


@dataclass
class Audit:
    results: list[Finding] = field(default_factory=list)
    summary: Optional[Summary] = None
    paths_read: list[str] = field(default_factory=list)
    read_log: dict[str, Any] = field(default_factory=dict)
    blocked: str = ""

    def by_id(self, rule_id: str) -> Optional[Finding]:
        return next((f for f in self.results if f.rule_id == rule_id), None)

    def failures(self) -> list[Finding]:
        return [f for f in self.results if f.verdict is Verdict.FAILED]

    def attention(self) -> list[Finding]:
        return [f for f in self.results if f.needs_attention]

    def assessment(self) -> str:
        summary = self.summary or summarise(self.results)
        lines = ["## Standards audit", "", summary.one_line(), ""]
        for finding in sorted(
            self.attention(), key=lambda f: (f.verdict.value, f.rule_id)
        ):
            lines.append(f"- {finding.line()}")
        if summary.could_not_be_determined:
            lines.append("")
            lines.append(
                f"{summary.could_not_be_determined} of {summary.rules} rules are not "
                f"a pass and not a failure. None of them is counted as compliant."
            )
        return "\n".join(lines)

    def to_response(self) -> Response:
        if self.blocked:
            return Response(
                companion=COMPANION,
                status=Status.BLOCKED,
                reason=self.blocked,
                confidence=0.0,
                read_log=self.read_log,
            )
        summary = self.summary or summarise(self.results)
        failures = self.failures()
        status = Status.NEEDS_CHANGES if failures else Status.OK
        reason = ""
        if failures:
            reason = (
                f"{len(failures)} standards rules failed, "
                f"{sum(1 for f in failures if f.severity == 'critical')} of them "
                f"critical: " + ", ".join(f.rule_id for f in failures)
            )
        # Confidence is the share of the rules this audit claimed to check that it
        # actually settled. The judgement and not-checkable lists are declared out
        # of scope up front rather than quietly missing, so they are not counted
        # against it.
        settled = summary.passed + summary.failed + summary.not_applicable
        confidence = settled / summary.checked if summary.checked else 0.0
        return Response(
            companion=COMPANION,
            status=status,
            assessment=self.assessment(),
            findings=[f.line() for f in self.attention()],
            paths_read=self.paths_read,
            citations=sorted(
                {p for f in self.attention() for p in f.looked_at if p != LISTING}
            ),
            confidence=round(confidence, 3),
            reason=reason,
            read_log=self.read_log,
        )


def _run(check: Check, reader: _Reader) -> Finding:
    reader.current = check.rule.id
    try:
        result = check.run(reader, check.rule)
    except Exception as exc:  # noqa: BLE001
        # One check that raises must not take the other twelve with it, and it
        # must not be silently absent from the count either.
        return _finding(
            reader,
            check.rule,
            Verdict.UNDETERMINED,
            f"the check itself failed with {type(exc).__name__}: {exc}",
        )
    if result is not None:
        return result
    truncated = reader.truncated_for(check.rule.id)
    return _finding(
        reader,
        check.rule,
        Verdict.PASSED,
        f"nothing that fails the rule in the "
        f"{len(reader.touched(check.rule.id))} paths this check examined",
        limitation=(
            f"{', '.join(truncated)} was longer than {MAX_BYTES_PER_FILE} bytes and "
            f"was read only that far, so this pass covers the part that was read"
            if truncated
            else ""
        ),
    )


def check_repository(corpus: Corpus) -> Audit:
    """Run every deterministic check, then declare the rest undecided."""
    reader = _Reader(corpus)
    try:
        paths = reader.paths()
    except Exception as exc:  # noqa: BLE001
        return Audit(
            blocked=f"the repository could not be listed: {type(exc).__name__}: {exc}"
        )
    if not paths:
        return Audit(
            blocked=(
                "the repository listing is empty, so there is nothing to audit. An "
                "empty listing is not a compliant repository"
            )
        )

    results = [_run(check, reader) for check in DETERMINISTIC]
    results.extend(
        Finding(
            rule_id=rule.id,
            severity=rule.severity,
            verdict=Verdict.NEEDS_JUDGEMENT,
            looked_for=rule.looked_for,
            looked_at=(),
            found=(
                "not decided here. This rule needs a reader, and a pattern that "
                f"answered it would be guessing: {rule.how}"
            ),
        )
        for rule in NEEDS_JUDGEMENT
    )
    results.extend(
        Finding(
            rule_id=rule.id,
            severity=rule.severity,
            verdict=Verdict.NOT_CHECKABLE,
            looked_for=rule.looked_for,
            looked_at=(),
            found=f"nothing in a repository records this: {rule.how}",
        )
        for rule in NOT_CHECKABLE
    )
    return Audit(
        results=results,
        summary=summarise(results),
        paths_read=reader.opened,
        read_log=reader.as_dict(),
    )


def audit(corpus: Corpus) -> Response:
    """The envelope the rest of the fleet speaks.

    `blocked` is reserved for a repository that cannot be read at all. A
    repository that fails every rule is legible, and `needs_changes` is what
    legible non compliance is called here.
    """
    return check_repository(corpus).to_response()


def audit_repository(
    repository: Optional[str] = None,
    ref: str = "HEAD",
    reader: Any = None,
) -> Response:
    """The demo corpus when no repository is named, a real one when it is.

    `reader` is the agentic half. Without one the audit is thirteen patterns and
    five rules honestly left at `needs_judgement`, which is a complete and
    truthful result and is what the offline suite runs. With one, those five go
    to something that opens files and decides, and can only make the verdict
    harsher.
    """
    corpus = build_corpus(repository, ref=ref, scope=AUDIT_SCOPE)
    result = check_repository(corpus)
    if reader is not None:
        # Deliberately not inside `check_repository`. The deterministic result
        # exists on its own and is what the tighten is applied to, so the two
        # halves stay separable and the model's contribution stays visible as a
        # difference rather than being blended into one opaque verdict.
        # The same corpus object goes to both halves. Letting the reader build
        # its own from a repository name is how the two silently diverge, and it
        # did: a two-file audit came back with a confident finding about a path
        # that only exists in the demo corpus.
        result = tighten(result, reader.read(judgement_queue(corpus), corpus))
    return result.to_response()


def judgement_queue(corpus: Corpus) -> list[dict[str, Any]]:
    """The five rules a pattern must not answer, ready to hand to a reader.

    Candidate paths come from the listing alone, so building this queue opens
    nothing. What a reader does with them is bounded by `tools.check_read` like
    every other read in this product.
    """
    paths = list(corpus.paths())
    candidates = {
        "openapi-matches-real-routes": [
            p for p in paths if p in SPEC_NAMES or _HANDLER_PATH.search(p)
        ],
        "adr-minimum-topic-coverage": [p for p in paths if p == CLAUDE_MD],
        "lifecycle-middleware-both-hooks": [
            p for p in paths if _MIDDLEWARE_PATH.search(p) or "app." in p
        ],
        "shared-client-connection-reuse": [
            p
            for p in paths
            if not _vendored(p)
            and re.search(r"(client|repository|db|database|queue|http)", p, re.I)
        ],
        "readme-required-sections": [
            p for p in paths if p.rsplit("/", 1)[-1].lower() == "readme.md"
        ],
    }
    return [
        {
            "rule": rule.id,
            "severity": rule.severity,
            "statement": rule.statement,
            "looked_for": rule.looked_for,
            "why_a_reader": rule.how,
            "candidate_paths": candidates.get(rule.id, [])[:20],
            # Said in the payload as well as in the docstring, because the payload
            # is what a model is handed and the docstring is not.
            "you_may_only_tighten": (
                "Report failed or suspected with the path you read. You cannot "
                "return a pass for this rule."
            ),
        }
        for rule in NEEDS_JUDGEMENT
    ]


_READER_VERDICTS = {"failed": Verdict.FAILED, "suspected": Verdict.SUSPECTED}


def tighten(audit_result: Audit, judgements: Iterable[dict[str, Any]]) -> Audit:
    """Fold a reader's answers back in, in the tightening direction only.

    There is deliberately no branch here that produces `Verdict.PASSED`. A rule
    on the judgement list can become a failure or a suspicion because somebody
    read the code and found something. It cannot become compliant, because the
    thing being asked is exactly what this module said it could not decide, and
    accepting a pass would let whatever produced the answer approve itself.
    """
    deferred = {r.id for r in NEEDS_JUDGEMENT}
    updated = {f.rule_id: f for f in audit_result.results}
    for item in judgements:
        rule_id = str(item.get("rule") or item.get("rule_id") or "")
        current = updated.get(rule_id)
        if rule_id not in deferred or current is None:
            continue
        if current.verdict is not Verdict.NEEDS_JUDGEMENT:
            continue
        verdict = _READER_VERDICTS.get(str(item.get("verdict", "")).lower())
        if verdict is None:
            continue
        found = str(item.get("found", "")).strip()
        looked_at = tuple(str(p) for p in item.get("looked_at", []) if str(p).strip())
        updated[rule_id] = Finding(
            rule_id=rule_id,
            severity=current.severity,
            verdict=verdict,
            looked_for=current.looked_for,
            looked_at=looked_at or (BY_A_READER,),
            found=found or "a reader reported a problem without saying what it was",
        )
    results = [updated[f.rule_id] for f in audit_result.results]
    return Audit(
        results=results,
        summary=summarise(results),
        paths_read=audit_result.paths_read,
        read_log=audit_result.read_log,
        blocked=audit_result.blocked,
    )
