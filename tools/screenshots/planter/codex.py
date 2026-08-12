"""Write a Codex-CLI-shaped rollout JSONL for one demo session.

Consumes the same `Transcript` the Claude writer does, projected onto Codex's
dual-record wire format instead (see ``src/grove/core/agents/codex.py``):
``response_item`` lines carry the conversation, ``event_msg`` lines carry
status and tokens. Conflating the two is the trap the real adapter guards
against, so this writer keeps them apart on the way in too.

A step left open leaves its ``function_call``/``custom_tool_call`` unresolved
and its ``turn_id`` without a matching ``task_complete``, which is what the
adapter's task pairing — and its trailing-open-call fallback — both read as
WORKING.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.screenshots.fixture import Tempo, ToolStep, Transcript

from .timeline import SessionTimeline


class CodexRolloutPlanter:
    """Writes one rollout per call into a Codex home.

    The file lands at
    ``<root>/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl``, matching the
    real path Codex CLI writes and ``_CodexHome`` globs for.
    """

    CLI_VERSION = "0.147.0"

    def __init__(self, *, root: Path, tempo: Tempo) -> None:
        self._root = root
        self._tempo = tempo

    @classmethod
    def from_env(cls, *, tempo: Tempo) -> CodexRolloutPlanter:
        """Bind to the ambient ``CODEX_HOME``, raising if it is unset."""
        return cls(root=Path(os.environ["CODEX_HOME"]), tempo=tempo)

    def plant(
        self,
        *,
        cwd: Path,
        session_id: str,
        branch: str,
        transcript: Transcript,
        model: str,
        base: datetime,
    ) -> Path:
        timeline = SessionTimeline.of(session_id, base=base, tempo=self._tempo)
        limits = timeline.rate_limits()
        clock = timeline.clock

        lines: list[dict[str, Any]] = [
            {
                "timestamp": clock.think(),
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": str(cwd),
                    "originator": "codex_cli_rs",
                    "cli_version": self.CLI_VERSION,
                    "git": {"branch": branch},
                },
            },
            {
                "timestamp": clock.think(),
                "type": "turn_context",
                "payload": {"cwd": str(cwd), "model": model},
            },
        ]

        tokens_in = tokens_out = tokens_cached = 0
        for turn in transcript.turns:
            lines.append(
                {
                    "timestamp": clock.think(),
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": turn.prompt}],
                    },
                }
            )
            turn_id = f"turn-{uuid.uuid4().hex[:8]}"
            lines.append(
                {
                    "timestamp": clock.think(),
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": turn_id},
                }
            )
            if turn.thinking:
                lines.append(
                    {
                        "timestamp": clock.think(),
                        "type": "response_item",
                        "payload": {
                            "type": "reasoning",
                            "summary": [{"type": "summary_text", "text": turn.thinking}],
                        },
                    }
                )

            for step in turn.resolved_steps():
                call_id = self._call_id()
                lines.append(self._call(timeline, step, call_id))
                lines.append(self._output(timeline, step, call_id))

            open_step = turn.open_step
            if open_step is not None:
                # Deliberately no closing message and no `task_complete`: the
                # unmatched `task_started` plus the unresolved call are the two
                # independent signals `_EventState.state` and the tail fallback
                # both read as WORKING.
                lines.append(self._call(timeline, open_step, self._call_id()))
            else:
                lines.append(
                    {
                        "timestamp": clock.think(),
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": turn.reply}],
                        },
                    }
                )
                lines.append(
                    {
                        "timestamp": clock.think(),
                        "type": "event_msg",
                        "payload": {"type": "task_complete", "turn_id": turn_id},
                    }
                )

            # One `token_count` per turn, covering every model request the turn
            # made — the tool calls inside it as well as the reply. Codex
            # reports `input_tokens` INCLUDING `cached_input_tokens` (the
            # adapter nets them apart), a cumulative running total, and its own
            # rate-limit envelope on the same line, which is the whole evidence
            # base the Codex quota provider tail-reads.
            usage = timeline.context.turn_usage(requests=len(turn.tools) + 1)
            tokens_in += usage["input_tokens"]
            tokens_out += usage["output_tokens"]
            tokens_cached += usage["cached_input_tokens"]
            lines.append(
                {
                    "timestamp": clock.think(),
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": tokens_in,
                                "cached_input_tokens": tokens_cached,
                                "output_tokens": tokens_out,
                            },
                            "last_token_usage": usage,
                        },
                        "rate_limits": limits.spend(),
                    },
                }
            )

        path = self._path(session_id, base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        return path

    def _path(self, session_id: str, base: datetime) -> Path:
        return (
            self._root
            / "sessions"
            / base.strftime("%Y")
            / base.strftime("%m")
            / base.strftime("%d")
            / f"rollout-{base.strftime('%Y-%m-%dT%H-%M-%S')}-{session_id}.jsonl"
        )

    @staticmethod
    def _call_id() -> str:
        return f"call_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _call(timeline: SessionTimeline, step: ToolStep, call_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = (
            {
                "type": "custom_tool_call",
                "name": step.name,
                "call_id": call_id,
                "input": step.input,
            }
            if step.is_patch
            else {
                "type": "function_call",
                "name": step.name,
                "call_id": call_id,
                "arguments": json.dumps(step.input),
            }
        )
        return {"timestamp": timeline.clock.think(), "type": "response_item", "payload": payload}

    @staticmethod
    def _output(timeline: SessionTimeline, step: ToolStep, call_id: str) -> dict[str, Any]:
        return {
            "timestamp": timeline.clock.after(step),
            "type": "response_item",
            "payload": {
                "type": ("custom_tool_call_output" if step.is_patch else "function_call_output"),
                "call_id": call_id,
                "output": step.result,
            },
        }
