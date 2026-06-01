"""Pure-function tests for `_render_card`.

The card body is the public visual contract for the workspace list.
Pinning the rendered plain-text protects against accidental drift in
status glyphs, label normalization, and the init-failed badge — all of
which are reachable without a Pilot, which keeps these tests fast and
free of Textual app construction.

Focus chrome (the clay-accent border on highlighted cards) is purely
TCSS now, so it lives in the live-wiring tests under ``test_list_screen.py``
rather than here. This module pins what the *content* of a card looks
like; it never asserts on focus state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grove.core import InitStatus, WorkspaceState, WorkspaceStatus
from grove.tui._status import (
    ACTIVE_PULSE_FRAMES,
    active_pulse,
    chrome_color,
    ref_color,
    status_color,
)
from grove.tui.widgets.card import _render_card

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
    """Focus moved entirely into TCSS — the body never carries the older
    inline ``▌`` bar regardless of ``state.status``. A reintroduced inline
    indicator would mean two sources of truth (CSS + render); pin against it.
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
    # And specifically frame 0 stays the canonical filled disc — the
    # static lookup must continue to render as it did before.
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
