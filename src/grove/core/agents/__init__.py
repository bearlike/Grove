"""Tool-agnostic agent-session introspection.

The DRY core of the Activity Dashboard (epic #11 §3): an :class:`AgentAdapter`
turns one agent tool's transcript into the normalized :class:`AgentActivity`
model, and ``get_adapter(kind)`` selects the implementation. Clients and the
``ActivityService`` consume only the model types here — never a tool's native
format — so a new tool slots in behind this surface without touching them.

Public surface only. Concrete adapters (``ClaudeCodeAdapter``, ``GenericAdapter``)
are reached through ``get_adapter``; their modules stay internal.
"""

from __future__ import annotations

from grove.core.agents.base import AgentAdapter
from grove.core.agents.model import (
    ATTENTION_STATES,
    FILE_EDIT_TOOL_NAMES,
    QUESTION_TOOL_NAMES,
    TASK_TOOL_NAMES,
    TODO_TOOL_NAMES,
    AgentActivity,
    AgentActivityState,
    AgentMessage,
    AgentQuestion,
    AgentQuestionKind,
    AgentQuestionOption,
    AgentSession,
    AnswerSelection,
    ContentBlock,
    ContentBlockType,
    ControlKind,
    ControlScope,
    DigestEntry,
    FileEdit,
    FinalResult,
    MessageRole,
    OrderedDigest,
    SessionControl,
    SessionControls,
    SessionProvenance,
    SessionSummary,
    SessionTurn,
    TaskBoard,
    TodoItem,
    TodoList,
    TodoStatus,
    TokenUsage,
    final_result_from_messages,
    latest_todo_from_messages,
)
from grove.core.agents.registry import (
    MODEL_CATALOG_CAP,
    all_adapters,
    get_adapter,
    resolve_models,
)

__all__ = [
    "ATTENTION_STATES",
    "FILE_EDIT_TOOL_NAMES",
    "MODEL_CATALOG_CAP",
    "QUESTION_TOOL_NAMES",
    "TASK_TOOL_NAMES",
    "TODO_TOOL_NAMES",
    "AgentActivity",
    "AgentActivityState",
    "AgentAdapter",
    "AgentMessage",
    "AgentQuestion",
    "AgentQuestionKind",
    "AgentQuestionOption",
    "AgentSession",
    "AnswerSelection",
    "ContentBlock",
    "ContentBlockType",
    "ControlKind",
    "ControlScope",
    "DigestEntry",
    "FileEdit",
    "FinalResult",
    "MessageRole",
    "OrderedDigest",
    "SessionControl",
    "SessionControls",
    "SessionProvenance",
    "SessionSummary",
    "SessionTurn",
    "TaskBoard",
    "TodoItem",
    "TodoList",
    "TodoStatus",
    "TokenUsage",
    "all_adapters",
    "final_result_from_messages",
    "get_adapter",
    "latest_todo_from_messages",
    "resolve_models",
]
