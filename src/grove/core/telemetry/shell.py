"""One shell invocation, written so a Langfuse-native evaluator can find it.

A shell command is the one tool call whose quality is worth grading across every
harness: it is where an agent touches the machine, and *"was that a reasonable
thing to run, and did it work"* is the same question whether Claude Code spelled
the tool ``Bash`` or Codex spelled it ``exec_command``. Nothing in this module
grades anything. Langfuse owns evaluation — the rules, the sampling, the judge
model, the scores. Grove's whole job is to make one invocation land as one
observation a rule can select and a judge can read.

Three properties are what "selectable and readable" actually costs, and each was
missing before this module existed.

**Selection has to be FLAT.** LangFuse nests every attribute it does not
recognise under ``metadata.attributes`` and documents that nesting as not
queryable, so ``grove.tool.category`` — present, correct and stored — matched
nothing. Everything a rule filters on is therefore written a second time through
:meth:`~grove.core.telemetry.semconv.LangfuseAttr.metadata`.

**The payloads have to be VALID.** The export seam encoded a tool's arguments to
JSON and then sliced the STRING at the attribute cap, which for any oversized
call published a truncated brace soup that no consumer can parse — and it did so
silently, because a clipped string is still a legal attribute value. Fitting
happens on the payload, before encoding, and says so in the payload.

**Absence has to stay absent.** A call still running, a call that returned an
empty string, a call whose output was clipped and a call the harness flagged as
failed are four different facts, and one of them (success) is not knowable at
all unless the harness recorded an exit code. This module reports the evidence
it has and reports "unknown" where it has none; it never reads a process's
success out of the absence of an error flag, and it never reads prose.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from grove.core.agents.shell import ShellCall
from grove.core.telemetry.semconv import ATTR_TEXT_CAP, LangfuseAttr

SHELL_SCHEMA: Final = "grove.shell/1"
"""Identity of the envelope shape below.

Versioned and filterable so a deployment holding observations from two Grove
releases can pin an evaluator to the shape it was written against, rather than
discovering the change as a rubric that quietly stopped matching.
"""

SHELL_CATEGORY: Final = "shell"
"""The canonical category value. The one selector an evaluation rule needs.

It is emitted **only** for a call whose provider tool name is a known shell tool
(:data:`~grove.core.agents.shell.SHELL_TOOL_NAMES`), which is also what makes it
a safe exclusion filter: an observation produced by anything other than Grove's
own normalization — a harness's own OTLP that Grove did not re-spell, or an
external transcript-exporting hook running beside Grove — carries no category at
all and is structurally outside the cohort.
"""


class ToolSource(StrEnum):
    """Which Grove tier wrote this observation.

    Grove can describe one physical tool call from two places — its replay of
    the session transcript, and its gateway re-export of the harness's own OTLP
    — and ``telemetry.content_owner`` normally ensures only one of them is
    producing content for a given agent kind. ``normally`` is why this exists:
    an operator who has changed that setting, or who is reading history written
    under a previous one, needs a filter that keeps exactly one record of each
    call in an evaluation cohort. Deterministic OTel ids do not do that job —
    they make reconciliation possible, they do not make ingestion idempotent.
    """

    TRANSCRIPT = "transcript"
    NATIVE_OTLP = "native_otlp"


class ShellResult(StrEnum):
    """What evidence of a result this observation actually holds.

    A ladder of completeness, not of success — every member here is a statement
    about the RECORD, and :class:`ShellOutcome` is the separate statement about
    the process. Keeping them apart is what lets an evaluator refuse to grade a
    call whose output it cannot see, instead of grading the clipping.
    """

    PENDING = "pending"
    """No result was recorded. The call was still in flight when this was
    written, or its result never landed — never "it returned nothing"."""

    REDACTED = "redacted"
    """A result WAS recorded and its content is withheld by content policy.

    Distinct from ``PENDING``, and the distinction is the whole reason this
    member exists: a redacting deployment folded every finished call onto
    "still running", so its shell observations reported a fleet permanently
    mid-command. What the policy withholds is the payload, never the fact.
    """

    EMPTY = "empty"
    """A result was recorded and it carried no content. A real answer: plenty of
    successful commands print nothing."""

    CAPTURED = "captured"
    """A result was recorded whole."""

    TRUNCATED = "truncated"
    """A result was recorded and this observation carries only part of it. The
    payload says so too, because a judge reading the output alone would
    otherwise grade a command on evidence it cannot tell is partial."""


class ShellOutcome(StrEnum):
    """What is known about how the process itself finished.

    ``SUCCEEDED`` is asserted from exactly one thing — a recorded exit status of
    zero — and from nothing else. In particular the absence of a tool-error flag
    is not evidence: Codex records no structural error flag on a shell result at
    all, so treating "not flagged" as "worked" would publish a confident verdict
    about a whole harness's calls that nothing measured.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ShellAttr:
    """The flat, filterable keys one shell observation carries.

    Each is the ``grove.*`` name it mirrors, folded through
    :meth:`LangfuseAttr.metadata`, so this table names facts rather than
    inventing a parallel vocabulary. They are emitted for shell observations
    only: a non-shell tool span is untouched, because classifying every tool
    Grove has ever seen is a claim about coverage Grove does not have.
    """

    CATEGORY: Final = LangfuseAttr.metadata("grove.tool.category")
    SCHEMA: Final = LangfuseAttr.metadata("grove.tool.schema")
    SOURCE: Final = LangfuseAttr.metadata("grove.tool.source")
    TOOL_NAME: Final = LangfuseAttr.metadata("grove.tool.name")
    CALL_ID: Final = LangfuseAttr.metadata("grove.tool.call_id")

    RESULT: Final = LangfuseAttr.metadata("grove.shell.result")
    OUTCOME: Final = LangfuseAttr.metadata("grove.shell.outcome")
    EXIT_CODE: Final = LangfuseAttr.metadata("grove.shell.exit_code")
    ERROR: Final = LangfuseAttr.metadata("grove.shell.error")
    BACKGROUND: Final = LangfuseAttr.metadata("grove.shell.background")


INPUT_COMMAND_PATH: Final = "command"
OUTPUT_CONTENT_PATH: Final = "content"
"""The two stable keys inside the input and output envelopes.

Named here rather than spelled at the call sites because they are the paths a
human types into an evaluator's variable mapping and into a rubric — they are
published surface, and moving one silently retargets somebody's judge.
"""

_TRUNCATED_KEY: Final = "truncated"


@dataclass(frozen=True, slots=True)
class ShellObservation:
    """One shell invocation's canonical metadata and payloads.

    Pure: it takes facts a caller has already read and returns attributes. Both
    export tiers build one, which is what makes "the same command and result
    property paths whatever produced them" a property of the code rather than a
    coincidence two modules maintain separately.
    """

    call: ShellCall | None
    """What the call asked for, or ``None`` when Grove could not read it — a
    redacting content policy withheld the arguments, or the harness sent a shape
    with no command in it. The identity and outcome metadata is published
    either way, so a redacted deployment still sees that a shell call happened
    and how it ended; only the payloads disappear."""
    source: ToolSource
    agent_kind: str
    tool_name: str
    resolved: bool
    """Whether a result was RECORDED for this call, whatever became of its text.

    Not inferred from :attr:`output`, and the redaction case is why: a content
    policy nulls the text of a call that finished perfectly well, so "no text
    here" answers two questions at once and gets the second one wrong. The
    caller holds the tool_result record; only it can say.
    """
    tool_call_id: str | None = None
    arguments: Mapping[str, Any] | None = None
    """The provider's own arguments, verbatim, carried alongside the normalized
    command so nothing a harness said is lost. Content-gated by the caller —
    ``None`` under a redacting policy, which leaves the command itself out too,
    since the command IS the content here."""
    output: str | None = None
    """The result's text. ``None`` means no result was recorded, which is a
    different fact from ``""`` and must stay so all the way to the wire."""
    exit_code: int | None = None
    is_error: bool = False
    """The harness's own structural tool-error flag, when it has one.

    Only ``True`` is ever published. ``False`` is ambiguous across harnesses —
    Claude Code means "not flagged" by it and Codex cannot mean anything by it —
    and an ambiguous ``false`` on the wire is read as a clean result by every
    consumer that does not know which harness wrote it.
    """
    emit_content: bool = True
    """Whether the command and result may ride at all. The identity and outcome
    metadata is emitted either way: it is a fact about the call, not its
    payload, and a deployment that redacts bodies still needs to see that a
    shell call happened and how it ended."""

    def result_state(self, *, truncated: bool) -> ShellResult:
        """Which evidence rung this observation sits on."""
        if not self.resolved:
            return ShellResult.PENDING
        if self.output is None:
            return ShellResult.REDACTED
        if truncated:
            return ShellResult.TRUNCATED
        return ShellResult.EMPTY if self.output == "" else ShellResult.CAPTURED

    def outcome(self) -> ShellOutcome:
        """What is known about the process, from recorded evidence only."""
        if not self.resolved:
            return ShellOutcome.PENDING
        if self.is_error or (self.exit_code is not None and self.exit_code != 0):
            return ShellOutcome.FAILED
        if self.exit_code == 0:
            return ShellOutcome.SUCCEEDED
        return ShellOutcome.UNKNOWN

    def input_payload(self) -> tuple[str, bool] | None:
        """``{"command": …, "argv": […], "arguments": {…}, "truncated": …}``.

        ``command`` is the stable path a rubric reads. ``argv`` rides beside it
        whenever the harness handed over an argument vector, because word
        boundaries a harness already decided are strictly more information than
        any line can carry — flattening them and calling the result equivalent
        is the kind of quiet lie that only shows up in a judge's verdict.
        """
        if not self.emit_content or self.call is None:
            return None
        payload: dict[str, Any] = {
            INPUT_COMMAND_PATH: self.call.command,
            _TRUNCATED_KEY: False,
        }
        if self.call.argv is not None:
            payload["argv"] = list(self.call.argv)
        if self.arguments:
            payload["arguments"] = _plain(self.arguments)
        return _fit(payload, text_key=INPUT_COMMAND_PATH, droppable=("arguments", "argv"))

    def output_payload(self) -> tuple[str, bool] | None:
        """``{"content": …, "exit_code": …, "truncated": …}``, or ``None``.

        ``None`` only when no result was recorded — the omission IS the pending
        signal, and emitting an empty envelope there would erase the difference
        between a call still running and a command that printed nothing.

        **The result is carried as a STRING under ``content``, even when it
        looks like JSON.** A command whose stdout happens to be a JSON document
        is still a command that printed text; parsing it into the envelope would
        make the envelope's shape depend on what the process printed, so one
        rubric would face a different document per invocation.
        """
        if self.output is None or not self.emit_content:
            return None
        payload: dict[str, Any] = {OUTPUT_CONTENT_PATH: self.output, _TRUNCATED_KEY: False}
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        return _fit(payload, text_key=OUTPUT_CONTENT_PATH)

    def attributes(self, *, truncated: bool) -> dict[str, str | int | bool]:
        """Every flat metadata key this observation publishes."""
        resolved: dict[str, str | int | bool] = {
            ShellAttr.CATEGORY: SHELL_CATEGORY,
            ShellAttr.SCHEMA: SHELL_SCHEMA,
            ShellAttr.SOURCE: str(self.source),
            ShellAttr.TOOL_NAME: self.tool_name,
            ShellAttr.RESULT: str(self.result_state(truncated=truncated)),
            ShellAttr.OUTCOME: str(self.outcome()),
        }
        if self.agent_kind:
            resolved[LangfuseAttr.metadata("grove.agent.kind")] = self.agent_kind
        if self.tool_call_id:
            resolved[ShellAttr.CALL_ID] = self.tool_call_id
        if self.exit_code is not None:
            resolved[ShellAttr.EXIT_CODE] = self.exit_code
        if self.is_error:
            resolved[ShellAttr.ERROR] = True
        if self.call is not None and self.call.background is not None:
            resolved[ShellAttr.BACKGROUND] = self.call.background
        return resolved

    def span_attributes(self) -> dict[str, str | int | bool]:
        """Everything this observation contributes to a span, payloads included.

        The one composition point. Both tiers call this rather than assembling
        metadata and envelopes themselves, because the truncation flag is
        produced by encoding the output and consumed by the metadata — two call
        sites doing that by hand is how one of them comes to publish
        ``captured`` for a clipped result.
        """
        resolved: dict[str, str | int | bool] = {}
        encoded_input = self.input_payload()
        if encoded_input is not None:
            resolved[LangfuseAttr.OBSERVATION_INPUT] = encoded_input[0]
        encoded_output = self.output_payload()
        truncated = False
        if encoded_output is not None:
            resolved[LangfuseAttr.OBSERVATION_OUTPUT] = encoded_output[0]
            truncated = encoded_output[1]
        resolved.update(self.attributes(truncated=truncated))
        return resolved


def _plain(value: Any) -> Any:
    """A JSON-encodable mirror of a provider's arguments.

    ``json.dumps(default=str)`` would do this at encode time, but only once —
    and :func:`_fit` may encode the same payload a dozen times while it shrinks.
    Normalizing once keeps that loop cheap and, more importantly, deterministic:
    a value whose ``str()`` includes an address would otherwise differ between
    two encodings of one payload.
    """
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _encode(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)


def _fit(
    payload: dict[str, Any],
    *,
    text_key: str,
    droppable: tuple[str, ...] = (),
    cap: int = ATTR_TEXT_CAP,
) -> tuple[str, bool]:
    """Encode ``payload`` so it fits ``cap`` and is ALWAYS valid JSON.

    Returns ``(encoded, truncated)``. The cap is Grove's own conservative
    ceiling rather than a vendor limit — LangFuse documents a 5 MB request
    ceiling on its cloud and no per-attribute limit at all — and it stays,
    because an OTLP receiver that rejects one oversized span drops the whole
    batch it rode in. What changes here is only that the shrinking happens on
    the PAYLOAD instead of on its encoding, and that the payload says it
    happened.

    Optional keys are dropped before the text is cut, so a huge arguments blob
    costs the arguments rather than the command a rubric is reading. The search
    for the largest surviving prefix is exact rather than arithmetic because
    JSON escaping is not length-preserving: one newline in a command costs two
    characters encoded, and a budget computed from the unescaped length
    overflows on exactly the multi-line commands this exists to carry.

    **Validity is the guarantee; the cap is best effort.** With the text emptied
    and every optional key dropped, what remains is the envelope's own keys —
    about 45 characters — and a cap below that cannot be met without emitting a
    fragment. It returns the small valid document instead, which is the right
    trade because the alternative is the unparseable output this function exists
    to end. Unreachable at :data:`ATTR_TEXT_CAP`; pinned by test so a future
    caller with a tighter cap finds out what it gets.
    """
    encoded = _encode(payload)
    if len(encoded) <= cap:
        return encoded, False

    work = dict(payload)
    for key in droppable:
        if key not in work:
            continue
        del work[key]
        candidate = _encode(_mark(work))
        if len(candidate) <= cap:
            return candidate, True

    text = str(work.get(text_key) or "")
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_encode(_mark({**work, text_key: text[:middle]}))) <= cap:
            low = middle
        else:
            high = middle - 1
    return _encode(_mark({**work, text_key: text[:low]})), True


def _mark(payload: dict[str, Any]) -> dict[str, Any]:
    """The same payload, admitting it is partial.

    Both envelopes carry ``truncated`` from the start and it only ever flips
    ``false`` → ``true``, which shortens the encoding by one character — so a
    payload measured as fitting cannot stop fitting when it is marked.
    """
    return {**payload, _TRUNCATED_KEY: True}


__all__ = [
    "INPUT_COMMAND_PATH",
    "OUTPUT_CONTENT_PATH",
    "SHELL_CATEGORY",
    "SHELL_SCHEMA",
    "ShellAttr",
    "ShellObservation",
    "ShellOutcome",
    "ShellResult",
    "ToolSource",
]
