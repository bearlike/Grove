"""The tiny hook producer keeps ordinary callback processes out of Grove's stack."""

from __future__ import annotations

import io
import json
from pathlib import Path

from grove.hook_producer import HookProducer


def _spool_dir(state_home: Path) -> Path:
    return state_home / "grove" / "agent-sidecars" / "spool"


def test_regular_event_spools_the_verbatim_payload_without_the_legacy_handler(
    monkeypatch, tmp_path: Path
) -> None:
    state_home = tmp_path / "state"
    spool = _spool_dir(state_home)
    spool.mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    payload = b'{"hook_event_name":"Stop","session_id":"s-1"}'
    legacy_calls: list[tuple[tuple[str, ...], bytes]] = []

    result = HookProducer(
        legacy=lambda argv, raw: legacy_calls.append((tuple(argv), raw)) or 0
    ).run(["--daemon-url", "http://127.0.0.1:7421"], stdin=io.BytesIO(payload))

    assert result == 0
    assert legacy_calls == []
    entries = list(spool.glob("*.json"))
    assert len(entries) == 1
    assert json.loads(entries[0].read_text(encoding="utf-8")) == {
        "grove_hook_envelope": 1,
        "payload": json.loads(payload),
        "tmux_pane": None,
    }
    assert list(spool.glob("*.tmp")) == []


def test_regular_event_carries_the_pane_for_adoption_evidence(monkeypatch, tmp_path: Path) -> None:
    state_home = tmp_path / "state"
    spool = _spool_dir(state_home)
    spool.mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("TMUX_PANE", "%42")

    assert (
        HookProducer().run([], stdin=io.BytesIO(b'{"hook_event_name":"Stop","session_id":"s-1"}'))
        == 0
    )

    entry = next(spool.glob("*.json"))
    assert json.loads(entry.read_text(encoding="utf-8"))["tmux_pane"] == "%42"


def test_statusline_uses_the_existing_suffix_that_selects_its_drain_arm(
    monkeypatch, tmp_path: Path
) -> None:
    state_home = tmp_path / "state"
    spool = _spool_dir(state_home)
    spool.mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    payload = json.dumps({"session_id": "s-1", "context_window": {}}).encode()

    assert HookProducer().run(["--statusline"], stdin=io.BytesIO(payload)) == 0

    entries = list(spool.glob("*.statusline.json"))
    assert len(entries) == 1
    assert json.loads(entries[0].read_text(encoding="utf-8")) == {
        "grove_hook_envelope": 1,
        "payload": json.loads(payload),
        "tmux_pane": None,
    }


def test_user_prompt_stays_synchronous_with_the_legacy_stdout_handler(
    monkeypatch, tmp_path: Path
) -> None:
    state_home = tmp_path / "state"
    spool = _spool_dir(state_home)
    spool.mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    payload = b'{"hook_event_name":"UserPromptSubmit","session_id":"s-1"}'
    calls: list[tuple[tuple[str, ...], bytes]] = []

    assert (
        HookProducer(legacy=lambda argv, raw: calls.append((tuple(argv), raw)) or 0).run(
            ["--daemon-url", "http://127.0.0.1:7421"], stdin=io.BytesIO(payload)
        )
        == 0
    )

    assert calls == [(("--daemon-url", "http://127.0.0.1:7421"), payload)]
    assert list(spool.glob("*.json")) == []


def test_an_unknown_mode_delegates_to_the_legacy_handler() -> None:
    payload = b'{"hook_event_name":"Stop","session_id":"s-1"}'
    calls: list[tuple[tuple[str, ...], bytes]] = []

    assert (
        HookProducer(legacy=lambda argv, raw: calls.append((tuple(argv), raw)) or 0).run(
            ["--future-mode"], stdin=io.BytesIO(payload)
        )
        == 0
    )

    assert calls == [(("--future-mode",), payload)]


def test_bounded_input_is_dropped_without_creating_a_partial_spool_entry(
    monkeypatch, tmp_path: Path
) -> None:
    state_home = tmp_path / "state"
    spool = _spool_dir(state_home)
    spool.mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    assert HookProducer().run(stdin=io.BytesIO(b"{" + b"x" * HookProducer.MAX_STDIN_BYTES)) == 0

    assert list(spool.iterdir()) == []
