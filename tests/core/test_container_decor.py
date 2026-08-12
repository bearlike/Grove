"""Grove's own chrome inside a container workspace.

Two halves, and the second one is the point.

The planner is ordinary pure-unit territory: a payload resolves, a plan turns
three booleans into one mount and two strings, and an incomplete payload plans
nothing rather than half a status bar.

The assets are SHELL, and this repo has been burned repeatedly by shell that was
only ever asserted as a Python string — a script can be syntactically valid,
correctly quoted, and still exit non-zero the first time a real `sh` runs it.
So every shipped asset is syntax-checked with `sh -n`, and both scripts are
actually EXECUTED: `resources.sh` against synthetic cgroup v1/v2 trees (the host
root cgroup carries no `cpu.max`, so its interesting paths are unreachable in
place) and `statusline.sh` against a realistic payload, an empty stdin, a
malformed one, and a PATH with no `python3`.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from grove.core.container_decor import CONTAINER_DECOR_ROOT, DecorPayload, DecorPlan

# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def _payload_dir(root: Path, *, omit: str = "") -> Path:
    """A decor directory shaped like the packaged one, optionally incomplete."""
    root.mkdir(parents=True, exist_ok=True)
    for name in DecorPayload.ASSET_NAMES:
        if name != omit:
            (root / name).write_text("# stub\n", encoding="utf-8")
    return root


def _run(
    script: Path,
    *,
    stdin: str = "",
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, str]:
    """Run one asset through a real ``sh``, exactly as Grove invokes it.

    ``/bin/sh`` absolute, so a test that deliberately narrows ``PATH`` is
    narrowing what the SCRIPT can find rather than breaking its own launch.
    """
    result = subprocess.run(
        ["/bin/sh", str(script)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
        cwd=str(cwd) if cwd is not None else None,
        timeout=30,
    )
    return result.returncode, result.stdout


def _minimal_path(root: Path, *names: str) -> Path:
    """A PATH directory holding only *names*, to hide one tool from a script."""
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        found = next(
            (Path(d) / name for d in ("/bin", "/usr/bin") if (Path(d) / name).exists()), None
        )
        if found is not None:
            (root / name).symlink_to(found)
    return root


def _cgroup_v2(root: Path, *, cpu_max: str, usage_usec: int, mem: int, mem_max: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cpu.max").write_text(f"{cpu_max}\n", encoding="utf-8")
    (root / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 0\n", encoding="utf-8")
    (root / "memory.current").write_text(f"{mem}\n", encoding="utf-8")
    (root / "memory.max").write_text(f"{mem_max}\n", encoding="utf-8")
    return root


# ─────────────────────────────────────────────────────────────────────────────
# the packaged payload
# ─────────────────────────────────────────────────────────────────────────────


def test_bundled_payload_ships_every_asset() -> None:
    """The packaging test: a wheel that drops an asset breaks decor silently."""
    payload = DecorPayload.resolve("")
    assert payload.managed is True
    assert payload.root == DecorPayload.bundled()
    assert payload.missing == ()
    assert payload.available is True
    for name in DecorPayload.ASSET_NAMES:
        assert (payload.root / name).is_file(), name


def test_resolve_prefers_an_operator_directory_and_marks_it_unmanaged(tmp_path: Path) -> None:
    payload = DecorPayload.resolve(f"  {tmp_path}  ")
    assert payload.root == tmp_path
    assert payload.managed is False


def test_a_payload_missing_one_asset_is_not_available(tmp_path: Path) -> None:
    payload = DecorPayload(root=_payload_dir(tmp_path, omit=DecorPayload.RESOURCES_NAME))
    assert payload.available is False
    assert payload.missing == (DecorPayload.RESOURCES_NAME,)
    assert DecorPayload.RESOURCES_NAME in payload.detail


def test_detail_distinguishes_a_packaging_miss_from_an_operator_one(tmp_path: Path) -> None:
    """Different remedies, so they must not read the same in a provision log."""
    root = _payload_dir(tmp_path, omit=DecorPayload.TMUX_CONF_NAME)
    assert "packaged" in DecorPayload(root=root).detail
    assert "configured" in DecorPayload(root=root, managed=False).detail


# ─────────────────────────────────────────────────────────────────────────────
# the plan
# ─────────────────────────────────────────────────────────────────────────────


def test_everything_enabled_plans_one_readonly_mount_and_both_values(tmp_path: Path) -> None:
    payload = DecorPayload(root=_payload_dir(tmp_path))
    plan = DecorPlan.from_config(enabled=True, statusline=True, tmux_conf=True, payload=payload)

    assert plan.enabled is True
    assert len(plan.mounts) == 1
    mount = plan.mounts[0]
    assert mount.source == tmp_path
    assert mount.target == CONTAINER_DECOR_ROOT
    assert mount.readonly is True
    assert plan.mount_flags == (f"type=bind,source={tmp_path},target=/grove/decor,readonly",)
    assert plan.tmux_conf is not None
    assert str(plan.tmux_conf) == "/grove/decor/tmux.conf"
    assert plan.statusline_command == "sh /grove/decor/statusline.sh"


def test_disabled_plans_nothing_at_all(tmp_path: Path) -> None:
    payload = DecorPayload(root=_payload_dir(tmp_path))
    plan = DecorPlan.from_config(enabled=False, statusline=True, tmux_conf=True, payload=payload)
    assert plan.mounts == ()
    assert plan.tmux_conf is None
    assert plan.statusline_command == ""
    assert plan.enabled is False


def test_statusline_off_suppresses_only_the_statusline(tmp_path: Path) -> None:
    payload = DecorPayload(root=_payload_dir(tmp_path))
    plan = DecorPlan.from_config(enabled=True, statusline=False, tmux_conf=True, payload=payload)
    assert plan.statusline_command == ""
    assert plan.tmux_conf is not None
    assert len(plan.mounts) == 1


def test_tmux_conf_off_suppresses_only_the_tmux_conf(tmp_path: Path) -> None:
    payload = DecorPayload(root=_payload_dir(tmp_path))
    plan = DecorPlan.from_config(enabled=True, statusline=True, tmux_conf=False, payload=payload)
    assert plan.tmux_conf is None
    assert plan.statusline_command == "sh /grove/decor/statusline.sh"
    assert len(plan.mounts) == 1


def test_an_unavailable_payload_plans_nothing_rather_than_half_a_status_bar(
    tmp_path: Path,
) -> None:
    payload = DecorPayload(root=_payload_dir(tmp_path, omit=DecorPayload.STATUSLINE_NAME))
    plan = DecorPlan.from_config(enabled=True, statusline=True, tmux_conf=True, payload=payload)
    assert plan.mounts == ()
    assert plan.tmux_conf is None
    assert plan.statusline_command == ""


def test_both_assets_are_invoked_through_sh_never_an_execute_bit() -> None:
    """A mode lost through a wheel build must not blank the chrome silently."""
    plan = DecorPlan.from_config(
        enabled=True, statusline=True, tmux_conf=True, payload=DecorPayload.resolve("")
    )
    assert plan.statusline_command.startswith("sh /")
    conf = (DecorPayload.bundled() / DecorPayload.TMUX_CONF_NAME).read_text(encoding="utf-8")
    assert f"sh {CONTAINER_DECOR_ROOT / DecorPayload.RESOURCES_NAME}" in conf


def test_the_tmux_conf_carries_a_container_badge_and_leaves_grove_state_alone() -> None:
    conf = (DecorPayload.bundled() / DecorPayload.TMUX_CONF_NAME).read_text(encoding="utf-8")
    assert "CONTAINER" in conf
    assert "status-interval 5" in conf
    assert "mouse on" in conf
    directives = [
        line.strip() for line in conf.splitlines() if line.strip() and not line.startswith("#")
    ]
    # Grove sets these per session deliberately; a config file would fight it.
    fought = ("set -g remain-on-exit", "set -g prefix")
    assert not any(line.startswith(fought) for line in directives)


# ─────────────────────────────────────────────────────────────────────────────
# the assets, run for real
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [DecorPayload.RESOURCES_NAME, DecorPayload.STATUSLINE_NAME])
def test_every_shipped_script_passes_a_real_shell_syntax_check(name: str) -> None:
    result = subprocess.run(
        ["sh", "-n", str(DecorPayload.bundled() / name)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_resources_never_reads_the_host_load_average() -> None:
    """The invariant the whole script exists for; a comment names it, code must not."""
    text = (DecorPayload.bundled() / DecorPayload.RESOURCES_NAME).read_text(encoding="utf-8")
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any("loadavg" in line for line in code)


def test_resources_exits_clean_on_this_host(tmp_path: Path) -> None:
    """The real host: whatever it can read, it must never fail or hang."""
    code, _ = _run(
        DecorPayload.bundled() / DecorPayload.RESOURCES_NAME, env={"TMPDIR": str(tmp_path)}
    )
    assert code == 0


def test_resources_prints_nothing_when_no_cgroup_is_readable(tmp_path: Path) -> None:
    absent = tmp_path / "no-cgroup"
    absent.mkdir()
    code, out = _run(
        DecorPayload.bundled() / DecorPayload.RESOURCES_NAME,
        env={"GROVE_CGROUP_ROOT": str(absent), "TMPDIR": str(tmp_path)},
    )
    assert code == 0
    assert out.strip() == ""


def test_resources_reads_a_capped_cgroup_v2_and_needs_two_samples_for_cpu(tmp_path: Path) -> None:
    """The first call has no previous sample, so it reports memory only."""
    cgroup = _cgroup_v2(
        tmp_path / "v2",
        cpu_max="150000 100000",  # 1.5 vCPU, Grove's own devcontainer cap
        usage_usec=1_000_000,
        mem=1288490188,
        mem_max="8589934592",
    )
    env = {"GROVE_CGROUP_ROOT": str(cgroup), "TMPDIR": str(tmp_path)}
    script = DecorPayload.bundled() / DecorPayload.RESOURCES_NAME

    code, first = _run(script, env=env)
    assert code == 0
    assert first.strip() == "mem 1.1G/8.0G"
    assert "cpu" not in first

    # Two seconds of wall clock against three seconds of consumed CPU on a
    # 1.5-vCPU cap is exactly 100%.
    time.sleep(2)
    (cgroup / "cpu.stat").write_text("usage_usec 4000000\nuser_usec 0\n", encoding="utf-8")
    code, second = _run(script, env=env)
    assert code == 0
    assert second.strip() == "cpu 100% mem 1.1G/8.0G"


def test_resources_reads_a_cgroup_v1_tree(tmp_path: Path) -> None:
    cgroup = tmp_path / "v1"
    (cgroup / "cpu").mkdir(parents=True)
    (cgroup / "cpuacct").mkdir(parents=True)
    (cgroup / "memory").mkdir(parents=True)
    (cgroup / "cpu" / "cpu.cfs_quota_us").write_text("50000\n", encoding="utf-8")
    (cgroup / "cpu" / "cpu.cfs_period_us").write_text("100000\n", encoding="utf-8")
    (cgroup / "cpuacct" / "cpuacct.usage").write_text("1000000000\n", encoding="utf-8")
    (cgroup / "memory" / "memory.usage_in_bytes").write_text("536870912\n", encoding="utf-8")
    # v1 spells "uncapped" as a sentinel near 2^63, not as a keyword.
    (cgroup / "memory" / "memory.limit_in_bytes").write_text(
        "9223372036854771712\n", encoding="utf-8"
    )
    env = {"GROVE_CGROUP_ROOT": str(cgroup), "TMPDIR": str(tmp_path)}
    script = DecorPayload.bundled() / DecorPayload.RESOURCES_NAME

    code, first = _run(script, env=env)
    assert code == 0
    assert first.strip() == "mem 512M"  # no total: the limit is the uncapped sentinel

    time.sleep(2)
    (cgroup / "cpuacct" / "cpuacct.usage").write_text("2000000000\n", encoding="utf-8")
    code, second = _run(script, env=env)
    assert code == 0
    assert second.strip() == "cpu 100% mem 512M"  # 1s of CPU over 2s at a 0.5 cap


def test_an_uncapped_cgroup_reports_against_the_cpus_it_may_actually_use(tmp_path: Path) -> None:
    """No quota means no cap to divide by, so the denominator is the affinity mask."""
    cgroup = _cgroup_v2(
        tmp_path / "unc", cpu_max="max 100000", usage_usec=0, mem=104857600, mem_max="max"
    )
    env = {"GROVE_CGROUP_ROOT": str(cgroup), "TMPDIR": str(tmp_path)}
    script = DecorPayload.bundled() / DecorPayload.RESOURCES_NAME

    code, first = _run(script, env=env)
    assert code == 0
    assert first.strip() == "mem 100M"  # uncapped memory prints no total either

    time.sleep(2)
    (cgroup / "cpu.stat").write_text("usage_usec 2000000\nuser_usec 0\n", encoding="utf-8")
    code, second = _run(script, env=env)
    assert code == 0
    assert re.match(r"^cpu \d+% mem 100M$", second.strip()), second


def test_an_uncapped_cgroup_with_no_nproc_omits_the_cpu_segment_entirely(tmp_path: Path) -> None:
    """Rather than inventing a denominator — the /proc/loadavg mistake, again."""
    cgroup = _cgroup_v2(
        tmp_path / "unc", cpu_max="max 100000", usage_usec=0, mem=104857600, mem_max="max"
    )
    env = {
        "GROVE_CGROUP_ROOT": str(cgroup),
        "TMPDIR": str(tmp_path),
        "PATH": str(_minimal_path(tmp_path / "bin", "date", "id")),
    }
    script = DecorPayload.bundled() / DecorPayload.RESOURCES_NAME

    code, _ = _run(script, env=env)
    assert code == 0
    time.sleep(2)
    (cgroup / "cpu.stat").write_text("usage_usec 2000000\nuser_usec 0\n", encoding="utf-8")
    code, second = _run(script, env=env)
    assert code == 0
    assert second.strip() == "mem 100M"


PAYLOAD = """{
  "model": {"display_name": "Claude Opus 5"},
  "workspace": {"current_dir": "%s"},
  "cwd": "%s",
  "effort": {"level": "high"},
  "context_window": {
    "used_percentage": 42.4,
    "context_window_size": 200000,
    "total_input_tokens": 80000,
    "total_output_tokens": 4000
  }
}"""

#: Every statusline assertion pins a width. Left to itself the script asks
#: ``tput``, which answers off whatever ``TERM`` the developer's shell exported
#: — so an unpinned test asserts against a layout tier that varies by machine,
#: which is the "reads a real host artifact" trap this module has already been
#: bitten by twice.
WIDE = "140"


def _statusline(
    stdin: str = "",
    *,
    columns: str = WIDE,
    glyphs: str = "",
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, str]:
    overrides = {"GROVE_STATUSLINE_COLUMNS": columns}
    if glyphs:
        overrides["GROVE_STATUSLINE_GLYPHS"] = glyphs
    overrides.update(env or {})
    return _run(
        DecorPayload.bundled() / DecorPayload.STATUSLINE_NAME,
        stdin=stdin,
        env=overrides,
        cwd=cwd,
    )


def _plain(out: str) -> str:
    """Rendered output with every SGR sequence removed.

    Assertions run over this, never the raw stream: an ANSI reset is literally
    ``\\x1b[0m``, so a naive ``"0m" not in out`` passes only on a colourless run
    and fails on every real one.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", out)


def _segment(out: str, marker: str) -> str:
    """The one ``  ``-separated segment carrying *marker*, ANSI stripped.

    Scoping every assertion to its own segment is the second half of the same
    lesson: this checkout's real branch name lands in the git segment, so a
    substring search over the whole render depends on what the branch is called.
    """
    for line in _plain(out).splitlines():
        for piece in line.split("  "):
            if marker in piece:
                return piece.strip()
    return ""


def test_statusline_renders_the_container_marker_and_every_segment(tmp_path: Path) -> None:
    code, out = _statusline(PAYLOAD % (tmp_path, tmp_path), glyphs="ascii")
    assert code == 0
    plain = _plain(out)
    assert "DEV CONTAINER" in plain
    assert "Claude Opus 5" in plain
    assert _segment(out, "ctx").startswith("ctx 42%")
    # Reported as headroom, because that is what a decision turns on.
    assert "116k left of 200k" in _segment(out, "ctx")
    assert _segment(out, "effort") == "effort high"
    assert tmp_path.name[-12:] in plain


def test_statusline_names_the_user_and_the_machine(tmp_path: Path) -> None:
    """The load-bearing pair: inside a container, "which machine" is ambiguous."""
    code, out = _statusline(
        PAYLOAD % (tmp_path, tmp_path),
        glyphs="ascii",
        env={"USER": "agent", "HOSTNAME": "grove-box.example.test"},
    )
    assert code == 0
    # Short form only — a status bar has no room for an FQDN, and the leading
    # label is the part that names the machine.
    assert _segment(out, "agent@") == "agent@grove-box"


def test_the_identity_chain_falls_back_past_an_empty_environment(tmp_path: Path) -> None:
    """A slim image may have no ``whoami``, no ``hostname`` and no ``$USER``."""
    etc = tmp_path / "etc"
    etc.mkdir()
    binaries = _minimal_path(tmp_path / "bin", "cat", "git", "date", "sh", "python3")
    assert not (binaries / "id").exists()
    assert not (binaries / "hostname").exists()

    code, out = _statusline(
        PAYLOAD % (tmp_path, tmp_path),
        glyphs="ascii",
        env={"USER": "", "LOGNAME": "", "USERNAME": "", "HOSTNAME": "", "PATH": str(binaries)},
    )
    assert code == 0
    plain = _plain(out)
    assert "DEV CONTAINER" in plain
    # `$HOME` is the last username source and `/etc/hostname` the machine one —
    # this host has both, and neither costs a fork.
    home = os.environ.get("HOME", "")
    if home and home != "/":
        assert Path(home).name in plain
    assert Path("/etc/hostname").read_text(encoding="utf-8").strip().split(".")[0] in plain


def test_a_nameless_user_leaves_the_machine_alone_rather_than_a_bare_at_sign(
    tmp_path: Path,
) -> None:
    """Half an identity degrades to the half that exists, never to ``@host``.

    ``HOME=/`` is the one value that exhausts the username chain on a host that
    still answers for its own name, so it isolates exactly this half.
    """
    binaries = _minimal_path(tmp_path / "bin", "cat", "git", "date", "sh", "python3")
    machine = Path("/etc/hostname").read_text(encoding="utf-8").strip().split(".")[0]
    code, out = _statusline(
        PAYLOAD % (tmp_path, tmp_path),
        glyphs="ascii",
        env={
            "USER": "",
            "LOGNAME": "",
            "USERNAME": "",
            "HOSTNAME": "",
            "HOME": "/",
            "PATH": str(binaries),
        },
    )
    assert code == 0
    assert _segment(out, machine) == machine


def test_statusline_computes_the_gauge_from_current_usage_when_totals_are_absent() -> None:
    payload = (
        '{"context_window": {"context_window_size": 200000, "current_usage": '
        '{"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 50000}}}'
    )
    code, out = _statusline(payload, glyphs="ascii")
    assert code == 0
    assert _segment(out, "ctx").startswith("ctx 25%")


@pytest.mark.parametrize("stdin", ["", "not json{{", "[]", '{"model": null}'])
def test_statusline_survives_every_shape_of_unusable_payload(stdin: str) -> None:
    code, out = _statusline(stdin)
    assert code == 0
    assert "DEV CONTAINER" in _plain(out)


def test_statusline_degrades_to_the_process_cwd_with_no_python3(tmp_path: Path) -> None:
    """A node-only image is a real case: marker plus directory, never garbage.

    This pins a DECISION, not just a behaviour, so it deliberately runs from a
    directory DIFFERENT from the one the payload names and asserts the process
    wins. With nothing able to parse the payload, the directory segment falls
    back to the process's own cwd rather than scraping ``workspace.current_dir``
    out of the JSON with sed/grep — Claude Code spawns the statusline IN the
    agent's cwd, so the two agree in production, and a hand-rolled JSON parse on
    the one path that exists because the machine is impoverished buys nothing.
    """
    binaries = _minimal_path(tmp_path / "bin", "cat", "git", "id", "date", "sh")
    assert not (binaries / "python3").exists()
    from_payload = tmp_path / "named-by-the-payload"
    from_payload.mkdir()
    from_process = tmp_path / "where-the-process-runs"
    from_process.mkdir()

    code, out = _statusline(
        PAYLOAD % (from_payload, from_payload),
        glyphs="ascii",
        env={"PATH": str(binaries)},
        cwd=from_process,
    )
    plain = _plain(out)
    assert code == 0
    assert "DEV CONTAINER" in plain
    assert "Claude Opus 5" not in plain  # nothing parsed the payload
    assert from_process.name in plain
    assert from_payload.name not in plain
    # The identity segment needs no interpreter at all, so it survives here —
    # which is most of why the machine is worth putting on this line.
    assert "@" in plain


def test_statusline_reports_subscription_usage_from_the_payload() -> None:
    """The pools a subscription login carries, with their reset countdowns.

    Read straight off `rate_limits`, which Claude Code populates for an OAuth
    login — and a container has one, because the credential store is shared
    into it. There is deliberately no cache fallback: the host's polled
    snapshot is not shared in, and a number invented for a pool we cannot read
    would be worse than an absent segment.
    """
    soon = int(time.time()) + 7500
    later = int(time.time()) + 320_000
    payload = (
        f'{{"rate_limits": {{"five_hour": {{"used_percentage": 12, "resets_at": {soon}}},'
        f' "seven_day": {{"used_percentage": 86, "resets_at": {later}}}}}}}'
    )
    code, out = _statusline(payload, glyphs="ascii")

    assert code == 0
    usage = _segment(out, "5h ")
    assert "5h 12%" in usage
    assert "7d 86%" in usage
    # Coarsest still-useful unit: minutes are noise once a reset is days out.
    assert "2h 5m" in usage
    assert "3d 16h" in usage


def test_statusline_omits_usage_entirely_for_a_login_that_has_no_pools(tmp_path: Path) -> None:
    """An API-key login carries no subscription pools, and 0% would be a lie.

    "Not applicable" and "none consumed" are different answers, and only one of
    them is true here — so the segment is absent rather than zeroed.

    Rendered from a NON-repo cwd with a NEUTRAL name, and both halves are
    load-bearing. **An assertion that something is absent has to control every
    channel that could supply it**, and this render has two: the git segment
    carries the checkout's real branch name (``feat/usage-audit`` is what caught
    this, and scoping to a segment does not help when the marker matches the git
    segment itself), and the directory segment carries the cwd's — which for a
    bare ``tmp_path`` is pytest's slug of THIS FUNCTION'S NAME, the word
    ``usage`` included. Scoping harder fixes neither; removing the sources does.
    """
    work = tmp_path / "elsewhere"
    work.mkdir()
    code, out = _statusline(
        '{"model": {"display_name": "Claude Opus 5"}}', glyphs="ascii", cwd=work
    )

    assert code == 0
    plain = _plain(out)
    assert "usage" not in plain
    assert "5h " not in plain
    assert "7d " not in plain


def test_statusline_drops_a_reset_clause_that_has_already_passed() -> None:
    """A stale `resets_at` loses its clause, never renders as `0m`, keeps the pool.

    The percentage is still the truth the payload reported; only the countdown
    has expired, so dropping the whole segment would discard a live number.
    """
    payload = '{"rate_limits": {"five_hour": {"used_percentage": 9, "resets_at": 100}}}'
    code, out = _statusline(payload, glyphs="ascii")

    assert code == 0
    usage = _segment(out, "5h ")
    assert usage == "usage 5h 9%"
    assert "(" not in usage


# ─────────────────────────────────────────────────────────────────────────────
# layout: the icon vocabulary and the narrow-pane tiers
# ─────────────────────────────────────────────────────────────────────────────


def test_the_icon_vocabulary_replaces_the_words_rather_than_decorating_them(
    tmp_path: Path,
) -> None:
    """An icon that sits next to its own label has bought nothing but width."""
    now = int(time.time())
    payload = PAYLOAD % (tmp_path, tmp_path)
    payload = payload.rstrip()[:-1] + (
        f', "rate_limits": {{"five_hour": {{"used_percentage": 12, "resets_at": {now + 7500}}}}}}}'
    )
    code, out = _statusline(payload)

    assert code == 0
    plain = _plain(out)
    for word in ("ctx", "effort", "usage", "git "):
        assert word not in plain, word
    # The badge is the one segment that must survive a font rendering nothing
    # else, so it asks least of it: a plain geometric shape, not a private-use
    # glyph, and the words stay.
    assert "⬢ DEV CONTAINER" in plain
    assert "Claude Opus 5" in plain
    assert "5h 12%" in plain


def test_the_ascii_vocabulary_puts_the_words_back_instead_of_mangling_the_icons(
    tmp_path: Path,
) -> None:
    """A font lives in the terminal a human attached FROM, never in this image.

    So there is nothing honest to probe from in here, and the defence is a
    switch rather than a detection — one whose fallback is readable prose, not a
    row of replacement boxes.
    """
    code, out = _statusline(PAYLOAD % (tmp_path, tmp_path), glyphs="ascii")

    assert code == 0
    plain = _plain(out)
    assert "[ DEV CONTAINER ]" in plain
    assert _segment(out, "ctx").startswith("ctx ")
    assert _segment(out, "effort").startswith("effort ")
    # Not one private-use codepoint survives the switch. Asserted on the Nerd
    # Font's own ranges rather than on `isascii()`: `·` and `…` are ordinary
    # punctuation every font on earth ships, and giving those up would cost
    # legibility to defend against a problem nobody has.
    assert not [ch for ch in plain if 0xE000 <= ord(ch) <= 0xF8FF or ord(ch) >= 0xF0000], plain
    assert "⬢" not in plain


@pytest.mark.parametrize("columns", ["140", "90", "60", "40"])
def test_every_tier_keeps_the_badge_the_machine_and_two_lines(columns: str, tmp_path: Path) -> None:
    """Whatever else goes, these do not — and the render never grows a third row."""
    code, out = _statusline(PAYLOAD % (tmp_path, tmp_path), columns=columns, glyphs="ascii")

    assert code == 0
    lines = [line for line in _plain(out).splitlines() if line.strip()]
    assert len(lines) == 2
    assert "CONTAINER" in lines[0]
    assert "42%" in lines[1]


def test_a_narrow_pane_elides_rather_than_wraps(tmp_path: Path) -> None:
    """Every variable-length field is capped, so the worst case is a known width.

    A wrapped line costs a second terminal row for two words and shifts every
    row after it, which is worse than the fact it was carrying.
    """
    deep = tmp_path / "a-rather-long-project-name" / "and-a-long-workspace-leaf-name"
    deep.mkdir(parents=True)
    wide_code, wide_out = _statusline(PAYLOAD % (deep, deep), columns="140", glyphs="ascii")
    narrow_code, narrow_out = _statusline(PAYLOAD % (deep, deep), columns="60", glyphs="ascii")

    assert wide_code == 0 and narrow_code == 0
    for line in _plain(narrow_out).splitlines():
        assert len(line) <= 66, line  # the badge alone is 17 of a 60-column pane
    # Narrower means strictly less, never a different set of facts in a new order.
    assert len(_plain(narrow_out)) < len(_plain(wide_out))
    # The countdown is the first thing to go: the percentage is what a decision
    # turns on, the countdown only says when it stops mattering.
    assert "left of" in _plain(wide_out)
    assert "left of" not in _plain(narrow_out)
