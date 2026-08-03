"""Tool-agnostic agent-session introspection.

The DRY core of the Activity Dashboard: an :class:`AgentAdapter`
turns one agent tool's transcript into the normalized :class:`AgentActivity`
model, and ``get_adapter(kind)`` selects the implementation. Clients and the
``ActivityService`` consume only the model types here — never a tool's native
format — so a new tool slots in behind this surface without touching them.

Public surface only. Concrete adapters (``ClaudeCodeAdapter``, ``GenericAdapter``)
are reached through ``get_adapter``; their modules stay internal.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Eagerly visible to mypy/IDEs; never executed at runtime.
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
        SessionRef,
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

# Exported name -> the internal module that defines it. Resolution is deferred
# (PEP 562) because this package's `registry` pulls in every concrete adapter --
# `claude_code` -> `tmux` -> `libtmux`, `mewbo` -> `httpx` -> `pydantic`. Eagerly,
# that made `import grove.core.agents.hook` (stdlib + `paths` + `model` only) cost
# ~1.2s, which the per-event `grove agent-hook` fork paid on every hook event of
# every session in the fleet. Importing a leaf must stay leaf-cheap.
_EXPORTS: dict[str, str] = {
    "ATTENTION_STATES": "grove.core.agents.model",
    "FILE_EDIT_TOOL_NAMES": "grove.core.agents.model",
    "MODEL_CATALOG_CAP": "grove.core.agents.registry",
    "QUESTION_TOOL_NAMES": "grove.core.agents.model",
    "TASK_TOOL_NAMES": "grove.core.agents.model",
    "TODO_TOOL_NAMES": "grove.core.agents.model",
    "AgentActivity": "grove.core.agents.model",
    "AgentActivityState": "grove.core.agents.model",
    "AgentAdapter": "grove.core.agents.base",
    "AgentMessage": "grove.core.agents.model",
    "AgentQuestion": "grove.core.agents.model",
    "AgentQuestionKind": "grove.core.agents.model",
    "AgentQuestionOption": "grove.core.agents.model",
    "AgentSession": "grove.core.agents.model",
    "AnswerSelection": "grove.core.agents.model",
    "ContentBlock": "grove.core.agents.model",
    "ContentBlockType": "grove.core.agents.model",
    "ControlKind": "grove.core.agents.model",
    "ControlScope": "grove.core.agents.model",
    "DigestEntry": "grove.core.agents.model",
    "FileEdit": "grove.core.agents.model",
    "FinalResult": "grove.core.agents.model",
    "MessageRole": "grove.core.agents.model",
    "OrderedDigest": "grove.core.agents.model",
    "SessionControl": "grove.core.agents.model",
    "SessionControls": "grove.core.agents.model",
    "SessionProvenance": "grove.core.agents.model",
    "SessionRef": "grove.core.agents.model",
    "SessionSummary": "grove.core.agents.model",
    "SessionTurn": "grove.core.agents.model",
    "TaskBoard": "grove.core.agents.model",
    "TodoItem": "grove.core.agents.model",
    "TodoList": "grove.core.agents.model",
    "TodoStatus": "grove.core.agents.model",
    "TokenUsage": "grove.core.agents.model",
    "all_adapters": "grove.core.agents.registry",
    "final_result_from_messages": "grove.core.agents.model",
    "get_adapter": "grove.core.agents.registry",
    "latest_todo_from_messages": "grove.core.agents.model",
    "resolve_models": "grove.core.agents.registry",
}

# Spelled as a literal (not derived from `_EXPORTS`) because type checkers only
# understand a static `__all__`; the test suite asserts the two agree.
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
    "SessionRef",
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


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name by importing its owning module on demand."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    # Cache on the package so subsequent lookups skip __getattr__ entirely.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return __all__
