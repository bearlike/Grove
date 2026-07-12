"""Native steering delivery (#172): render_answer + the ChannelSteerClient seam.

The paneless twin of tmux keystroke steering — an answer/message is rendered to
text and delivered over the #182 channel receiver. Best-effort by contract: no
endpoint (channels off / server down) is a logged no-op, never a raise.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from grove.core import channel, native
from grove.core.agents.model import AgentQuestion, AgentQuestionOption, AnswerSelection

# ─── render_answer: questions + selections → deliverable text ────────────────


def _question(prompt: str, *labels: str) -> AgentQuestion:
    return AgentQuestion(
        id="q#0",
        group_id="g",
        kind="single_select",
        prompt=prompt,
        options=tuple(AgentQuestionOption(label=x) for x in labels),
    )


def test_render_answer_maps_indexes_to_option_labels() -> None:
    q = _question("Which color?", "Blue", "Green")
    text = native.render_answer((q,), [AnswerSelection(indexes=(1,))])
    assert text == "Which color? Green"


def test_render_answer_passes_free_text_through() -> None:
    q = _question("Name?")
    text = native.render_answer((q,), [AnswerSelection(text="Ada")])
    assert text == "Name? Ada"


def test_render_answer_skips_out_of_range_index_without_raising() -> None:
    q = _question("Pick", "A")
    # index 5 doesn't exist — dropped, not raised; the prompt still rides.
    assert native.render_answer((q,), [AnswerSelection(indexes=(5,))]) == "Pick"


def test_render_answer_empty_degrades_to_acknowledgement() -> None:
    assert native.render_answer((), []) == "(answer submitted)"


# ─── ChannelSteerClient: best-effort delivery over the channel receiver ──────


def test_send_message_no_endpoint_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No published endpoint file (channels off / server down) → a logged no-op,
    never a raise, and no HTTP attempt."""
    monkeypatch.setattr(channel, "channel_endpoint_path", lambda: tmp_path / "absent.json")

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("no POST should be attempted without an endpoint")

    monkeypatch.setattr(httpx, "post", _boom)
    native.ChannelSteerClient().send_message("sess-1", "hi")  # must not raise


def test_send_message_posts_to_receiver_with_bearer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    endpoint = tmp_path / "channel-endpoint.json"
    endpoint.write_text(json.dumps({"port": 54321, "token": "tok"}), encoding="utf-8")
    monkeypatch.setattr(channel, "channel_endpoint_path", lambda: endpoint)
    captured: dict[str, object] = {}

    def _fake_post(url: str, **kwargs: object) -> object:
        captured.update({"url": url, **kwargs})

        class _Resp:
            status_code = 202

        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)
    native.ChannelSteerClient().send_message("sess-1", "hello")

    assert captured["url"] == f"http://127.0.0.1:54321{channel.RECEIVE_ROUTE}"
    assert captured["headers"] == {"Authorization": "Bearer tok"}
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["content"] == "hello"
    assert body["meta"] == {"session_id": "sess-1"}


def test_send_message_swallows_http_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A transport failure is best-effort: logged and swallowed, never raised into
    the steering caller's path."""
    endpoint = tmp_path / "channel-endpoint.json"
    endpoint.write_text(json.dumps({"port": 1, "token": "t"}), encoding="utf-8")
    monkeypatch.setattr(channel, "channel_endpoint_path", lambda: endpoint)

    def _raise(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)
    native.ChannelSteerClient().send_message("sess-1", "hello")  # must not raise


def test_interrupt_is_best_effort_noop() -> None:
    native.ChannelSteerClient().interrupt("sess-1")  # deferred primitive; must not raise
