"""The canonical vocabulary a Grove agent observation is written in.

One module answers one question: *what does an agent's work look like on the
wire?* Everything Grove exports — the transcript replay in
:mod:`grove.core.trace`, the context span in :mod:`grove.core.trace_forwarder`,
and the gateway's re-export of a harness's own OTLP — spells its attributes
from here, so a fleet of mixed harnesses lands as one shape.

**Every observation is written twice, on purpose.** The OpenTelemetry GenAI
semantic conventions specify ``gen_ai.input.messages``/``gen_ai.output.messages``
as structured ``role``/``parts`` documents. LangFuse maps neither: it reads
``gen_ai.prompt``/``gen_ai.completion`` (the superseded flat keys) and its own
``langfuse.observation.*``, verified against its OTel mapping docs on
2026-08-11. Portable and readable are therefore different keys *today*, and a
gateway that picks one picks either a vendor lock-in or a blank UI. Emitting
both costs attribute bytes and buys consumer independence; when LangFuse adopts
the newer keys, exactly one module deletes half its output.

**The convention strings are literals here, and a test proves them equal to the
SDK's.** ``opentelemetry.semconv._incubating.attributes.gen_ai_attributes``
holds these same values, but three facts argue against importing it in
production code. It is pre-stable and underscore-private, so its path is
expected to move — GenAI semconv split into its own repository at semconv
v1.42.0 and is still Development stability. ``opentelemetry`` is the optional
``telemetry`` extra, and :mod:`grove.core.trace` imports it lazily *precisely*
so ``grove.core`` stays importable without it; a module-scope import here would
undo that for every consumer of this vocabulary. And a constant that is a
string either way gains nothing at runtime from the indirection. So
``tests/core/test_telemetry_semconv.py`` imports the SDK and asserts every key
below matches — CI has the extra, drift fails there, and no production import
path depends on a module the OTel project has said it will move.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

ObservationKind = Literal["agent", "generation", "tool"]
"""The three things an agent does that are worth their own observation.

Deliberately smaller than the semconv operation vocabulary (which has nine):
these are the kinds Grove can *observe* from a transcript or an inbound span,
and a kind nothing can produce is a branch nothing can test.
"""

ATTR_TEXT_CAP: Final = 4000
"""Ceiling for any free-text or JSON attribute value.

A tool argument blob or a whole conversation document is unbounded, and an
OTLP receiver that rejects one oversized span drops the batch it rode in.
Truncation is the honest failure: a clipped prompt still says what happened,
a rejected batch says nothing at all.
"""


class GenAiAttr:
    """Attribute keys from the OpenTelemetry GenAI semantic conventions.

    Namespaced as a class rather than module constants so the call sites read
    ``GenAiAttr.TOOL_NAME`` — the namespace is the point, since three
    vocabularies below use overlapping words (``NAME``, ``ID``, ``MODEL``) and
    a bare constant would not say whose.
    """

    OPERATION_NAME: Final = "gen_ai.operation.name"
    PROVIDER_NAME: Final = "gen_ai.provider.name"
    CONVERSATION_ID: Final = "gen_ai.conversation.id"

    AGENT_ID: Final = "gen_ai.agent.id"
    AGENT_NAME: Final = "gen_ai.agent.name"
    AGENT_DESCRIPTION: Final = "gen_ai.agent.description"

    REQUEST_MODEL: Final = "gen_ai.request.model"
    RESPONSE_MODEL: Final = "gen_ai.response.model"
    RESPONSE_ID: Final = "gen_ai.response.id"
    RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"

    INPUT_MESSAGES: Final = "gen_ai.input.messages"
    OUTPUT_MESSAGES: Final = "gen_ai.output.messages"
    # Superseded by the two above in the convention, and the only pair LangFuse
    # currently maps. Kept until it does — see the module docstring.
    PROMPT: Final = "gen_ai.prompt"
    COMPLETION: Final = "gen_ai.completion"

    TOOL_NAME: Final = "gen_ai.tool.name"
    TOOL_TYPE: Final = "gen_ai.tool.type"
    TOOL_CALL_ID: Final = "gen_ai.tool.call.id"
    TOOL_CALL_ARGUMENTS: Final = "gen_ai.tool.call.arguments"
    TOOL_CALL_RESULT: Final = "gen_ai.tool.call.result"

    USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"
    USAGE_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.cache_read.input_tokens"
    USAGE_CACHE_CREATION_INPUT_TOKENS: Final = "gen_ai.usage.cache_creation.input_tokens"
    USAGE_REASONING_OUTPUT_TOKENS: Final = "gen_ai.usage.reasoning.output_tokens"


class LangfuseAttr:
    """Vendor keys LangFuse reads out of an OTLP span.

    Segregated from :class:`GenAiAttr` because they have a different lifetime:
    these exist to satisfy one consumer and are expected to *shrink* as that
    consumer adopts the convention, while the ``gen_ai.*`` set only grows. A
    reader deciding what to delete later needs the boundary to be visible.
    """

    OBSERVATION_TYPE: Final = "langfuse.observation.type"
    OBSERVATION_LEVEL: Final = "langfuse.observation.level"
    OBSERVATION_INPUT: Final = "langfuse.observation.input"
    OBSERVATION_OUTPUT: Final = "langfuse.observation.output"
    OBSERVATION_MODEL_NAME: Final = "langfuse.observation.model.name"
    OBSERVATION_USAGE_DETAILS: Final = "langfuse.observation.usage_details"
    OBSERVATION_COST_DETAILS: Final = "langfuse.observation.cost_details"
    OBSERVATION_COMPLETION_START_TIME: Final = "langfuse.observation.completion_start_time"

    SESSION_ID: Final = "langfuse.session.id"
    TRACE_NAME: Final = "langfuse.trace.name"
    TRACE_TAGS: Final = "langfuse.trace.tags"
    RELEASE: Final = "langfuse.release"


class GroveIdentityAttr:
    """Facts about a workspace that are true from the moment it launches.

    **Every key here is also stamped into the agent's own launch environment by
    :func:`~grove.core.otel_resource.compose_resource_attributes`,** and
    ``tests/core/test_telemetry_semconv.py`` asserts that. It has to hold: the
    agent's spans and Grove's spans describe one workspace, and a filter that
    finds only half of it is worse than one that finds neither, because the
    half looks complete. Adding a key here without adding it there fails the
    test rather than silently splitting a dashboard.
    """

    WORKSPACE_ID: Final = "grove.workspace.id"
    WORKSPACE_TITLE: Final = "grove.workspace.title"
    REPO: Final = "grove.repo"
    PROJECT: Final = "grove.project"
    BRANCH: Final = "grove.branch"
    BASE_BRANCH: Final = "grove.base_branch"
    WORKTREE: Final = "grove.worktree"
    PLACEMENT: Final = "grove.placement"
    AGENT_NAME: Final = "grove.agent.name"
    AGENT_KIND: Final = "grove.agent.kind"
    AGENT_VERSION: Final = "grove.agent.version"
    ORCHESTRATOR_NAME: Final = "grove.orchestrator.name"
    ORCHESTRATOR_VERSION: Final = "grove.orchestrator.version"
    RUNTIME: Final = "grove.runtime"
    TICKET_IDS: Final = "grove.ticket.ids"
    SESSION_ID: Final = "grove.session.id"


class GroveLiveAttr:
    """Facts that only became true after the agent process started.

    Separate from :class:`GroveIdentityAttr` because a resource attribute is
    frozen when the exporting SDK starts, so none of these can ever ride one.
    They exist only on spans Grove emits itself, which is the entire reason the
    forwarder tier exists. Do not move a key between the two classes without
    moving the fact it names.
    """

    AGENT_SESSION_ID: Final = "grove.agent.session.id"
    AGENT_STATE: Final = "grove.agent.state"
    AGENT_TURN_ID: Final = "grove.agent.turn.id"
    WORKSPACE_STATUS: Final = "grove.workspace.status"
    TICKET_URLS: Final = "grove.ticket.urls"
    PHASE: Final = "grove.phase"
    PHASE_NOTE: Final = "grove.phase.note"
    PHASE_UPDATED_AT: Final = "grove.phase.updated_at"
    TODO_TOTAL: Final = "grove.todo.total"
    TODO_COMPLETED: Final = "grove.todo.completed"
    # Depth of this observation in the spawn tree: 0 is the turn root, 1 a
    # sub-agent it spawned, 2 one that sub-agent spawned, and so on with no
    # ceiling. Recursion makes the tree correct; this makes it *queryable* —
    # "show me every third-level agent" is a filter, not a graph traversal.
    AGENT_DEPTH: Final = "grove.agent.depth"
    AGENT_PARENT_ID: Final = "grove.agent.parent.id"
    # How a sub-agent thread found its place in the tree: `spawn_tool` when the
    # harness recorded the call that created it, `turn_window` when it did not
    # and Grove placed the thread in the turn its own start instant falls in.
    # The distinction is not pedantry — one is evidence about causation and the
    # other is a clock, and a reader comparing two sub-agents needs to know
    # which they are looking at.
    AGENT_ATTACHMENT: Final = "grove.agent.attachment"


class GenAiOperation(StrEnum):
    """The semconv operation a span reports, and the first token of its name.

    A ``StrEnum`` so it interpolates into a span name and serializes onto an
    attribute without a ``.value`` at every call site. Only the members Grove
    can actually emit are listed — the convention defines more, and an unused
    member is a claim Grove makes about coverage it does not have.
    """

    CHAT = "chat"
    EXECUTE_TOOL = "execute_tool"
    INVOKE_AGENT = "invoke_agent"


@dataclass(frozen=True, slots=True)
class ObservationShape:
    """How one :data:`ObservationKind` is written: operation, name, vendor type.

    The three facts travel together because they are three views of one
    decision, and splitting them into parallel lookup tables is how a span
    named ``chat`` acquires ``gen_ai.operation.name=execute_tool``. The
    *subject* — a model, a tool name, an agent name — is both the second token
    of the span name and an attribute value, so :attr:`subject_key` names the
    attribute the name's own subject also lands on. That is what keeps the
    rendered name and the queryable attribute from disagreeing.
    """

    kind: ObservationKind
    operation: GenAiOperation
    subject_key: str
    langfuse_type: str

    def span_name(self, subject: str | None) -> str:
        """``"{operation} {subject}"``, per the convention's span-name rule.

        A subject Grove does not know degrades to the bare operation rather
        than to ``"chat None"`` or a placeholder: the convention permits the
        operation alone, and an invented subject is a fact a dashboard would
        group by.
        """
        return f"{self.operation} {subject}" if subject else str(self.operation)

    def attributes(self, subject: str | None) -> dict[str, str]:
        """The keys every observation of this kind carries, whatever else it has."""
        attributes = {
            GenAiAttr.OPERATION_NAME: str(self.operation),
            LangfuseAttr.OBSERVATION_TYPE: self.langfuse_type,
        }
        if subject:
            attributes[self.subject_key] = subject
        return attributes


class ObservationShapes:
    """The one mapping from Grove's kinds onto the convention.

    A class rather than a module-level dict so the lookup is a named behaviour
    (:meth:`for_kind`) with a total signature, and so the three shapes are read
    as one table — the table *is* the contract this module publishes.
    """

    AGENT: Final = ObservationShape(
        kind="agent",
        operation=GenAiOperation.INVOKE_AGENT,
        subject_key=GenAiAttr.AGENT_NAME,
        # Not in LangFuse's documented type list (span/generation/event), but
        # its ingestion accepts the value and renders it — confirmed against a
        # live instance on 2026-08-11, where Grove's existing `tool` and
        # `generation` types already land as TOOL and GENERATION. A consumer
        # that rejects it degrades to an untyped span, never to a dropped one.
        langfuse_type="agent",
    )
    GENERATION: Final = ObservationShape(
        kind="generation",
        operation=GenAiOperation.CHAT,
        subject_key=GenAiAttr.REQUEST_MODEL,
        langfuse_type="generation",
    )
    TOOL: Final = ObservationShape(
        kind="tool",
        operation=GenAiOperation.EXECUTE_TOOL,
        subject_key=GenAiAttr.TOOL_NAME,
        langfuse_type="tool",
    )

    _BY_KIND: Final[dict[ObservationKind, ObservationShape]] = {
        "agent": AGENT,
        "generation": GENERATION,
        "tool": TOOL,
    }

    @classmethod
    def for_kind(cls, kind: ObservationKind) -> ObservationShape:
        """The shape for a kind. Total over :data:`ObservationKind`, so a new
        kind fails type-checking here rather than falling through to a default
        that would emit a plausible wrong span. Resolved against a table built
        once at class definition: this runs per span, and at the volumes this
        vocabulary exists to serve, a dict rebuilt per call is real work."""
        return cls._BY_KIND[kind]


# ─── who the trace belongs to ────────────────────────────────────────────────


class TraceIdentity(BaseModel):
    """The workspace facts every span of a trace repeats, and the tags they become.

    One value object rather than a dict assembled at each producer, because
    three of them exist — the launch-time resource attributes, the live context
    span, and the transcript replay — and until this class they agreed only by
    coincidence. The replay tier in fact agreed with nobody: its spans carried
    the session join key and nothing else, so the richest tree Grove produces
    was the one a reader could not filter by repo, branch or agent.

    **The tags are a PROJECTION of these same facts, not a second vocabulary.**
    ``langfuse.trace.tags`` is the one surface a human filters on by clicking
    rather than by writing a query, and a tag set assembled independently of
    the attributes is a tag set that drifts from them. Deriving both from one
    object is what makes "tagged `branch:main`" and "``grove.branch=main``"
    provably the same claim.

    Every field defaults to empty and every empty field is OMITTED from both
    projections — the rule the rest of this module already follows. Absent
    means Grove had nothing to say; ``""`` would assert an empty fact.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = ""
    workspace_title: str = ""
    repo: str = ""
    project: str = ""
    branch: str = ""
    base_branch: str = ""
    worktree: str = ""
    runtime: str = ""
    placement: str = ""
    agent_name: str = ""
    agent_kind: str = ""
    agent_version: str = ""
    orchestrator_version: str = ""
    ticket_ids: tuple[str, ...] = ()
    phase: str = ""

    _ATTRIBUTE_FIELDS: ClassVar[tuple[tuple[str, str], ...]] = (
        (GroveIdentityAttr.WORKSPACE_ID, "workspace_id"),
        (GroveIdentityAttr.WORKSPACE_TITLE, "workspace_title"),
        (GroveIdentityAttr.REPO, "repo"),
        (GroveIdentityAttr.PROJECT, "project"),
        (GroveIdentityAttr.BRANCH, "branch"),
        (GroveIdentityAttr.BASE_BRANCH, "base_branch"),
        (GroveIdentityAttr.WORKTREE, "worktree"),
        (GroveIdentityAttr.RUNTIME, "runtime"),
        (GroveIdentityAttr.PLACEMENT, "placement"),
        (GroveIdentityAttr.AGENT_NAME, "agent_name"),
        (GroveIdentityAttr.AGENT_KIND, "agent_kind"),
        (GroveIdentityAttr.AGENT_VERSION, "agent_version"),
        (GroveIdentityAttr.ORCHESTRATOR_VERSION, "orchestrator_version"),
        (GroveLiveAttr.PHASE, "phase"),
    )
    """``(attribute key, field name)`` — the table IS the mapping, so adding a
    fact to the identity is one field plus one row here, never a hand-written
    dict at each producer."""

    _TAG_FIELDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("agent", "agent_kind"),
        ("agent-name", "agent_name"),
        ("runtime", "runtime"),
        ("placement", "placement"),
        ("repo", "repo"),
        ("project", "project"),
        ("branch", "branch"),
        ("phase", "phase"),
    )
    """``(tag prefix, field name)`` for the ``prefix:value`` tags.

    Deliberately a SUBSET of the attribute table. A tag is something a human
    clicks to narrow a list, so the fields here are the ones with few enough
    distinct values to be worth a facet; ``worktree`` (an absolute path) and
    ``workspace_title`` (free prose) stay attributes-only, where they are still
    queryable but do not bury the useful facets in a list nobody can scan.
    """

    ORCHESTRATOR_TAG: ClassVar[str] = "grove"
    """Present on every trace Grove emits, valueless on purpose: it answers
    *"did this come through Grove at all"*, which is the one question a mixed
    project — where a hand-run agent also exports — cannot answer from any
    ``prefix:value`` tag, because an absent fact yields no tag to filter on."""

    def attributes(self) -> dict[str, str]:
        """The ``grove.*`` facts, plus the one vendor key that is a fact about
        Grove rather than about the workspace.

        ``langfuse.release`` carries Grove's own version because that is what
        the field means — the release of the application producing the trace —
        and it is the value that makes "did this regress after the upgrade"
        answerable without reading an attribute blob.
        """
        resolved = {
            key: value
            for key, field_name in self._ATTRIBUTE_FIELDS
            if (value := getattr(self, field_name))
        }
        if self.ticket_ids:
            resolved[GroveIdentityAttr.TICKET_IDS] = ",".join(self.ticket_ids)
        if self.orchestrator_version:
            resolved[LangfuseAttr.RELEASE] = self.orchestrator_version
        return resolved

    def tags(self) -> tuple[str, ...]:
        """The same facts as a flat, clickable tag list.

        Sorted, because a tag list is rendered in the order it arrives and two
        replays of one session must not produce two visually different traces.
        The version tags are ``<what>:<version>`` so the agent's version and
        Grove's read the same way and neither is mistakable for the other.
        """
        tags = {self.ORCHESTRATOR_TAG}
        tags.update(
            f"{prefix}:{value}"
            for prefix, field_name in self._TAG_FIELDS
            if (value := getattr(self, field_name))
        )
        if self.orchestrator_version:
            tags.add(f"grove:{self.orchestrator_version}")
        if self.agent_version and self.agent_kind:
            tags.add(f"{self.agent_kind}:{self.agent_version}")
        tags.update(f"ticket:{ticket}" for ticket in self.ticket_ids if ticket)
        return tuple(sorted(tags))


# ─── the structured message document ─────────────────────────────────────────


class TextPart(BaseModel):
    """Prose contributed by one participant in a turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text"] = "text"
    content: str


class ToolCallPart(BaseModel):
    """A model's request to run a tool, as it appears inside its own output.

    ``arguments`` stays a decoded object rather than a JSON string because the
    convention models it as structured data — a consumer that wants to group by
    one argument should not have to re-parse a string Grove already parsed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: object | None = None


class ToolCallResponsePart(BaseModel):
    """What a tool returned, carried back on the next input message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call_response"] = "tool_call_response"
    id: str
    response: str


MessagePart = Annotated[
    TextPart | ToolCallPart | ToolCallResponsePart,
    Field(discriminator="type"),
]
"""One element of a message's ``parts`` array, discriminated by ``type`` so a
consumer (and Pydantic) can narrow it without inspecting field presence."""

ChatRole = Literal["system", "user", "assistant", "tool"]
"""Who produced a message. The convention's four; a harness role outside them
is narrowed at the boundary, never carried through."""


class ChatMessage(BaseModel):
    """One ``role``/``parts`` message in a ``gen_ai.*.messages`` document.

    This is the convention's own schema rather than a provider's envelope: the
    adapters have already normalized block shape away, so re-introducing an
    Anthropic or OpenAI wire format here would be modelling a semantic
    difference Grove deliberately does not carry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ChatRole
    parts: tuple[MessagePart, ...] = ()
    finish_reason: str | None = None

    @classmethod
    def of_text(cls, role: ChatRole, text: str) -> ChatMessage:
        """The common case — one participant, one block of prose."""
        return cls(role=role, parts=(TextPart(content=text),))

    def flatten(self) -> str:
        """This message rendered as plain prose, for consumers with no
        structured-message support.

        A tool call has no prose form, so it renders as its own compact JSON
        rather than being dropped: a completion that shows the model's text but
        silently omits the four tools it invoked is a misleading record of the
        turn, which is the exact failure this vocabulary exists to end.
        """
        rendered: list[str] = []
        for part in self.parts:
            if isinstance(part, TextPart):
                rendered.append(part.content)
            elif isinstance(part, ToolCallPart):
                rendered.append(
                    json.dumps(
                        {"tool_call": part.name, "arguments": part.arguments},
                        separators=(",", ":"),
                        default=str,
                        ensure_ascii=False,
                    )
                )
            else:
                rendered.append(part.response)
        return "\n".join(text for text in rendered if text)

    @staticmethod
    def encode(messages: Sequence[ChatMessage]) -> str | None:
        """The messages as one capped JSON attribute value, or ``None`` when
        there is nothing to say.

        Returning ``None`` for an empty document is the same rule the rest of
        this vocabulary follows: an absent attribute means Grove had nothing,
        while ``"[]"`` would assert that the turn genuinely exchanged no
        messages. Only one of those is ever true here.
        """
        if not messages:
            return None
        encoded = json.dumps(
            [message.model_dump(exclude_none=True) for message in messages],
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        )
        return encoded[:ATTR_TEXT_CAP]
