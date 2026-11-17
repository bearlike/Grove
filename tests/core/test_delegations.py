"""The batch journal: durable requests and per-recipient receipts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.contracts.mailboxes import MailboxAddress, MailboxReceipt
from grove.core.delegations import DelegationJournal

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SENDER, OTHER = "f" * 32, "e" * 32
A, B = "a" * 32, "b" * 32


def _record(
    journal: DelegationJournal,
    request_id: str,
    *,
    sender: str = SENDER,
    recipients: tuple[str, ...] = (A, B),
    at: datetime = NOW,
    kind: str = "batch",
) -> None:
    journal.record_request(
        request_id=request_id,
        kind=kind,  # type: ignore[arg-type]
        sender=MailboxAddress(workspace_id=sender),
        digest="d-" + request_id,
        subject="Rebase",
        body="Rebase on main.",
        recipients=[MailboxAddress(workspace_id=r) for r in recipients],
        created_at=at,
    )


def _receipt(recipient: str, stage: str = "delivered", **extra: object) -> MailboxReceipt:
    return MailboxReceipt(
        recipient=MailboxAddress(workspace_id=recipient),
        stage=stage,  # type: ignore[arg-type]
        created_at=NOW,
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
def journal(tmp_path: Path) -> DelegationJournal:
    return DelegationJournal(tmp_path / "delegations.sqlite3")


def test_a_request_reads_back_with_no_receipts_until_they_are_appended(
    journal: DelegationJournal,
) -> None:
    _record(journal, "req-1")
    recorded = journal.lookup("req-1")
    assert recorded is not None and recorded.receipts == ()
    assert [r.workspace_id for r in recorded.recipients] == [A, B]

    journal.record_receipt("req-1", _receipt(B, "rejected", reason="not_live"))
    journal.record_receipt("req-1", _receipt(A, message_id="mbx_" + "1" * 32))
    recorded = journal.lookup("req-1")
    assert recorded is not None
    # Request order, not the order the sends returned in.
    assert [(r.recipient.workspace_id, r.stage) for r in recorded.receipts] == [
        (A, "delivered"),
        (B, "rejected"),
    ]
    assert recorded.receipts[0].message_id == "mbx_" + "1" * 32
    assert recorded.receipts[0].reason is None and recorded.receipts[1].reason == "not_live"


def test_a_request_id_is_recorded_once_and_its_attribution_never_changes(
    journal: DelegationJournal,
) -> None:
    _record(journal, "req-1", sender=SENDER)
    with pytest.raises(sqlite3.IntegrityError):
        _record(journal, "req-1", sender=OTHER)
    recorded = journal.lookup("req-1")
    assert recorded is not None and recorded.sender.workspace_id == SENDER


def test_the_first_receipt_for_a_primary_agent_recipient_stands(
    journal: DelegationJournal,
) -> None:
    """``agent`` is '' for a primary agent; a NULL there would let a second row in."""
    _record(journal, "req-1", recipients=(A,))
    journal.record_receipt("req-1", _receipt(A, "delivered"))
    journal.record_receipt("req-1", _receipt(A, "unknown", reason="transport_failed"))

    recorded = journal.lookup("req-1")
    assert recorded is not None
    assert [r.stage for r in recorded.receipts] == ["delivered"]
    count = sqlite3.connect(journal.path).execute("SELECT COUNT(*) FROM delegation_receipts")
    assert count.fetchone()[0] == 1


def test_rows_survive_a_new_journal_on_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "delegations.sqlite3"
    first = DelegationJournal(path)
    _record(first, "req-1", kind="voice")
    first.record_receipt("req-1", _receipt(A))
    first.close()

    recorded = DelegationJournal(path).lookup("req-1")
    assert recorded is not None and recorded.kind == "voice"
    assert recorded.created_at == NOW
    assert [r.recipient.workspace_id for r in recorded.receipts if r.recipient] == [A]


def test_the_payload_digest_is_canonical() -> None:
    assert DelegationJournal.digest({"a": 1, "b": [2]}) == DelegationJournal.digest(
        {"b": [2], "a": 1}
    )
    assert DelegationJournal.digest({"a": 1}) != DelegationJournal.digest({"a": 2})
