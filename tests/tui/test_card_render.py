"""Pure-function tests for `_render_card`.

The card body is the public visual contract for the workspace list.
Pinning the rendered plain-text protects against accidental drift in
status glyphs, label normalization, and the init-failed badge — all of
which are reachable without a Pilot, which keeps these tests fast and
free of Textual app construction.

Focus chrome (the clay-accent border on highlighted cards) is purely
TCSS, so it lives in the live-wiring tests under ``test_list_screen.py``
rather than here. This module pins what the *content* of a card looks
like; it never asserts on focus state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from grove.core import InitStatus, WorkspaceState, WorkspaceStatus
from grove.core.agents import AgentActivityState
from grove.core.contracts.runtime_palette import RUNTIME_GLYPH
from grove.core.contracts.tickets import TicketRef
from grove.core.phase import PHASE_ORDER, PhaseReport
from grove.core.workspace import Runtime
from grove.tui._status import (
    ACTIVE_PULSE_FRAMES,
    AGENT_STATE_GLYPH,
    BLOCKED_GLYPH,
    PHASE_GLYPH,
    PR_GLYPH,
    STATUS_GLYPH,
    active_pulse,
    agent_state_color,
    agent_state_glyph,
    agent_state_label,
    blocked_color,
    chrome_color,
    phase_color,
    phase_glyph,
    phase_label,
    pr_status_color,
    ref_color,
    runtime_color,
    runtime_glyph,
    status_color,
)
from grove.tui.widgets import status as status_widget
from grove.tui.widgets.card import _render_card, format_wait, ticket_pill

_NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _state(**overrides: object) -> WorkspaceState:
    base = {
        "id": "wid-1",
        "title": "fix-modal-focus",
        "repo_root": "/tmp/repo",
        "branch": "test/fix-modal-focus-20260506",
        "base_branch": "main",
        "worktree_path": "/tmp/wt",
        "tmux_session": "test-fix-modal-focus",
        "agent_name": "claude",
        "status": WorkspaceStatus.ACTIVE,
        "created_at": _NOW - timedelta(minutes=10),
        "updated_at": _NOW - timedelta(minutes=4),
    }
    base.update(overrides)
    return WorkspaceState(**base)  # type: ignore[arg-type]


# ─── plain-text content ─────────────────────────────────────────────────────


def test_render_card_includes_title_branch_agent_and_status_label() -> None:
    body = _render_card(_state(), dark=True, now=_NOW).plain
    assert "fix-modal-focus" in body
    assert "test/fix-modal-focus-20260506" in body
    assert "claude" in body
    assert "active" in body  # status_label normalizes RUNNING/ACTIVE to "active"


def test_render_card_age_is_humanized_against_now() -> None:
    body = _render_card(_state(), dark=True, now=_NOW).plain
    # 4 minutes ago — humanize phrasing varies by version, but "minute"
    # appears in every supported one.
    assert "minute" in body or "min" in body


def test_render_card_paused_uses_paused_glyph_and_label() -> None:
    body = _render_card(
        _state(status=WorkspaceStatus.PAUSED, paused_at=_NOW - timedelta(hours=2)),
        dark=True,
        now=_NOW,
    ).plain
    assert "‖" in body  # paused glyph
    assert "paused" in body


def test_render_card_idle_uses_idle_glyph() -> None:
    body = _render_card(_state(status=WorkspaceStatus.IDLE), dark=True, now=_NOW).plain
    assert "◐" in body
    assert "idle" in body


def test_render_card_error_uses_error_glyph_and_label() -> None:
    body = _render_card(
        _state(status=WorkspaceStatus.ERROR, error_detail="boom"),
        dark=True,
        now=_NOW,
    ).plain
    assert "✗" in body
    assert "error" in body


def test_render_card_init_failed_appends_badge() -> None:
    body = _render_card(
        _state(init_status=InitStatus.FAILED, init_log_path="/tmp/init.log"),
        dark=True,
        now=_NOW,
    ).plain
    # Badge sits on line 2 next to the status label so the user sees a
    # broken init at a glance even if the workspace itself is "active".
    assert "init failed" in body


def test_render_card_init_ok_does_not_show_init_badge() -> None:
    body = _render_card(_state(init_status=InitStatus.OK), dark=True, now=_NOW).plain
    assert "init failed" not in body


def test_render_card_long_title_is_trimmed_with_ellipsis() -> None:
    long = "a" * 80
    body = _render_card(_state(title=long), dark=True, now=_NOW).plain
    assert long not in body  # full string must not survive
    assert "…" in body


# ─── focus chrome (TCSS-only) ───────────────────────────────────────────────


def test_render_card_body_carries_no_focus_indicator_glyph() -> None:
    """Focus lives entirely in TCSS — the body never carries an inline ``▌``
    bar regardless of ``state.status``. A reintroduced inline indicator would
    mean two sources of truth (CSS + render); pin against it.
    """
    for status in (
        WorkspaceStatus.ACTIVE,
        WorkspaceStatus.IDLE,
        WorkspaceStatus.PAUSED,
        WorkspaceStatus.OFFLINE,
        WorkspaceStatus.ERROR,
    ):
        body = _render_card(_state(status=status), dark=True, now=_NOW).plain
        assert "▌" not in body, (
            f"render must never emit '▌' (it's TCSS chrome now); body for {status}: {body!r}"
        )


def test_render_card_first_visible_char_is_status_glyph() -> None:
    """Without the leading focus-bar gutter, the very first visible char
    of the card body is the status glyph itself. Confirms the layout
    starts cleanly under whatever indent the parent list applies."""
    body = _render_card(_state(), dark=True, now=_NOW).plain
    assert body[:1] == "●"  # ACTIVE glyph from STATUS_GLYPH


# ─── theme polarity ──────────────────────────────────────────────────────────


def test_render_card_branch_uses_ref_branch_color_in_dark() -> None:
    text = _render_card(_state(), dark=True, now=_NOW)
    branch_hex = ref_color("branch", dark=True)
    seen = {str(st) for _, _, st in text.spans}
    assert any(branch_hex.lower() in s.lower() for s in seen)


def test_render_card_branch_uses_ref_branch_color_in_light() -> None:
    text = _render_card(_state(), dark=False, now=_NOW)
    branch_hex = ref_color("branch", dark=False)
    seen = {str(st) for _, _, st in text.spans}
    assert any(branch_hex.lower() in s.lower() for s in seen)


def test_render_card_status_color_tracks_dark_flag() -> None:
    dark_text = _render_card(_state(), dark=True, now=_NOW)
    light_text = _render_card(_state(), dark=False, now=_NOW)
    dark_hex = status_color(WorkspaceStatus.ACTIVE, dark=True)
    light_hex = status_color(WorkspaceStatus.ACTIVE, dark=False)
    assert dark_hex != light_hex  # guard for a future palette collapse
    assert any(dark_hex.lower() in str(st).lower() for _, _, st in dark_text.spans)
    assert any(light_hex.lower() in str(st).lower() for _, _, st in light_text.spans)


# ─── typographic hierarchy ──────────────────────────────────────────────────


def test_render_card_branch_is_bold() -> None:
    """Branch carries the strongest accent on line 2 — bold + ref color —
    so a user scanning a list of workspaces lands on it first."""
    text = _render_card(_state(), dark=True, now=_NOW)
    branch_hex = ref_color("branch", dark=True)
    found = False
    for start, end, style in text.spans:
        if "test/fix-modal-focus" in text.plain[start:end]:
            style_str = str(style).lower()
            if "bold" in style_str and branch_hex.lower() in style_str:
                found = True
                break
    assert found, (
        f"branch span should be 'bold {branch_hex}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_status_label_is_bold_and_colored() -> None:
    """Status label on line 2 is bold + status color — same weight as the
    branch so 'what state is this in' reads at the same tier as 'which branch'."""
    text = _render_card(_state(), dark=True, now=_NOW)
    s_hex = status_color(WorkspaceStatus.ACTIVE, dark=True)
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == "active":
            style_str = str(style).lower()
            if "bold" in style_str and s_hex.lower() in style_str:
                found = True
                break
    assert found, (
        f"'active' span should be 'bold {s_hex}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_separator_uses_muted_hex_not_terminal_dim() -> None:
    """Separators between sections use the explicit muted hex from
    `chrome_color('muted')` rather than relying on terminal `dim` —
    this gives stable, theme-tracked color in screenshots and across
    terminals that interpret `dim` differently."""
    text = _render_card(_state(), dark=True, now=_NOW)
    muted = chrome_color("muted", dark=True)
    seen = {str(st).lower() for _, _, st in text.spans}
    assert any(muted.lower() in s for s in seen), (
        f"expected muted hex {muted} on at least one span; seen: {seen}"
    )
    # Belt-and-braces: confirm the separator characters made it into the body.
    assert "·" in text.plain


def test_render_card_title_is_bold_and_underlined() -> None:
    """Title carries `bold underline` so the row's identity reads as a
    heading (same affordance as a hyperlink in IDE file lists). Other
    line-1 tokens (glyph, age) MUST NOT carry underline — the cue is
    reserved for the title."""
    text = _render_card(_state(), dark=True, now=_NOW)
    found_title = False
    for start, end, style in text.spans:
        slice_text = text.plain[start:end]
        style_str = str(style).lower()
        if slice_text == "fix-modal-focus":
            assert "bold" in style_str, f"title span should be bold; got {style_str}"
            assert "underline" in style_str, f"title span should be underlined; got {style_str}"
            found_title = True
        # Belt-and-braces: underline is only on the title.
        elif "underline" in style_str:
            raise AssertionError(
                f"non-title span {slice_text!r} should not carry underline; got {style_str}"
            )
    assert found_title, (
        f"expected a 'fix-modal-focus' span; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_agent_uses_info_cyan() -> None:
    """Agent name takes a different ref color than branch (info cyan vs
    branch teal) so the eye separates 'who is running' from 'what branch'
    without re-reading the labels. Both are bold so they read as peers."""
    text = _render_card(_state(), dark=True, now=_NOW)
    info_hex = ref_color("info", dark=True).lower()
    branch_hex = ref_color("branch", dark=True).lower()
    assert info_hex != branch_hex  # palette must keep the two colors distinct
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == "claude":
            style_str = str(style).lower()
            if "bold" in style_str and info_hex in style_str:
                found = True
                break
    assert found, (
        f"agent span 'claude' should be 'bold {info_hex}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


# ─── live-signal pulse (ACTIVE only) ────────────────────────────────────────


def test_active_pulse_frames_have_distinct_glyphs() -> None:
    """The two pulse frames must render different glyphs in plain text — that
    is what carries the visible 'beat' even on a B&W terminal. If a future
    palette change accidentally collapses them onto the same glyph, the
    animation degrades into a color-only flicker."""
    glyphs = {active_pulse(f, dark=True)[0] for f in range(ACTIVE_PULSE_FRAMES)}
    assert len(glyphs) == ACTIVE_PULSE_FRAMES
    # Frame 0 is the canonical filled disc, the static lookup's own glyph.
    assert active_pulse(0, dark=True)[0] == "●"


def test_active_pulse_tint_distinct_from_base_in_both_modes() -> None:
    """The swelled-frame hex must not collapse onto the resting-frame hex
    in either polarity — otherwise the animation reads as a glyph-only swap
    with no color motion, which contradicts the 'color shift in lockstep'
    contract the design system documents."""
    for dark in (True, False):
        rest_hex = active_pulse(0, dark=dark)[1]
        swell_hex = active_pulse(1, dark=dark)[1]
        assert rest_hex != swell_hex, f"pulse frames share hex (dark={dark}): {rest_hex}"


def test_render_card_active_swells_glyph_and_color_with_pulse_frame() -> None:
    """ACTIVE rows take the pulse: frame 0 → resting glyph + base hex, frame 1
    → swelled glyph + tint hex. Both glyph and the line-2 status label color
    move in lockstep (design-system rule: 'glyph and label share the status
    color'). Pinned by inspecting the rendered Rich Text spans."""
    rest_glyph, rest_hex = active_pulse(0, dark=True)
    swell_glyph, swell_hex = active_pulse(1, dark=True)

    rest = _render_card(_state(), dark=True, now=_NOW, pulse_frame=0)
    swell = _render_card(_state(), dark=True, now=_NOW, pulse_frame=1)

    # Plain text differs by the leading glyph alone.
    assert rest.plain[:1] == rest_glyph
    assert swell.plain[:1] == swell_glyph
    assert rest.plain != swell.plain

    # Status-label color moves with the glyph color.
    rest_label_styles = [str(st) for s, e, st in rest.spans if rest.plain[s:e] == "active"]
    swell_label_styles = [str(st) for s, e, st in swell.spans if swell.plain[s:e] == "active"]
    assert any(rest_hex.lower() in s.lower() for s in rest_label_styles)
    assert any(swell_hex.lower() in s.lower() for s in swell_label_styles)


def test_render_card_non_active_ignores_pulse_frame() -> None:
    """Non-ACTIVE statuses must render identical bytes regardless of the
    pulse frame — pulsing IDLE/PAUSED/etc. would contradict their
    'quiet / deliberate / dormant' semantics."""
    for status in (
        WorkspaceStatus.IDLE,
        WorkspaceStatus.PAUSED,
        WorkspaceStatus.OFFLINE,
        WorkspaceStatus.ERROR,
    ):
        body_0 = _render_card(_state(status=status), dark=True, now=_NOW, pulse_frame=0).plain
        body_1 = _render_card(_state(status=status), dark=True, now=_NOW, pulse_frame=1).plain
        assert body_0 == body_1, f"{status} body must not depend on pulse_frame"


# ─── root placement indicator ───────────────────────────────────────────────


def test_render_card_root_placement_shows_root_tag() -> None:
    """A root workspace (placement ROOT) renders a muted `root` tag on line 2
    so the user can tell it apart from a worktree workspace at a glance."""
    from grove.core.workspace import Placement  # noqa: PLC0415

    body = _render_card(_state(placement=Placement.ROOT), dark=True, now=_NOW).plain
    assert "root" in body


def test_render_card_worktree_placement_has_no_root_tag() -> None:
    """A normal worktree workspace renders no `root` tag — the absence is the
    default, so the indicator only appears for the root case."""
    from grove.core.workspace import Placement  # noqa: PLC0415

    # Default placement is WORKTREE; branch text deliberately avoids "root".
    body = _render_card(
        _state(placement=Placement.WORKTREE, branch="feat/x", title="task"),
        dark=True,
        now=_NOW,
    ).plain
    assert "root" not in body


def test_render_card_root_tag_uses_muted_hex() -> None:
    """The root tag rides the same muted chrome hex as the card's separators —
    a quiet qualifier, not a loud status token (never an inline literal hex)."""
    from grove.core.workspace import Placement  # noqa: PLC0415

    text = _render_card(_state(placement=Placement.ROOT), dark=True, now=_NOW)
    muted = chrome_color("muted", dark=True).lower()
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == "root" and muted in str(style).lower():
            found = True
            break
    assert found, (
        f"'root' tag span should carry muted hex {muted}; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_agent_state_working_shows_glyph_and_label() -> None:
    """An agent state pushed by the screen's activity tick renders as
    `<glyph> <label>` on line 2, after the agent-name segment — bold +
    agent-state color, the same typography tier as the status label."""
    state = AgentActivityState.WORKING
    text = _render_card(_state(), dark=True, now=_NOW, agent_state=state)
    segment = f"{agent_state_glyph(state)} {agent_state_label(state)}"
    assert segment in text.plain  # "▶ working"
    # The segment sits on line 2, between the agent name and the status label.
    line2 = text.plain.splitlines()[1]
    assert line2.index("claude") < line2.index(segment) < line2.rindex("active")
    state_hex = agent_state_color(state, dark=True).lower()
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == segment:
            style_str = str(style).lower()
            if "bold" in style_str and state_hex in style_str:
                found = True
                break
    assert found, (
        f"agent-state span should be 'bold {state_hex}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_agent_state_none_is_byte_identical_to_legacy_render() -> None:
    """``agent_state=None`` (and the omitted-param default) must render the
    exact same bytes AND spans as a card built with no agent-state param at
    all — absence is the default, same convention as the `root` tag."""
    for status in (
        WorkspaceStatus.ACTIVE,
        WorkspaceStatus.IDLE,
        WorkspaceStatus.PAUSED,
        WorkspaceStatus.OFFLINE,
        WorkspaceStatus.ERROR,
    ):
        legacy = _render_card(_state(status=status), dark=True, now=_NOW)
        explicit = _render_card(_state(status=status), dark=True, now=_NOW, agent_state=None)
        assert legacy.plain == explicit.plain
        assert legacy.spans == explicit.spans
        # And no agent-state token leaks into the agent-less render.
        for label in ("working", "waiting", "blocked", "starting"):
            assert label not in legacy.plain


# ─── ticket pills ────────────────────────────────────────────────────────────


def test_ticket_pill_formats_per_provider() -> None:
    """The shared formatter renders the compact per-provider convention:
    Linear verbatim, GitHub / Gitea prefixed, ambiguous gets a trailing `?`."""
    assert ticket_pill(TicketRef(provider="linear", id="ENG-123")) == "ENG-123"
    assert ticket_pill(TicketRef(provider="github", id="42")) == "GH#42"
    assert ticket_pill(TicketRef(provider="gitea", id="5")) == "GTEA#5"
    assert ticket_pill(TicketRef(provider="github", id="42", ambiguous=True)) == "GH#42?"


def test_render_card_renders_ticket_pills() -> None:
    """Each ticket ref shows its compact pill on line 2 next to the status
    label — Linear verbatim, GitHub / Gitea prefixed, ambiguous suffixed."""
    body = _render_card(
        _state(
            ticket_refs=[
                TicketRef(provider="linear", id="ENG-123"),
                TicketRef(provider="github", id="42"),
                TicketRef(provider="gitea", id="5"),
                TicketRef(provider="github", id="42", ambiguous=True),
            ]
        ),
        dark=True,
        now=_NOW,
    ).plain
    assert "ENG-123" in body
    assert "GH#42" in body
    assert "GTEA#5" in body
    assert "GH#42?" in body


def test_render_card_ticket_pill_uses_info_hex() -> None:
    """Ticket pills ride the agent-info cyan (the auxiliary-metadata slot),
    never an inline literal hex — same accessor every other accent uses."""
    text = _render_card(
        _state(ticket_refs=[TicketRef(provider="linear", id="ENG-7")]),
        dark=True,
        now=_NOW,
    )
    info_hex = ref_color("info", dark=True).lower()
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == "ENG-7":
            style_str = str(style).lower()
            if "bold" in style_str and info_hex in style_str:
                found = True
                break
    assert found, (
        f"ticket pill span 'ENG-7' should be 'bold {info_hex}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_empty_ticket_refs_renders_no_pill() -> None:
    """No tickets → no pill text and no stray trailing separator beyond
    line-2's other segments. Pinned against a placeholder creeping in."""
    legacy = _render_card(_state(), dark=True, now=_NOW)
    explicit = _render_card(_state(ticket_refs=[]), dark=True, now=_NOW)
    # Empty refs must be byte-identical (and span-identical) to the default.
    assert legacy.plain == explicit.plain
    assert legacy.spans == explicit.spans
    for token in ("ENG-", "GH#", "GTEA#"):
        assert token not in explicit.plain


def test_render_card_init_failed_badge_styled_bold_error() -> None:
    """Init-failed badge sits in bold + init-failure red so a broken init
    reads as the most urgent thing on the row."""
    text = _render_card(
        _state(init_status=InitStatus.FAILED, init_log_path="/tmp/init.log"),
        dark=True,
        now=_NOW,
    )
    fail_hex = "#e64c4c".lower()  # _DARK_STATUS_ERROR via init_status_color(FAILED)
    found = False
    for start, end, style in text.spans:
        if "init failed" in text.plain[start:end]:
            style_str = str(style).lower()
            if "bold" in style_str and fail_hex in style_str:
                found = True
                break
    assert found, (
        f"'init failed' span should be 'bold {fail_hex}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


# ─── task-phase segment (third axis: how far through the task, not what the
# agent is doing right now) ─────────────────────────────────────────────────


def _phase(phase: str = "verifying", **overrides: object) -> PhaseReport:
    base: dict[str, object] = {"phase": phase, "note": None, "updated_at": _NOW}
    base.update(overrides)
    return PhaseReport(**base)  # type: ignore[arg-type]


def test_render_card_phase_shows_glyph_label_and_progress() -> None:
    """A reported phase renders `<glyph> <label> N/M` on line 2, between the
    agent-activity segment and the status label."""
    report = _phase("verifying")
    text = _render_card(
        _state(), dark=True, now=_NOW, agent_state=AgentActivityState.WORKING, phase=report
    )
    segment = f"{phase_glyph('verifying')} {phase_label('verifying')} 4/{len(PHASE_ORDER)}"
    assert segment in text.plain
    line2 = text.plain.splitlines()[1]
    working = AgentActivityState.WORKING
    agent_segment = f"{agent_state_glyph(working)} {agent_state_label(working)}"
    assert line2.index(agent_segment) < line2.index(segment) < line2.rindex("active")


def test_render_card_phase_span_is_bold_and_phase_colored() -> None:
    report = _phase("scoping")
    text = _render_card(_state(), dark=True, now=_NOW, phase=report)
    segment = f"{phase_glyph('scoping')} {phase_label('scoping')} 1/{len(PHASE_ORDER)}"
    hex_ = phase_color("scoping", dark=True).lower()
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == segment:
            style_str = str(style).lower()
            if "bold" in style_str and hex_ in style_str:
                found = True
                break
    assert found, (
        f"phase span should be 'bold {hex_}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_blocked_phase_appends_the_flag_beside_the_phase() -> None:
    """`blocked=True` is a FLAG riding beside the phase segment, not a
    replacement — the phase glyph/label/progress must still render untouched,
    with `BLOCKED_GLYPH` appended after it in `blocked_color`."""
    report = _phase("implementing", blocked=True)
    text = _render_card(_state(), dark=True, now=_NOW, phase=report)
    unblocked_segment = (
        f"{phase_glyph('implementing')} {phase_label('implementing')} 3/{len(PHASE_ORDER)}"
    )
    assert unblocked_segment in text.plain
    assert f"{unblocked_segment} {BLOCKED_GLYPH}" in text.plain
    hex_ = blocked_color(dark=True).lower()
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == f" {BLOCKED_GLYPH}":
            style_str = str(style).lower()
            if "bold" in style_str and hex_ in style_str:
                found = True
                break
    assert found, (
        f"blocked flag span should be 'bold {hex_}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_unblocked_phase_omits_the_flag() -> None:
    """Absence is the default: an unreported-blocked claim renders byte-
    identical to before `blocked` existed, and the glyph never appears."""
    report = _phase("implementing", blocked=False)
    text = _render_card(_state(), dark=True, now=_NOW, phase=report)
    assert BLOCKED_GLYPH not in text.plain


def test_render_card_phase_none_is_byte_identical_to_pre_phase_render() -> None:
    """`phase=None` (and the omitted-param default) must render the exact
    same bytes AND spans as a card built with no phase param at all —
    absence is the default, same convention as agent state and the `root`
    tag."""
    for status in (WorkspaceStatus.ACTIVE, WorkspaceStatus.IDLE, WorkspaceStatus.PAUSED):
        legacy = _render_card(_state(status=status), dark=True, now=_NOW)
        explicit = _render_card(_state(status=status), dark=True, now=_NOW, phase=None)
        assert legacy.plain == explicit.plain
        assert legacy.spans == explicit.spans
        for phase_name in PHASE_ORDER:
            assert phase_name not in legacy.plain


def test_render_card_phase_glyphs_do_not_collide_with_status_or_agent_state() -> None:
    """The phase glyph family must be visually distinguishable from the other
    two axes at a glance — no shared glyphs across the three families."""
    status_glyphs = set(STATUS_GLYPH.values())
    agent_glyphs = set(AGENT_STATE_GLYPH.values())
    phase_glyphs = set(PHASE_GLYPH.values())
    assert phase_glyphs.isdisjoint(status_glyphs)
    assert phase_glyphs.isdisjoint(agent_glyphs)


def test_blocked_glyph_does_not_collide_with_any_other_axis() -> None:
    """`BLOCKED_GLYPH` is a flag riding beside a phase claim, not a member of
    any existing glyph family (status/agent-state circles, the phase block
    ramp, the PR arrow, the runtime squares) — a fifth family, so it can never
    be mistaken for one of them at a glance."""
    other_glyphs = set(STATUS_GLYPH.values()) | set(AGENT_STATE_GLYPH.values())
    other_glyphs |= set(PHASE_GLYPH.values()) | {PR_GLYPH}
    assert BLOCKED_GLYPH not in other_glyphs


# ─── runtime mark (the isolation axis) ───────────────────────────────────────


def test_render_card_runtime_glyphs_do_not_collide_with_any_other_axis_or_chrome() -> None:
    """Runtime is its own glyph family (squares) — disjoint from the status and
    agent-state circles, the phase block ramp, and the PR arrow.

    It also checks the STATUS BAR's chrome glyphs, which the phase test does
    not: the obvious host mark is a house, and `⌂` is already the status bar's
    repo chip. An axis-only disjointness check would have waved that through.
    """
    runtime_glyphs = set(RUNTIME_GLYPH.values())
    assert runtime_glyphs.isdisjoint(set(STATUS_GLYPH.values()))
    assert runtime_glyphs.isdisjoint(set(AGENT_STATE_GLYPH.values()))
    assert runtime_glyphs.isdisjoint(set(PHASE_GLYPH.values()))
    assert PR_GLYPH not in runtime_glyphs
    chrome = {
        status_widget._GLYPH_REPO,
        status_widget._GLYPH_BRANCH,
        status_widget._GLYPH_FILTER,
        status_widget._GLYPH_SELECT,
        status_widget._GLYPH_DIVIDER,
        status_widget._GLYPH_UPDATE,
    }
    assert runtime_glyphs.isdisjoint(chrome)
    # Two runtimes, two distinct marks — neither may borrow the other's.
    assert len(runtime_glyphs) == len(RUNTIME_GLYPH)


def test_blocked_glyph_does_not_collide_with_runtime_squares_or_chrome() -> None:
    """Same extended check `test_render_card_runtime_glyphs_do_not_collide_
    with_any_other_axis_or_chrome` runs for runtime — the status bar's own
    glyphs are not covered by an axis-only disjointness check."""
    assert BLOCKED_GLYPH not in set(RUNTIME_GLYPH.values())
    chrome = {
        status_widget._GLYPH_REPO,
        status_widget._GLYPH_BRANCH,
        status_widget._GLYPH_FILTER,
        status_widget._GLYPH_SELECT,
        status_widget._GLYPH_DIVIDER,
        status_widget._GLYPH_UPDATE,
    }
    assert BLOCKED_GLYPH not in chrome


@pytest.mark.parametrize("runtime", [Runtime.HOST, Runtime.CONTAINER])
def test_render_card_marks_every_runtime_leading_line_two(runtime: Runtime) -> None:
    """BOTH runtimes carry a mark — this axis has no silent state — and the mark
    leads line 2 so a narrow terminal's ellipsis can never crop it away."""
    text = _render_card(_state(runtime=runtime), dark=True, now=_NOW)
    line2 = text.plain.splitlines()[1]
    glyph = runtime_glyph(runtime)
    assert line2.startswith(f"{glyph} ")
    hex_ = runtime_color(runtime, dark=True).lower()
    found = any(
        text.plain[start:end] == f"{glyph} "
        and "bold" in str(style).lower()
        and hex_ in str(style).lower()
        for start, end, style in text.spans
    )
    assert found, (
        f"runtime mark should be 'bold {hex_}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_runtime_mark_is_distinct_from_the_fallback_badge() -> None:
    """A workspace that WANTED a container and fell back to the host keeps its
    isolation contract voided for life — so it must not read as a plain host
    workspace. The host mark stays (it is where the agent really runs) and the
    amber degradation badge rides alongside it, never folded into it."""
    degraded = _render_card(
        _state(runtime=Runtime.HOST, runtime_fallback_reason="docker unavailable"),
        dark=True,
        now=_NOW,
    )
    plain_host = _render_card(_state(runtime=Runtime.HOST), dark=True, now=_NOW)
    assert degraded.plain.splitlines()[1].startswith(f"{runtime_glyph(Runtime.HOST)} ")
    assert "⚠ container fallback" in degraded.plain
    assert "⚠ container fallback" not in plain_host.plain
    assert degraded.plain != plain_host.plain


def test_render_card_phase_index_matches_declared_order() -> None:
    for i, name in enumerate(PHASE_ORDER):
        assert _phase(name).index == i


# ─── pull-request pill ───────────────────────────────────────────────────────


def test_render_card_no_pr_ref_is_byte_identical_to_pre_pr_render() -> None:
    """A workspace whose tickets are all issues (`kind == "issue"`, the
    default) must render the exact same bytes AND spans as one with no PR
    segment at all. Absence of a PR is the default, same convention as
    agent state / phase / `root`."""
    refs = [
        TicketRef(provider="linear", id="ENG-123"),
        TicketRef(provider="github", id="42"),
        TicketRef(provider="gitea", id="5"),
    ]
    no_tickets_legacy = _render_card(_state(), dark=True, now=_NOW)
    no_tickets_explicit = _render_card(_state(ticket_refs=[]), dark=True, now=_NOW)
    assert no_tickets_legacy.plain == no_tickets_explicit.plain
    assert no_tickets_legacy.spans == no_tickets_explicit.spans

    issues_only = _render_card(_state(ticket_refs=refs), dark=True, now=_NOW)
    # Golden-pins the exact issue-pill formatting a PR segment must not touch.
    assert issues_only.plain == (
        "● fix-modal-focus  · 4 minutes ago\n"
        "■ test/fix-modal-focus-20260506  · claude  · active"
        "  · ENG-123  · GH#42  · GTEA#5"
    )
    for body in (no_tickets_legacy.plain, no_tickets_explicit.plain, issues_only.plain):
        assert PR_GLYPH not in body


def test_render_card_pull_request_renders_glyph_pill_and_status() -> None:
    """A PR ref renders `⇒ <pill> <status>` right after the issue pills,
    the whole segment bold + `pr_status_color` — never the issue cyan."""
    text = _render_card(
        _state(
            ticket_refs=[
                TicketRef(provider="gitea", id="5"),
                TicketRef(provider="github", id="50", kind="pull_request", status="open"),
            ]
        ),
        dark=True,
        now=_NOW,
    )
    body = text.plain
    assert "· GTEA#5" in body  # issue pill untouched
    assert f"{PR_GLYPH} GH#50 open" in body
    open_hex = pr_status_color("open", dark=True).lower()
    found = False
    for start, end, style in text.spans:
        if text.plain[start:end] == "GH#50":
            style_str = str(style).lower()
            if "bold" in style_str and open_hex in style_str:
                found = True
                break
    assert found, (
        f"PR pill span 'GH#50' should be 'bold {open_hex}'; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_render_card_pull_request_without_status_omits_status_word() -> None:
    """No blank-fill: an unenriched PR ref (no `status` yet) shows the pill
    alone — same absence convention as every other optional card piece."""
    text = _render_card(
        _state(ticket_refs=[TicketRef(provider="github", id="50", kind="pull_request")]),
        dark=True,
        now=_NOW,
    )
    assert text.plain.endswith(f"{PR_GLYPH} GH#50")


def test_pr_status_color_maps_open_merged_closed_and_unknown() -> None:
    """open → live lime (ACTIVE); merged → settled muted gray; closed →
    destructive red (ERROR); an unset/unknown status settles to muted gray
    rather than red — enrichment may simply not have landed yet."""
    assert pr_status_color("open", dark=True) == status_color(WorkspaceStatus.ACTIVE, dark=True)
    assert pr_status_color("merged", dark=True) == chrome_color("muted", dark=True)
    assert pr_status_color("closed", dark=True) == status_color(WorkspaceStatus.ERROR, dark=True)
    assert pr_status_color(None, dark=True) == chrome_color("muted", dark=True)
    # Both polarities stay distinct from each other (open vs merged vs closed).
    for dark in (True, False):
        colors = {
            pr_status_color("open", dark=dark),
            pr_status_color("merged", dark=dark),
            pr_status_color("closed", dark=dark),
        }
        assert len(colors) == 3


def test_render_card_merged_pr_reads_as_settled_muted_gray() -> None:
    """A merged PR is visually terminal — same muted hex PAUSED/OFFLINE use
    for "no live signal", never the live-lime open color."""
    ref = TicketRef(provider="github", id="50", kind="pull_request", status="merged")
    text = _render_card(_state(ticket_refs=[ref]), dark=True, now=_NOW)
    muted_hex = chrome_color("muted", dark=True).lower()
    for start, end, style in text.spans:
        if text.plain[start:end] == "GH#50":
            assert muted_hex in str(style).lower()
            return
    raise AssertionError("expected a 'GH#50' span")


def test_pr_glyph_does_not_collide_with_status_agent_state_or_phase_families() -> None:
    """The PR glyph is a fourth family (Arrows), disjoint from the
    circles/shapes status + agent-state glyphs and the growing-block phase
    ramp — mirrors the phase-glyph collision test above."""
    other_glyphs = set(STATUS_GLYPH.values()) | set(AGENT_STATE_GLYPH.values())
    other_glyphs |= set(PHASE_GLYPH.values())
    assert PR_GLYPH not in other_glyphs


# ─── provisioning (the status whose remedy is to wait) ───────────────────────


def test_render_card_provisioning_reads_as_working_with_a_clock() -> None:
    """The row a user sees for 49 s (warm) to 6.5 min (cold) must say what is
    happening and for how long. Glyph, label and elapsed time all render, and
    the elapsed time takes the status hue so label and clock read as one fact."""
    state = _state(
        status=WorkspaceStatus.PROVISIONING,
        provision_started_at=(_NOW - timedelta(seconds=134)).isoformat(),
    )
    text = _render_card(state, dark=True, now=_NOW)
    body = text.plain
    assert body.startswith(f"{STATUS_GLYPH[WorkspaceStatus.PROVISIONING]} ")
    assert "provisioning 2m14s" in body
    prov_hex = status_color(WorkspaceStatus.PROVISIONING, dark=True).lower()
    for start, end, style in text.spans:
        if body[start:end] == " 2m14s":
            assert prov_hex in str(style).lower()
            break
    else:
        raise AssertionError(f"expected an elapsed-time span: {body!r}")


def test_render_card_provisioning_without_a_start_stamp_omits_the_clock() -> None:
    """Every workspace created before the field existed has no stamp. An absent
    number is honest; a `0s` would read as "it just started" and restart the
    user's patience on every render."""
    body = _render_card(_state(status=WorkspaceStatus.PROVISIONING), dark=True, now=_NOW).plain
    assert "provisioning" in body
    assert "0s" not in body


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (49, "49s"), (60, "1m00s"), (134, "2m14s"), (3600, "1h00m"), (4260, "1h11m")],
)
def test_format_wait_keeps_seconds_until_they_stop_mattering(seconds: int, expected: str) -> None:
    """`humanize.naturaltime` rounds the entire warm-start range to "a minute",
    which is exactly the range the user is staring at the clock for."""
    assert format_wait(seconds) == expected


def test_provisioning_glyph_is_free_across_every_axis_and_the_chrome() -> None:
    """A new status glyph must collide with nothing — the other status circles,
    the agent-state circles, the phase block ramp, the runtime squares, the PR
    arrow, and the status bar's own chrome marks (the axis-only checks above
    would wave a chrome collision straight through)."""
    glyph = STATUS_GLYPH[WorkspaceStatus.PROVISIONING]
    others = {g for s, g in STATUS_GLYPH.items() if s is not WorkspaceStatus.PROVISIONING}
    assert glyph not in others
    assert glyph not in set(AGENT_STATE_GLYPH.values())
    assert glyph not in set(PHASE_GLYPH.values())
    assert glyph not in set(RUNTIME_GLYPH.values())
    assert glyph != PR_GLYPH
    assert glyph not in {
        status_widget._GLYPH_REPO,
        status_widget._GLYPH_BRANCH,
        status_widget._GLYPH_FILTER,
        status_widget._GLYPH_SELECT,
        status_widget._GLYPH_DIVIDER,
        status_widget._GLYPH_UPDATE,
    }
    # Both frames of the ACTIVE pulse too — a glyph that appears mid-pulse is
    # just as ambiguous as one in a static map.
    assert glyph not in {active_pulse(frame, dark=True)[0] for frame in range(ACTIVE_PULSE_FRAMES)}


def test_render_card_non_provisioning_row_with_a_stale_stamp_is_unchanged() -> None:
    """`provision_started_at` outlives the provision (it stays on the record so
    a finished build can still be timed), so the clock must be gated on the
    STATUS, not on the field being set — otherwise every container workspace
    ever built carries a forever-growing timer on its row."""
    stamped = _state(provision_started_at=(_NOW - timedelta(seconds=90)).isoformat())
    assert (
        _render_card(stamped, dark=True, now=_NOW).plain
        == _render_card(_state(), dark=True, now=_NOW).plain
    )
