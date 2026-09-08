"""Write hook callbacks to the daemon-watched spool without importing Grove."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, Final

MAX_STDIN_BYTES: Final = 1_048_576
_STATE_HOME_ENV: Final = "XDG_STATE_HOME"
_SPOOL_SUFFIX: Final = ".json"
_STATUSLINE_FLAG: Final = "--statusline"
_DAEMON_URL_FLAG: Final = "--daemon-url"
_USER_PROMPT_EVENT: Final = "UserPromptSubmit"

LegacyHandler = Callable[[Sequence[str], bytes], int]


class HookProducer:
    """Persist one ordinary hook callback for the daemon's filesystem intake.

    The producer deliberately knows only the state-directory protocol shared by
    the existing sidecar reader. It leaves event interpretation and daemon
    delivery to the persistent process, so an unavailable daemon leaves its
    callback on disk for the next reader. ``UserPromptSubmit`` is the exception:
    Claude Code consumes its stdout synchronously, so that callback lazily
    re-enters the established handler.
    """

    MAX_STDIN_BYTES: Final = MAX_STDIN_BYTES

    def __init__(self, legacy: LegacyHandler | None = None) -> None:
        self._legacy = self._run_legacy if legacy is None else legacy

    def run(self, argv: Sequence[str] | None = None, *, stdin: BinaryIO | None = None) -> int:
        """Accept one bounded callback and always return the hook-safe status."""
        args = tuple(sys.argv[1:] if argv is None else argv)
        raw = (sys.stdin.buffer if stdin is None else stdin).read(self.MAX_STDIN_BYTES + 1)
        if len(raw) > self.MAX_STDIN_BYTES:
            return 0
        mode = self._mode(args)
        if mode is None:
            return self._legacy(args, raw)
        payload = self._payload(raw)
        if mode == _STATUSLINE_FLAG:
            self._spool(payload, suffix=".statusline.json")
            return 0
        if self._event_name(raw) == _USER_PROMPT_EVENT:
            return self._legacy(args, raw)
        self._spool(payload, suffix=_SPOOL_SUFFIX)
        return 0

    @staticmethod
    def _mode(argv: Sequence[str]) -> str | None:
        """Classify the legacy flags without reimplementing their semantics."""
        index = 0
        statusline = False
        while index < len(argv):
            arg = argv[index]
            if arg == _STATUSLINE_FLAG:
                statusline = True
                index += 1
            elif arg == _DAEMON_URL_FLAG:
                index += 2
            else:
                return None
        return _STATUSLINE_FLAG if statusline else "event"

    @staticmethod
    def _event_name(raw: bytes) -> str | None:
        """Read only the discriminator needed to preserve the stdout contract."""
        payload = HookProducer._payload(raw)
        event = payload.get("hook_event_name") if payload is not None else None
        return event if isinstance(event, str) else None

    @staticmethod
    def _payload(raw: bytes) -> dict[str, object] | None:
        """Decode a hook object once, without assigning meaning beyond its shape."""
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _spool_dir() -> Path:
        """Resolve the lightweight equivalent of Grove's standard state location."""
        configured = os.environ.get(_STATE_HOME_ENV)
        if configured:
            state_home = Path(configured)
        elif sys.platform == "darwin":
            state_home = Path.home() / "Library" / "Application Support"
        elif sys.platform == "win32":
            state_home = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            state_home = Path.home() / ".local" / "state"
        return state_home / "grove" / "agent-sidecars" / "spool"

    @classmethod
    def _spool(cls, payload: dict[str, object] | None, *, suffix: str) -> None:
        """Atomically publish a transport envelope into the daemon-watched spool."""
        if payload is None:
            return
        spool = cls._spool_dir()
        raw = json.dumps(
            {
                "grove_hook_envelope": 1,
                "payload": payload,
                "tmux_pane": os.environ.get("TMUX_PANE") or None,
            },
            separators=(",", ":"),
        ).encode()
        staged: Path | None = None
        try:
            spool.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=spool, prefix=".hook-", suffix=".tmp", delete=False
            ) as stream:
                staged = Path(stream.name)
                stream.write(raw)
            target = spool / f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex}{suffix}"
            os.replace(staged, target)
        except OSError:
            if staged is not None:
                with suppress(OSError):
                    staged.unlink(missing_ok=True)

    @staticmethod
    def _run_legacy(argv: Sequence[str], raw: bytes) -> int:
        """Replay one consumed payload through the full handler only when required."""
        # Kept here rather than at module import time: normal callbacks must not
        # pay for the hook module (and its logging/config transitive imports).
        from grove.core.agents.hook import run_hook_from_stdin  # noqa: PLC0415

        prior = sys.stdin
        try:
            sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
            return run_hook_from_stdin(argv)
        finally:
            sys.stdin = prior


def run_hook_from_stdin(argv: Sequence[str] | None = None) -> int:
    """Console-script edge for the producer-only hook process."""
    return HookProducer().run(argv)
