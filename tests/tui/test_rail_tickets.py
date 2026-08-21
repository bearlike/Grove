"""The rail's tickets panel: the link, the title, and the per-ticket phase.

`_render_tickets_panel` is a pure renderer, so its whole contract is the `Text`
it returns — including the SPANS, which is where the hyperlink lives. Asserting
on `.plain` alone cannot see a link at all, which is exactly the property that
makes the link affordable: it costs no rendered width.
"""

from __future__ import annotations

import pytest
from rich.style import Style
from rich.text import Text

from grove.core.contracts.tickets import TicketRef
from grove.core.phase import TicketClaim
from grove.tui.widgets.peek_rail import _render_tickets_panel


def _links(text: Text) -> list[str]:
    """Every OSC 8 target carried by this render, in span order.

    Spans appended with a style STRING keep it unparsed, so the link only
    appears after `Style.parse` — reading `.link` off the raw span silently
    reports no links at all, which looks exactly like the feature being absent.
    """
    links: list[str] = []
    for span in text.spans:
        style = Style.parse(span.style) if isinstance(span.style, str) else span.style
        if style.link:
            links.append(style.link)
    return links


def test_the_pill_carries_the_url_as_a_link_and_never_prints_it() -> None:
    """The original rule dropped the url because it 'isn't clickable in a
    terminal'. It is — and carrying it costs zero columns, which is what makes
    it affordable on a line that already crops."""
    ref = TicketRef(provider="gitea", id="42", url="https://example.test/issues/42")

    rendered = _render_tickets_panel([ref], dark=True)

    assert _links(rendered) == ["https://example.test/issues/42"]
    assert "https://" not in rendered.plain


def test_a_ref_without_a_url_carries_no_link() -> None:
    rendered = _render_tickets_panel([TicketRef(provider="gitea", id="42")], dark=True)
    assert _links(rendered) == []


def test_the_title_renders_beside_the_pill() -> None:
    ref = TicketRef(provider="gitea", id="42", title="Fix the login redirect")
    assert "Fix the login redirect" in _render_tickets_panel([ref], dark=True).plain


def test_a_per_ticket_phase_renders_for_the_ticket_that_claimed_it() -> None:
    """One workspace, two tickets, two different phases — the whole reason the
    axis is per ticket rather than per workspace."""
    refs = [
        TicketRef(provider="gitea", id="1"),
        TicketRef(provider="gitea", id="2", kind="pull_request"),
    ]
    claims = {
        "gitea:1": TicketClaim(ticket="gitea:1", phase="delivering"),
        "gitea:2": TicketClaim(ticket="gitea:2", phase="implementing", blocked=True),
    }

    lines = _render_tickets_panel(refs, dark=True, claims=claims).plain.splitlines()

    assert "implementing" in lines[0]
    assert "‼" in lines[0]  # blocked is a trailing mark, never a seventh phase
    assert "delivering" in lines[1]
    assert "‼" not in lines[1]


def test_tickets_follow_the_shared_pr_state_and_id_ordering() -> None:
    """A PR leads its linked issues while closed work sinks within its kind."""
    refs = [
        TicketRef(provider="gitea", id="9", status="closed"),
        TicketRef(provider="gitea", id="12", status="open", kind="pull_request"),
        TicketRef(provider="gitea", id="3", status="open"),
    ]

    lines = _render_tickets_panel(refs, dark=True).plain.splitlines()

    assert lines[0].startswith("⇒ GTEA#12")
    assert lines[1].startswith("GTEA#3")
    assert lines[2].startswith("GTEA#9")


def test_a_ticket_note_renders_on_its_claimed_row() -> None:
    ref = TicketRef(provider="gitea", id="1")
    claim = TicketClaim(ticket="gitea:1", phase="verifying", note="Waiting for approval")

    rendered = _render_tickets_panel([ref], dark=True, claims={ref.key: claim})

    assert "Waiting for approval" in rendered.plain


def test_a_claim_without_a_note_keeps_its_pre_note_bytes() -> None:
    ref = TicketRef(provider="gitea", id="1")
    claim = TicketClaim(ticket="gitea:1", phase="verifying")

    assert (
        _render_tickets_panel([ref], dark=True, claims={ref.key: claim}).plain
        == "GTEA#1  ▆ verifying"
    )


def test_a_ticket_note_is_trimmed_to_its_row_budget() -> None:
    ref = TicketRef(provider="gitea", id="1")
    claim = TicketClaim(ticket="gitea:1", phase="verifying", note="n" * 200)

    rendered = _render_tickets_panel([ref], dark=True, claims={ref.key: claim})

    assert "n" * 79 + "…" in rendered.plain
    assert "n" * 80 not in rendered.plain


def test_a_ticket_with_no_claim_renders_no_phase_at_all() -> None:
    """Absence of a report is not step zero — inventing `scoping` would claim
    progress on work nobody said anything about."""
    refs = [TicketRef(provider="gitea", id="1"), TicketRef(provider="gitea", id="2")]
    claims = {"gitea:1": TicketClaim(ticket="gitea:1", phase="verifying")}

    lines = _render_tickets_panel(refs, dark=True, claims=claims).plain.splitlines()

    assert "verifying" in lines[0]
    # `ticket_pill` abbreviates the provider; the point is that the line
    # carries the ref and nothing else.
    assert lines[1].strip() == "GTEA#2"


@pytest.mark.parametrize("claims", [None, {}])
def test_without_claims_the_render_is_byte_identical_to_the_pre_phase_one(
    claims: dict[str, TicketClaim] | None,
) -> None:
    """The byte-identical-absence rule this surface already follows: a workspace
    whose agent never reported must render exactly as it did before per-ticket
    phase existed."""
    refs = [TicketRef(provider="gitea", id="1", title="A thing", status="open")]

    assert (
        _render_tickets_panel(refs, dark=True, claims=claims).plain
        == _render_tickets_panel(refs, dark=True).plain
    )


def test_an_empty_ref_list_still_renders_nothing() -> None:
    assert _render_tickets_panel([], dark=True, claims={}).plain == ""
