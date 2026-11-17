"""Fold an owned Claude stream into "what is this session doing right now".

The owner already parses every stream-json frame; this keeps the few facts a
working loader needs, in memory, at the cost of a dict lookup per frame. It
publishes NOTHING by itself — the worker reads :meth:`LivePulse.view` only while
somebody is watching, so an unwatched session pays for the fold and nothing else.

Sources, all measured on Claude Code 2.1.289 (2026-10-10) without
``--include-partial-messages``: ``system/thinking_tokens`` carries the running
``estimated_tokens`` while the model deliberates (about once a second);
``assistant`` frames carry one content block each plus the request's
``usage.output_tokens``; a root ``tool_use`` stays in flight until a ``user``
frame carries its ``tool_result``; ``system/task_started`` / ``task_progress``
/ ``task_notification`` bracket a sub-agent and name its last tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from grove.core.contracts.live import LivePulseView, LiveStep

# A tool call's input is shown as a hint beside its name; the full body is the
# transcript's job, so the loader takes one short line.
_DETAIL_CHARS: Final = 80
_DETAIL_KEYS: Final = ("description", "command", "file_path", "pattern", "url", "query", "prompt")


class LivePulse:
    """The current step of one owned session, folded frame by frame (pure)."""

    __slots__ = (
        "_message_outputs",
        "_since",
        "_step",
        "_subagent",
        "_subagent_tool",
        "_subagents",
        "_thinking",
        "_tool",
        "_tool_detail",
        "_tools",
        "version",
    )

    def __init__(self) -> None:
        self._step: LiveStep | None = None
        self._since: datetime | None = None
        self._thinking: int | None = None
        # Per API response: one message id carries several frames that repeat
        # the same usage, so the turn total sums each id once, latest figure.
        self._message_outputs: dict[str, int] = {}
        self._tools: dict[str, tuple[str, str | None]] = {}
        self._tool: str | None = None
        self._tool_detail: str | None = None
        self._subagents: dict[str, str] = {}
        self._subagent: str | None = None
        self._subagent_tool: str | None = None
        # Bumped on every change a reader would see; the worker publishes only
        # when it moved, so an idle watched session sends nothing.
        self.version = 0

    def observe(self, frame: Mapping[str, Any], now: datetime) -> None:
        """Fold one received frame; anything unrecognised changes nothing."""
        kind = frame.get("type")
        if kind == "system":
            self._system(frame, now)
        elif kind == "assistant":
            self._assistant(frame, now)
        elif kind == "user":
            self._user(frame, now)
        elif kind == "result":
            self._reset()

    def view(self) -> LivePulseView:
        return LivePulseView(
            step=self._step,
            since=self._since,
            thinking_tokens=self._thinking,
            output_tokens=sum(self._message_outputs.values()) or None,
            tool=self._tool,
            tool_detail=self._tool_detail,
            subagents=len(self._subagents),
            subagent=self._subagent,
            subagent_tool=self._subagent_tool,
        )

    # ── frame kinds ───────────────────────────────────────────────────────
    def _system(self, frame: Mapping[str, Any], now: datetime) -> None:
        subtype = frame.get("subtype")
        if subtype == "thinking_tokens":
            tokens = _int(frame.get("estimated_tokens"))
            if tokens is not None:
                self._thinking = tokens
            self._enter("thinking", now)
        elif subtype in ("task_started", "task_progress"):
            self._task_moved(frame, started=subtype == "task_started", now=now)
        elif subtype == "task_notification":
            self._task_ended(frame, now)

    def _task_moved(self, frame: Mapping[str, Any], *, started: bool, now: datetime) -> None:
        task_id = frame.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return
        # `task_*` frames also bracket a BACKGROUND SHELL command
        # (`task_type: local_bash` — 165 of 167 task_started frames across the
        # live panes on 2026-10-10), which is not a sub-agent. Its progress
        # frames carry no type, so a task is admitted on its start frame only,
        # and progress for an unknown task never invents one.
        if started and frame.get("task_type") == "local_bash":
            return
        if not started and task_id not in self._subagents:
            return
        label = _text(frame.get("description")) or _text(frame.get("subagent_type")) or "task"
        self._subagents[task_id] = label
        self._subagent = label
        last_tool = _text(frame.get("last_tool_name"))
        if last_tool is not None or started:
            self._subagent_tool = last_tool
        if not self._tools:
            self._enter("subagent", now)
        self.version += 1

    def _task_ended(self, frame: Mapping[str, Any], now: datetime) -> None:
        task_id = frame.get("task_id")
        if not isinstance(task_id, str) or self._subagents.pop(task_id, None) is None:
            return
        if not self._subagents:
            self._subagent = self._subagent_tool = None
            if self._step == "subagent":
                self._enter(None, now)
        else:
            self._subagent = next(reversed(self._subagents.values()))
        self.version += 1

    def _assistant(self, frame: Mapping[str, Any], now: datetime) -> None:
        if frame.get("parent_tool_use_id") is not None:
            return
        message = frame.get("message")
        if not isinstance(message, dict):
            return
        message_id = message.get("id")
        usage = message.get("usage")
        if isinstance(message_id, str) and isinstance(usage, dict):
            output = _int(usage.get("output_tokens"))
            if output is not None and self._message_outputs.get(message_id) != output:
                self._message_outputs[message_id] = output
                self.version += 1
        content = message.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_id, name = block.get("id"), _text(block.get("name"))
                if isinstance(tool_id, str) and name is not None:
                    self._tools[tool_id] = (name, _detail(block.get("input")))
                    self._show_latest_tool()
                    self._enter("tool", now)
            elif block_type == "thinking":
                self._enter("thinking", now)
            elif block_type == "text" and not self._tools:
                self._enter("generating", now)

    def _user(self, frame: Mapping[str, Any], now: datetime) -> None:
        if frame.get("parent_tool_use_id") is not None:
            return
        message = frame.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        settled = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                settled |= self._tools.pop(str(block.get("tool_use_id")), None) is not None
        if not settled:
            return
        if self._tools:
            self._show_latest_tool()
            self.version += 1
        else:
            self._tool = self._tool_detail = None
            # The model reads the result next; a sub-agent still running keeps
            # its claim, since it is what the session is waiting on.
            self._enter("subagent" if self._subagents else "thinking", now)
            self._thinking = None

    # ── state ─────────────────────────────────────────────────────────────
    def _enter(self, step: LiveStep | None, now: datetime) -> None:
        if step == self._step:
            if step == "thinking":
                self.version += 1
            return
        if step != "thinking":
            self._thinking = None
        self._step = step
        self._since = now if step is not None else None
        self.version += 1

    def _show_latest_tool(self) -> None:
        name, detail = next(reversed(self._tools.values()))
        self._tool, self._tool_detail = name, detail

    def _reset(self) -> None:
        """A ``result`` closes the turn: nothing is generating any more."""
        self._step = self._since = self._thinking = None
        self._message_outputs.clear()
        self._tools.clear()
        self._tool = self._tool_detail = None
        self._subagents.clear()
        self._subagent = self._subagent_tool = None
        self.version += 1


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _detail(tool_input: object) -> str | None:
    """One short line describing a call, from the first argument that names it."""
    if not isinstance(tool_input, dict):
        return None
    for key in _DETAIL_KEYS:
        value = _text(tool_input.get(key))
        if value is not None:
            line = value.splitlines()[0]
            if key == "file_path":
                # The file's name is what a glance needs; the whole path is the
                # transcript's job, and it is mostly the worktree prefix.
                line = line.rstrip("/").rsplit("/", 1)[-1] or line
            return line if len(line) <= _DETAIL_CHARS else f"{line[: _DETAIL_CHARS - 1]}…"
    return None


__all__ = ["LivePulse"]
