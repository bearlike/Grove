"""The Grove config the demo resolves, and getting it to the daemon as well.

Two consumers with two different reaches: `resolve()` serves the managers built
in THIS process (the TUI capture builds its own straight from it), and
`publish()` writes the parts a SEPARATE process has to see — the web capture
runs the daemon out of process, where it resolves its own cascade.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from tools.screenshots.fixture import DemoWorld

from grove.core import paths
from grove.core.config import GroveConfig

_AGENTS_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "agents"


class DemoConfig:
    """Shipped defaults, with the agent registry pointed at the stub scripts.

    The `claude` entry declares ``kind: "claude_code"`` and `codex` declares
    ``kind: "codex"`` so the introspection layer treats each as a real session
    and reads the planted transcripts/rollouts. `aider` and `shell` stay
    generic.

    ``container.enabled`` is forced OFF. The engine's own default flipped to
    container-on-create (the containerized-workspaces epic), and the stub agent
    scripts are host paths that do not exist inside a devcontainer — a
    container-runtime demo workspace fails its agent launch with "exited with
    status 127" and never plants a live pane, and every ``devcontainer up`` also
    makes the whole capture run minutes slower for no visual difference. Host
    runtime is what every screenshot in the pipeline has always assumed.
    """

    STUBS: Final[dict[str, str]] = {
        "claude": "stub-claude.sh",
        "codex": "stub-codex.sh",
        "aider": "stub-aider.sh",
    }

    def __init__(self, world: DemoWorld) -> None:
        self._world = world

    def resolve(self) -> GroveConfig:
        return GroveConfig.model_validate(
            {
                "tmux": {"session_prefix": "grove-", "activity_threshold_seconds": 3},
                "container": {"enabled": False},
                "usage": {
                    "pricing": self._world.pricing.as_config(),
                    "quota": {"profiles": self.quota_profiles()},
                },
                "agents": [
                    {
                        "name": "claude",
                        "command": str(_AGENTS_DIR / self.STUBS["claude"]),
                        "kind": "claude_code",
                        "description": "Anthropic Claude Code",
                    },
                    {
                        "name": "codex",
                        "command": str(_AGENTS_DIR / self.STUBS["codex"]),
                        "kind": "codex",
                        "description": "OpenAI Codex CLI",
                    },
                    {
                        "name": "aider",
                        "command": str(_AGENTS_DIR / self.STUBS["aider"]),
                        "description": "Aider AI pair-programmer",
                    },
                    {"name": "shell", "command": "$SHELL", "description": "Plain shell"},
                ],
            }
        )

    @staticmethod
    def quota_profiles() -> dict[str, list[str]]:
        """The two subscriptions the demo shows: one Claude Code, one Codex.

        Quota is opt-in per profile root (`usage.quota.profiles` is empty by
        default), so without this neither account is ever asked for. Both roots
        are the sandbox ones the caller already exported — never a real profile.

        The Codex arm is genuinely real end to end: its provider tail-reads the
        ``rate_limits`` block `CodexRolloutPlanter` writes onto every
        ``token_count`` line, exactly as the CLI does. The Claude arm spends a
        live OAuth request and cannot work offline; the capture harness fakes
        that one call, and everything upstream of it stays real.
        """
        return {
            "claude_code": [os.environ["CLAUDE_CONFIG_DIR"]],
            "codex": [os.environ["CODEX_HOME"]],
        }

    def publish(self) -> None:
        """Merge this demo's pricing and quota selection into the USER config.

        A read-modify-write merge rather than a write, because the capture
        harness plants a Claude credential and its own quota selection into the
        same file before seeding, and clobbering that would silently un-select
        the account whose window the screenshot is for.
        """
        config_path = paths.user_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        usage = data.setdefault("usage", {})
        usage["pricing"] = self._world.pricing.as_config()
        profiles = usage.setdefault("quota", {}).setdefault("profiles", {})
        for provider, roots in self.quota_profiles().items():
            profiles.setdefault(provider, roots)
        config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
