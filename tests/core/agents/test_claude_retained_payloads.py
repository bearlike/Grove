"""Keep provider payloads that Grove projects, not a duplicate UI rendering."""

from __future__ import annotations

from copy import deepcopy

from grove.core.agents.claude_code import _Record, _RecordFolder, _TranscriptParser
from grove.core.agents.transcript_cache import _retained_size


def test_provider_rendering_and_nonqueue_attachment_payload_are_not_retained() -> None:
    record = {
        "type": "attachment",
        "uuid": "attachment",
        "timestamp": "2026-09-16T00:00:00Z",
        "rendered": "UI-only representation " * 10_000,
        "attachment": {"type": "file", "body": "UI-only context " * 10_000},
    }
    original = _TranscriptParser([_Record(raw=deepcopy(record), index=0)])
    folder = _RecordFolder()
    folder.add(deepcopy(record), "source")
    compact = _TranscriptParser(folder.records())
    assert compact.messages() == original.messages()
    assert compact.activity() == original.activity()
    assert compact.digest() == original.digest()
    assert _retained_size(folder.records()) < _retained_size(record) // 10
    assert "rendered" not in folder.records()[0].raw
    assert folder.records()[0].raw["attachment"] == {"type": "file"}


def test_queue_prompt_remains_complete_after_provider_ui_fields_are_dropped() -> None:
    prompt = "A queued prompt with no truncation " * 1000
    record = {
        "type": "attachment",
        "uuid": "queued",
        "timestamp": "2026-09-16T00:00:00Z",
        "attachment": {
            "type": "queued_command",
            "prompt": prompt,
            "commandMode": "prompt",
            "origin": {"kind": "human"},
            "unused": "unused " * 1000,
        },
        "rendered": "not the authoritative prompt",
    }
    original = _TranscriptParser([_Record(raw=deepcopy(record), index=0)]).messages()
    folder = _RecordFolder()
    folder.add(deepcopy(record), "source")
    assert folder.messages() == original
    assert folder.records()[0].queued_prompt == prompt
