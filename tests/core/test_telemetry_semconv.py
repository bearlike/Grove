"""The safety net for `semconv.py`'s design decision: convention strings are
held as literals rather than imported from OTel's pre-stable `_incubating`
package (see that module's docstring). Two things must therefore hold, or the
decision quietly rots: every literal must still equal the SDK's own constant
(drift), and no *production* code may take the shortcut of importing the
pre-stable path directly (the whole reason for holding literals here). This
file is deliberately the one place that imports it.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.config import AgentSpec
from grove.core.contracts.tickets import TicketRef
from grove.core.otel_resource import compose_resource_attributes
from grove.core.telemetry.semconv import (
    ATTR_TEXT_CAP,
    ChatMessage,
    GenAiAttr,
    GenAiOperation,
    GroveIdentityAttr,
    ObservationShapes,
    TextPart,
    ToolCallPart,
    ToolCallResponsePart,
    TraceIdentity,
)
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus

gen_ai_attributes = pytest.importorskip(
    "opentelemetry.semconv._incubating.attributes.gen_ai_attributes",
    reason="drift test needs the optional `telemetry` extra",
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# ─── drift: every GenAiAttr literal must equal the SDK's own constant ────────

# Built explicitly rather than derived by string munging (e.g. upper-casing
# and prefixing `GEN_AI_`) — a derivation could silently skip a renamed or
# reshaped SDK constant and this table would then prove nothing.
_GEN_AI_ATTR_TO_SDK_NAME = {
    "OPERATION_NAME": "GEN_AI_OPERATION_NAME",
    "PROVIDER_NAME": "GEN_AI_PROVIDER_NAME",
    "CONVERSATION_ID": "GEN_AI_CONVERSATION_ID",
    "AGENT_ID": "GEN_AI_AGENT_ID",
    "AGENT_NAME": "GEN_AI_AGENT_NAME",
    "AGENT_DESCRIPTION": "GEN_AI_AGENT_DESCRIPTION",
    "REQUEST_MODEL": "GEN_AI_REQUEST_MODEL",
    "RESPONSE_MODEL": "GEN_AI_RESPONSE_MODEL",
    "RESPONSE_ID": "GEN_AI_RESPONSE_ID",
    "RESPONSE_FINISH_REASONS": "GEN_AI_RESPONSE_FINISH_REASONS",
    "INPUT_MESSAGES": "GEN_AI_INPUT_MESSAGES",
    "OUTPUT_MESSAGES": "GEN_AI_OUTPUT_MESSAGES",
    "PROMPT": "GEN_AI_PROMPT",
    "COMPLETION": "GEN_AI_COMPLETION",
    "TOOL_NAME": "GEN_AI_TOOL_NAME",
    "TOOL_TYPE": "GEN_AI_TOOL_TYPE",
    "TOOL_CALL_ID": "GEN_AI_TOOL_CALL_ID",
    "TOOL_CALL_ARGUMENTS": "GEN_AI_TOOL_CALL_ARGUMENTS",
    "TOOL_CALL_RESULT": "GEN_AI_TOOL_CALL_RESULT",
    "USAGE_INPUT_TOKENS": "GEN_AI_USAGE_INPUT_TOKENS",
    "USAGE_OUTPUT_TOKENS": "GEN_AI_USAGE_OUTPUT_TOKENS",
    "USAGE_CACHE_READ_INPUT_TOKENS": "GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS",
    "USAGE_CACHE_CREATION_INPUT_TOKENS": "GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS",
    "USAGE_REASONING_OUTPUT_TOKENS": "GEN_AI_USAGE_REASONING_OUTPUT_TOKENS",
}


def _grove_attr_names(cls: type) -> set[str]:
    """The `Final` class-attribute NAMES an attribute-namespace class declares
    — everything that is not itself a class/dunder."""
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and isinstance(value, str)
    }


def _grove_attr_values(cls: type) -> set[str]:
    """The wire-key strings an attribute-namespace class declares — what a
    consumer of the class actually emits onto a span or a resource."""
    return {
        value
        for name, value in vars(cls).items()
        if not name.startswith("_") and isinstance(value, str)
    }


def test_every_gen_ai_attr_key_has_an_explicit_sdk_mapping() -> None:
    """The mapping table above must cover every `GenAiAttr` member — a key
    added to `GenAiAttr` without a corresponding table entry would otherwise
    go unchecked by the drift assertion below rather than failing loudly."""
    assert _grove_attr_names(GenAiAttr) == set(_GEN_AI_ATTR_TO_SDK_NAME)


def test_gen_ai_attr_literals_match_the_sdk_constants() -> None:
    for grove_name, sdk_name in _GEN_AI_ATTR_TO_SDK_NAME.items():
        grove_value = getattr(GenAiAttr, grove_name)
        sdk_value = getattr(gen_ai_attributes, sdk_name)
        assert grove_value == sdk_value, (
            f"GenAiAttr.{grove_name}={grove_value!r} != gen_ai_attributes.{sdk_name}={sdk_value!r}"
        )


# ─── blast shield: no production import of the pre-stable SDK path ──────────


# Matches a real static import naming a `_incubating` module (`import
# opentelemetry.semconv._incubating...` / `from opentelemetry.semconv._incubating... import ...`)
# or a dynamic-import call handed a dotted string naming one
# (`importlib.import_module("...")`, `pytest.importorskip("...")`). Deliberately
# does NOT match a bare substring occurrence, because `semconv.py`'s own module
# docstring names the path in prose to explain why no import exists — that is
# documentation, not the dependency being guarded against.
_STATIC_IMPORT = re.compile(r"^(?:from\s+\S*_incubating\S*\s+import\s|import\s+\S*_incubating)")
_DYNAMIC_IMPORT = re.compile(r'(?:import_module|importorskip)\(\s*["\'][^"\']*_incubating')


def _incubating_import_sites() -> list[str]:
    """Every `.py` site under `src/` or `tests/` that actually IMPORTS the
    `_incubating` path, static or dynamic.

    A static import is always a single line, so it is checked per line. A
    dynamic-import CALL is checked over the whole file's text instead — a
    call wrapping its dotted-path string argument onto a following line (as
    this file's own `pytest.importorskip(...)` does, to stay under the line
    length limit) would otherwise be invisible to a per-line regex.
    """
    sites: list[str] = []
    for root_dir in ("src", "tests"):
        for path in (REPO_ROOT / root_dir).rglob("*.py"):
            content = path.read_text()
            for lineno, line in enumerate(content.splitlines(), start=1):
                if _STATIC_IMPORT.match(line.strip()):
                    sites.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
            for match in _DYNAMIC_IMPORT.finditer(content):
                lineno = content.count("\n", 0, match.start()) + 1
                sites.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    return sites


def test_only_this_test_file_imports_the_incubating_semconv_module() -> None:
    """Pins the module docstring's blast-shield claim: production code never
    imports OTel's pre-stable `_incubating` path, so that path moving cannot
    break anything Grove ships. This file's own import above is the one
    deliberate exception."""
    this_rel = str(Path(__file__).resolve().relative_to(REPO_ROOT))
    sites = _incubating_import_sites()
    assert sites, "sanity check: this test's own import should have been found"
    assert all(site.startswith(this_rel) for site in sites), sites


# ─── the four span-name/operation rules ──────────────────────────────────────


@pytest.mark.parametrize(
    ("shape", "operation"),
    [
        (ObservationShapes.AGENT, GenAiOperation.INVOKE_AGENT),
        (ObservationShapes.GENERATION, GenAiOperation.CHAT),
        (ObservationShapes.TOOL, GenAiOperation.EXECUTE_TOOL),
    ],
)
def test_span_name_is_operation_plus_subject(shape: object, operation: GenAiOperation) -> None:
    assert shape.operation is operation  # type: ignore[attr-defined]
    assert shape.span_name("thing") == f"{operation} thing"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "shape",
    [ObservationShapes.AGENT, ObservationShapes.GENERATION, ObservationShapes.TOOL],
)
def test_span_name_degrades_to_the_bare_operation_when_subject_is_unknown(shape: object) -> None:
    assert shape.span_name(None) == str(shape.operation)  # type: ignore[attr-defined]


def test_for_kind_resolves_every_observation_kind() -> None:
    assert ObservationShapes.for_kind("agent") is ObservationShapes.AGENT
    assert ObservationShapes.for_kind("generation") is ObservationShapes.GENERATION
    assert ObservationShapes.for_kind("tool") is ObservationShapes.TOOL


# ─── ChatMessage.encode round-trip ───────────────────────────────────────────


def test_chat_message_encode_round_trips_role_and_discriminated_parts() -> None:
    messages = [
        ChatMessage(role="system", parts=(TextPart(content="be terse"),)),
        ChatMessage(
            role="assistant",
            parts=(
                TextPart(content="running it"),
                ToolCallPart(id="call-1", name="grep", arguments={"pattern": "TODO"}),
            ),
            finish_reason="tool_calls",
        ),
        ChatMessage(
            role="tool",
            parts=(ToolCallResponsePart(id="call-1", response="3 matches"),),
        ),
    ]
    encoded = ChatMessage.encode(messages)
    assert encoded is not None
    assert len(encoded) <= ATTR_TEXT_CAP

    decoded = json.loads(encoded)
    assert decoded == [
        {"role": "system", "parts": [{"type": "text", "content": "be terse"}]},
        {
            "role": "assistant",
            "parts": [
                {"type": "text", "content": "running it"},
                {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "grep",
                    "arguments": {"pattern": "TODO"},
                },
            ],
            "finish_reason": "tool_calls",
        },
        {
            "role": "tool",
            "parts": [{"type": "tool_call_response", "id": "call-1", "response": "3 matches"}],
        },
    ]


def test_chat_message_encode_of_an_empty_list_is_none_not_an_empty_array() -> None:
    """An absent attribute means Grove had nothing to say; `"[]"` would
    assert the turn genuinely exchanged zero messages, which is a different
    and stronger claim the caller never made."""
    assert ChatMessage.encode([]) is None


# ─── identity sync: every GroveIdentityAttr key is also stamped by the
#     resource composer ─────────────────────────────────────────────────────

T0 = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)


def _state(**overrides: object) -> WorkspaceState:
    fields: dict[str, object] = {
        "id": "ws1",
        "title": "fix-auth",
        "repo_root": "/home/u/proj",
        "branch": "grove/fix-auth",
        "base_branch": "main",
        "worktree_path": "/home/u/proj/.worktrees/fix-auth",
        "tmux_session": "grove-fix-auth",
        "agent_name": "claude",
        "status": WorkspaceStatus.RUNNING,
        "created_at": T0,
        "updated_at": T0,
        "ticket_refs": [TicketRef(provider="gitea", id="42")],
        "runtime": Runtime.CONTAINER,
    }
    fields.update(overrides)
    return WorkspaceState(**fields)  # type: ignore[arg-type]


def _agent(**overrides: object) -> AgentSpec:
    fields: dict[str, object] = {"name": "claude", "command": "claude", "kind": "claude_code"}
    fields.update(overrides)
    return AgentSpec(**fields)  # type: ignore[arg-type]


def test_every_grove_identity_attr_key_is_emitted_by_compose_resource_attributes() -> None:
    """`GroveIdentityAttr`'s own docstring states this invariant: every key it
    defines is also stamped into the agent's launch environment, so the
    agent's spans and Grove's spans can be joined on `grove.*`. Every
    candidate field on `WorkspaceState`/`AgentSpec` is populated here so a
    key silently dropped from `compose_resource_attributes` fails loudly
    rather than passing because the value happened to be empty."""
    state = _state()
    agent = _agent()
    result = compose_resource_attributes(
        state, agent, existing=None, agent_version="2.1.226 (Claude Code)"
    )
    assert result is not None

    parsed_keys = {part.split("=", 1)[0] for part in result.split(",")}
    identity_keys = _grove_attr_values(GroveIdentityAttr)
    assert identity_keys, "sanity check: GroveIdentityAttr should declare keys"
    assert identity_keys <= parsed_keys, identity_keys - parsed_keys


# ─── the tag projection ──────────────────────────────────────────────────────


def _identity(**overrides: object) -> TraceIdentity:
    fields: dict[str, object] = {
        "workspace_id": "ws1",
        "workspace_title": "fix auth",
        "repo": "proj",
        "project": "proj/webapp",
        "branch": "grove/fix-auth",
        "runtime": "container",
        "placement": "worktree",
        "agent_name": "claude",
        "agent_kind": "claude_code",
        "agent_version": "2.1.226",
        "orchestrator_version": "0.0.6",
        "ticket_ids": ("42", "43"),
        "phase": "implementing",
    }
    fields.update(overrides)
    return TraceIdentity(**fields)  # type: ignore[arg-type]


def test_tags_and_attributes_describe_the_same_facts() -> None:
    """The tag list is a PROJECTION of the attributes, which is the property
    that stops a facet a human clicks from disagreeing with the attribute a
    query filters on. Every `prefix:value` tag's value must appear as some
    attribute's value on the same identity."""
    identity = _identity()
    attribute_values = set(identity.attributes().values())
    for tag in identity.tags():
        prefix, separator, value = tag.partition(":")
        if not separator or prefix in {"grove", "claude_code", "ticket"}:
            continue  # the version and ticket tags carry their own vocabulary
        assert value in attribute_values, f"{tag} names a fact no attribute states"


def test_tags_are_sorted_so_two_replays_of_one_session_render_alike() -> None:
    assert list(_identity().tags()) == sorted(_identity().tags())


def test_the_orchestrator_tag_survives_an_identity_that_knows_nothing() -> None:
    """An empty identity still answers "did this come through Grove at all",
    because an absent fact yields no `prefix:value` tag to filter on and that
    question would otherwise be unanswerable in a mixed project."""
    assert TraceIdentity().tags() == ("grove",)
    assert TraceIdentity().attributes() == {}


def test_versions_are_tagged_by_what_they_version() -> None:
    tags = _identity().tags()
    assert "grove:0.0.6" in tags
    assert "claude_code:2.1.226" in tags


def test_each_ticket_is_its_own_tag_but_one_joined_attribute() -> None:
    """A tag is clicked, so two tickets are two facets; an attribute is
    queried, so they stay one delimited value the resource half also uses."""
    identity = _identity()
    assert {"ticket:42", "ticket:43"} <= set(identity.tags())
    assert identity.attributes()[GroveIdentityAttr.TICKET_IDS] == "42,43"


def test_a_high_cardinality_fact_is_an_attribute_and_never_a_tag() -> None:
    """A worktree path and a free-prose title are queryable but would bury
    every useful facet in a list nobody can scan."""
    identity = _identity(worktree="/tmp/proj/.worktrees/fix-auth")
    assert identity.attributes()[GroveIdentityAttr.WORKTREE] == "/tmp/proj/.worktrees/fix-auth"
    buried = [tag for tag in identity.tags() if "/.worktrees/" in tag or "fix auth" in tag]
    assert not buried, buried
