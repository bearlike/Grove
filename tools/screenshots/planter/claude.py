"""Write a Claude-Code-style JSONL transcript for one demo session.

Each turn is one human prompt, an optional ``thinking`` block, zero or more
resolved tool round-trips, and a closing assistant reply — UNLESS the very last
tool step of the very last turn is left open, in which case the transcript ends
mid-tool-call (no closing reply, no result line) and the session reads WORKING.
That is the same tail rule the real adapter uses: a trailing
``stop_reason: "tool_use"`` with no following result.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.screenshots.fixture import Tempo, ToolStep, Transcript, Turn

from .timeline import SessionTimeline


class ClaudeTranscriptPlanter:
    """Writes one session per call into a Claude Code profile root.

    ``root`` is the profile directory the reading side will scan — the sandbox
    ``CLAUDE_CONFIG_DIR`` the capture already exported, never a real one.
    """

    def __init__(self, *, root: Path, tempo: Tempo) -> None:
        self._root = root
        self._tempo = tempo

    @classmethod
    def from_env(cls, *, tempo: Tempo) -> ClaudeTranscriptPlanter:
        """Bind to the ambient ``CLAUDE_CONFIG_DIR``, raising if it is unset.

        Deliberately loud: an unset variable means the run would write into the
        developer's real profile, which is the one failure this whole sandbox
        exists to prevent.
        """
        return cls(root=Path(os.environ["CLAUDE_CONFIG_DIR"]), tempo=tempo)

    def plant(
        self,
        *,
        cwd: Path,
        session_id: str,
        transcript: Transcript,
        model: str,
        base: datetime,
    ) -> Path:
        timeline = SessionTimeline.of(session_id, base=base, tempo=self._tempo)
        lines: list[dict[str, Any]] = []
        if transcript.ai_title is not None:
            lines.append(
                {"type": "ai-title", "aiTitle": transcript.ai_title, "sessionId": session_id}
            )
        for index, turn in enumerate(transcript.turns):
            lines.extend(self._turn(timeline, turn, cwd=cwd, model=model, first=index == 0))

        folder = self._root / "projects" / self.encode_cwd(cwd)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session_id}.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def encode_cwd(cwd: Path) -> str:
        """Claude Code's transcript folder name for a worktree.

        Mirrors the adapter's forward encoding (lossy, non-reversible): every
        non-alphanumeric character becomes a dash. Inlined here so the tool does
        not depend on a private adapter helper.
        """
        return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))

    def _turn(
        self,
        timeline: SessionTimeline,
        turn: Turn,
        *,
        cwd: Path,
        model: str,
        first: bool,
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = [
            {
                "type": "user",
                "uuid": timeline.uuid(),
                "timestamp": timeline.clock.human(first=first),
                "isSidechain": False,
                "cwd": str(cwd),
                "sessionId": timeline.session_id,
                "message": {"role": "user", "content": turn.prompt},
            }
        ]
        if turn.thinking:
            lines.append(
                self._assistant(
                    timeline,
                    model,
                    timeline.clock.think(),
                    "tool_use",
                    [{"type": "thinking", "thinking": turn.thinking}],
                )
            )

        for step in turn.resolved_steps():
            tool_use_id = timeline.hex_id("tu")
            lines.append(
                self._assistant(
                    timeline,
                    model,
                    timeline.clock.think(),
                    "tool_use",
                    [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": step.name,
                            "input": step.input,
                        }
                    ],
                )
            )
            lines.append(self._tool_result(timeline, step, tool_use_id))

        open_step = turn.open_step
        if open_step is not None:
            lines.append(
                self._assistant(
                    timeline,
                    model,
                    timeline.clock.think(),
                    "tool_use",
                    [
                        {
                            "type": "tool_use",
                            "id": timeline.hex_id("tu"),
                            "name": open_step.name,
                            "input": open_step.input,
                        }
                    ],
                )
            )
        else:
            lines.append(
                self._assistant(
                    timeline,
                    model,
                    timeline.clock.think(),
                    "end_turn",
                    [{"type": "text", "text": turn.reply}],
                )
            )
        return lines

    @staticmethod
    def _tool_result(timeline: SessionTimeline, step: ToolStep, tool_use_id: str) -> dict[str, Any]:
        return {
            "type": "user",
            "uuid": timeline.uuid(),
            "timestamp": timeline.clock.after(step),
            "isSidechain": False,
            "sessionId": timeline.session_id,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": step.result,
                        "is_error": step.is_error,
                    }
                ],
            },
        }

    @staticmethod
    def _assistant(
        timeline: SessionTimeline,
        model: str,
        timestamp: str,
        stop_reason: str,
        content: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """One ``type:"assistant"`` record — shared by the thinking, tool-use
        and closing-text lines so the boilerplate lives once."""
        usage = timeline.context.request()
        return {
            "type": "assistant",
            "uuid": timeline.uuid(),
            "requestId": timeline.hex_id("req"),
            "timestamp": timestamp,
            "isSidechain": False,
            "sessionId": timeline.session_id,
            "message": {
                "id": timeline.hex_id("msg"),
                "role": "assistant",
                "model": model,
                "stop_reason": stop_reason,
                "usage": usage,
                "content": content,
            },
        }
