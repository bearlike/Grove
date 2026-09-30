"""The rendered status row Grove's ``statusLine`` registration draws in place of the user's."""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from grove import statusline
from grove.core.agents.hook import _context_from_statusline
from grove.hook_producer import HookProducer
from grove.statusline import (
    Ansi,
    Git,
    GitState,
    Glyphs,
    Phase,
    Platform,
    QuotaSnapshot,
    StatusLine,
    context_usage,
    visible_width,
)

_WINDOW = {
    "context_window_size": 1_000_000,
    "current_usage": {
        "input_tokens": 1_000,
        "output_tokens": 500,
        "cache_creation_input_tokens": 2_000,
        "cache_read_input_tokens": 196_500,
    },
}
_PAYLOAD = {
    "session_id": "s-1",
    "cwd": "/home/dev/src/app",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
    "effort": {"level": "high"},
    "context_window": _WINDOW,
}


class _Platform(Platform):
    def __init__(self, load: float | None = 0.5, width: int = 200) -> None:
        super().__init__({})
        self._load, self._width = load, width

    def user_at_host(self) -> str:
        return "dev@box"

    def home(self) -> str:
        return "/home/dev"

    def load_average(self) -> float | None:
        return self._load

    def terminal_width(self, fallback: int = 100) -> int:
        return self._width


class _Git(Git):
    def __init__(self, state: GitState | None) -> None:
        self._state = state

    def read(self, cwd: str) -> GitState | None:
        return self._state


def _line(
    payload: dict[str, object] | None = None,
    *,
    env: dict[str, str] | None = None,
    platform: Platform | None = None,
    git: GitState | None = None,
    ascii_only: bool = False,
    quotas: QuotaSnapshot | None = None,
    now: float = 0.0,
) -> str:
    environ = env or {}
    return StatusLine(
        _PAYLOAD if payload is None else payload,
        env=environ,
        platform=platform or _Platform(),
        git=_Git(git),
        phase=Phase(environ),
        glyphs=Glyphs(ascii_only),
        quotas=quotas or QuotaSnapshot(None, spawn=_never),
        now=now,
    ).render()


def _never(path: Path) -> None:
    raise AssertionError(f"unexpected quota refresh of {path}")


def _plain(text: str) -> str:
    return statusline._ANSI_RE.sub("", text)


# -- the context rule ----------------------------------------------------------


def test_context_sums_the_four_token_classes_over_the_window() -> None:
    assert context_usage(_PAYLOAD) == (200_000, 1_000_000)


@pytest.mark.parametrize(
    "window",
    [
        {"context_window_size": 200_000, "current_usage": None},
        {"context_window_size": 0, "current_usage": {"input_tokens": 5}},
        {"context_window_size": 200_000, "current_usage": {"input_tokens": True}},
        {"current_usage": {"input_tokens": 5}},
    ],
)
def test_an_unmeasured_window_is_absent_rather_than_zero(window: dict[str, object]) -> None:
    assert context_usage({"context_window": window}) is None
    assert "%" not in _plain(_line({"context_window": window}))


def test_the_sidecar_fold_and_the_rendered_row_read_one_rule() -> None:
    folded = _context_from_statusline(_PAYLOAD)
    assert folded is not None
    assert (folded.used, folded.size) == context_usage(_PAYLOAD)
    assert "20% (200K / 1.0M)" in _plain(_line())


# -- git -----------------------------------------------------------------------


def test_porcelain_v2_yields_branch_distance_and_all_three_counts() -> None:
    porcelain = "\n".join(
        [
            "# branch.oid 4f29b594707667a31a4e7582c2ef71d58fb91906",
            "# branch.head feature/x",
            "# branch.upstream origin/feature/x",
            "# branch.ab +2 -1",
            "1 M. N... 100644 100644 100644 a b src/staged.py",
            "1 .M N... 100644 100644 100644 a b src/unstaged.py",
            "1 MM N... 100644 100644 100644 a b src/both.py",
            "2 R. N... 100644 100644 100644 a b R100 new.py\told.py",
            "u UU N... 100644 100644 100644 100644 a b c conflict.py",
            "? scratch.txt",
            "? notes.md",
        ]
    )
    assert GitState.parse(porcelain) == GitState(
        "feature/x", ahead=2, behind=1, staged=3, unstaged=3, untracked=2
    )


def test_a_detached_head_names_the_short_commit() -> None:
    porcelain = "# branch.oid 4f29b594707667a3\n# branch.head (detached)\n"
    assert GitState.parse(porcelain) == GitState("4f29b59")


def test_no_branch_header_means_not_a_repository() -> None:
    assert GitState.parse("") is None


def test_git_reads_a_real_repository(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    (tmp_path / "new.txt").write_text("x", encoding="utf-8")

    assert Git().read(str(tmp_path)) == GitState("trunk", untracked=1)
    assert Git().read(str(tmp_path / "missing")) is None


def test_the_git_cell_shows_distance_and_counts() -> None:
    plain = _plain(_line(git=GitState("main", ahead=1, staged=2, unstaged=0, untracked=3)))
    assert "main ↑1 +2 -0 ?3" in plain


# -- segments and degrades -----------------------------------------------------


def test_the_row_carries_this_agents_own_phase(tmp_path: Path) -> None:
    phase_file = tmp_path / "ws.json"
    phase_file.write_text(json.dumps({"phase": "verify", "blocked": True}), encoding="utf-8")

    plain = _plain(_line(env={"GROVE_PHASE_FILE": str(phase_file)}, ascii_only=True))

    assert plain.startswith("grove verify blocked")


def test_no_phase_file_still_marks_the_pane_as_grove() -> None:
    first_cell = _plain(_line(ascii_only=True)).split(" · ")[0]
    assert first_cell.strip() == "grove"


def test_a_home_directory_is_shortened_and_the_gateway_is_named() -> None:
    plain = _plain(_line(env={"ANTHROPIC_BASE_URL": "http://gateway.example:4000"}))
    assert "~/src/app" in plain
    assert "via gateway.example" in plain


def test_windows_has_no_load_average_and_drops_the_segment() -> None:
    plain = _plain(_line(platform=_Platform(load=None)))
    assert "load" not in plain
    assert "Opus 5.5 [high]" in plain


def test_the_ascii_vocabulary_uses_no_private_use_glyphs() -> None:
    rendered = _line(git=GitState("main"), ascii_only=True)
    assert not any(0xE000 <= ord(ch) <= 0xF8FF or ord(ch) >= 0xF0000 for ch in rendered)
    assert "ctx 20%" in _plain(rendered)


def test_the_nerd_vocabulary_draws_glyphs() -> None:
    assert any(ord(ch) >= 0xF0000 for ch in _line())


def test_the_grid_is_abandoned_rather_than_overflowing_a_narrow_terminal() -> None:
    narrow = _line(platform=_Platform(width=60), git=GitState("main"))
    rows = narrow.split("\n")
    assert rows[-1] == Ansi.paint("─" * 60, Ansi.DIM)
    # Unpadded: no run of alignment spaces before a separator.
    assert "  " + " · " not in _plain(rows[0]) + _plain(rows[1])


def test_the_grid_aligns_separators_when_it_fits() -> None:
    rows = _plain(_line(git=GitState("main"))).split("\n")[:2]
    assert rows[0].index(" · ") == rows[1].index(" · ")
    assert visible_width(rows[0]) <= 200


def test_an_empty_payload_still_renders_without_raising() -> None:
    assert "─" in _line({})


# -- the producer arm ----------------------------------------------------------


def _producer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, io.BytesIO]:
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("GROVE_STATUSLINE_GLYPHS", "ascii")
    out = io.BytesIO()
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(out, encoding="utf-8"))
    return state_home / "grove" / "agent-sidecars" / "spool", out


def test_the_statusline_arm_spools_and_then_draws(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool, out = _producer(monkeypatch, tmp_path)

    assert (
        HookProducer().run(["--statusline"], stdin=io.BytesIO(json.dumps(_PAYLOAD).encode())) == 0
    )

    assert len(list(spool.glob("*.statusline.json"))) == 1
    assert "ctx 20%" in _plain(out.getvalue().decode("utf-8"))


def test_a_rendering_failure_still_spools_prints_nothing_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool, out = _producer(monkeypatch, tmp_path)

    def boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("renderer broke")

    monkeypatch.setattr(statusline, "render", boom)

    assert (
        HookProducer().run(["--statusline"], stdin=io.BytesIO(json.dumps(_PAYLOAD).encode())) == 0
    )

    assert len(list(spool.glob("*.statusline.json"))) == 1
    assert out.getvalue() == b""


# -- subscription quota ----------------------------------------------------------

# 2026-09-23T15:00:00Z — every window below resets after it unless a test says otherwise.
_NOW = 1_790_175_600.0


def _window(label: str, used: float | None, resets_at: str) -> dict[str, object]:
    return {"label": label, "used_percent": used, "resets_at": resets_at}


def _acct(provider: str, label: str, windows: list[dict[str, object]], **extra: object):
    return {"provider": provider, "label": label, "status": "ok", "windows": windows, **extra}


_TWO_CLAUDES_AND_CODEX = [
    _acct(
        "claude_code",
        "a@example.com",
        [_window("5h", 8.0, "2026-09-23T16:00:00Z"), _window("7d", 90.0, "2026-09-26T11:00:00Z")],
        subscription={"label": "max"},
    ),
    _acct(
        "claude_code",
        "b@example.com",
        [_window("5h", 4.0, "2026-09-23T20:00:00Z"), _window("7d", 10.0, "2026-09-30T06:00:00Z")],
        subscription={"label": "max"},
    ),
    _acct(
        "codex",
        "a@example.com",
        [_window("7d", 52.0, "2026-09-24T23:00:00Z")],
        subscription={"label": "pro"},
    ),
]


def _snapshot(tmp_path: Path, accounts: list[dict[str, object]], *, age: float = 0.0):
    path = tmp_path / "statusline-quotas.json"
    path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    os.utime(path, (_NOW - age, _NOW - age))
    return path


def _quota_rows(text: str) -> list[str]:
    return [line for line in _plain(text).split("\n") if "quota" in line or "Codex" in line]


def test_several_accounts_of_one_vendor_share_a_cell_and_expose_the_worst(
    tmp_path: Path,
) -> None:
    path = _snapshot(tmp_path, _TWO_CLAUDES_AND_CODEX)
    rendered = _line(quotas=QuotaSnapshot(path, spawn=_never), now=_NOW, ascii_only=True)
    plain = _plain(rendered)

    assert "quota Claude x2 5h:6% [8,4] (resets in 1h 0m)" in plain
    # The 7d mean is 50% (yellow) while one account is at 90%: the breakdown is red.
    assert "7d:50% [90,10] (resets in 2d 20h)" in plain
    assert Ansi.paint("[90,10]", Ansi.RED) in rendered
    assert "Codex pro 7d:52% (resets in 1d 8h)" in plain


def test_a_window_whose_reset_has_passed_is_dropped_not_shown_as_current(
    tmp_path: Path,
) -> None:
    path = _snapshot(
        tmp_path,
        [
            _acct(
                "generic",
                "Token Plan",
                [_window("7d", 23.6, "2026-08-23T06:00:00Z")],
                status="stale",
            )
        ],
    )
    plain = _plain(_line(quotas=QuotaSnapshot(path, spawn=_never), now=_NOW, ascii_only=True))

    assert "Token Plan (stale)" in plain
    assert "23" not in plain.split("Token Plan")[1]


def test_an_unmeasured_window_is_not_rendered_as_zero(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, [_acct("codex", "x", [_window("7d", None, "2026-09-24T23:00:00Z")])])
    plain = _plain(_line(quotas=QuotaSnapshot(path, spawn=_never), now=_NOW, ascii_only=True))

    assert _quota_rows(plain) == ["quota Codex -"]


def test_an_expired_login_says_so(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, [_acct("claude_code", "a", [], status="auth_expired")])
    plain = _plain(_line(quotas=QuotaSnapshot(path, spawn=_never), now=_NOW, ascii_only=True))

    assert "Claude REAUTH" in plain


def test_quota_cells_wrap_between_cells_and_never_overflow(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, _TWO_CLAUDES_AND_CODEX)
    rendered = _line(
        quotas=QuotaSnapshot(path, spawn=_never),
        now=_NOW,
        platform=_Platform(width=80),
        ascii_only=True,
    )
    rows = _quota_rows(rendered)

    assert len(rows) >= 2
    assert all(visible_width(row) <= 80 for row in rows)


def test_no_snapshot_renders_no_quota_row(tmp_path: Path) -> None:
    spawned: list[Path] = []
    missing = tmp_path / "statusline-quotas.json"

    rendered = _line(quotas=QuotaSnapshot(missing, spawn=spawned.append), now=_NOW)

    assert _quota_rows(rendered) == []
    assert spawned == [missing]


def test_a_fresh_snapshot_is_read_without_refreshing(tmp_path: Path) -> None:
    path = _snapshot(tmp_path, _TWO_CLAUDES_AND_CODEX, age=5.0)
    spawned: list[Path] = []

    QuotaSnapshot(path, spawn=spawned.append).accounts(_NOW)

    assert spawned == []


def test_a_stale_snapshot_is_still_shown_and_triggers_exactly_one_refresh(
    tmp_path: Path,
) -> None:
    path = _snapshot(tmp_path, _TWO_CLAUDES_AND_CODEX, age=10_000.0)
    spawned: list[Path] = []

    first = QuotaSnapshot(path, spawn=spawned.append).accounts(_NOW)
    # A second pane rendering in the same period coalesces onto the claim.
    QuotaSnapshot(path, spawn=spawned.append).accounts(_NOW)

    assert len(first) == 3
    assert spawned == [path]
