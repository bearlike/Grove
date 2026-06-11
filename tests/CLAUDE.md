# tests — suite conventions + CI/lint gotchas

> ↑ [root](../CLAUDE.md)

Conventions for the pytest suite under `tests/`: where the test seams sit, how to keep cross-platform defenses honest, and the CI/lint traps. Root holds the two principles these build on ("tests pin contracts, not implementation"; "tests prefer real code paths, stub only I/O"). Engine internals live in [core](../src/grove/core/CLAUDE.md).

## Test seams

**Patch the public module surface, never a private symbol.** `tests/conftest.py` monkey-patches `grove.core.tmux.create_session` and friends directly — no Protocol abstraction. Rename a public function and the test breaks loudly; that is the design. A test that patches a private symbol turns that path into an implicit contract: move it and the patch silently no-ops while the test still passes.

**Sandbox Grove paths by patching the Grove function, not the env var.** `monkeypatch.setattr("grove.core.paths.user_schema_path", lambda: tmp_path / "...")` is the right sandbox — deterministic on every OS, matching `tests/conftest.py::tmp_state_dir` (which already redirects `user_state_path` / `user_config_path` / `user_schema_path`). Do **not** `monkeypatch.setenv("LOCALAPPDATA", ...)`: recent `platformdirs` (≥4.x) resolves the user dir via `SHGetKnownFolderPath` through `ctypes` on Windows and only falls back to env vars when `ctypes` is unavailable, so `setenv` is a no-op there — the write lands outside `tmp_path` and `rglob` returns empty. Same rule for redirecting `~/.grove`, `~/.config/grove`, or any `platformdirs.user_*_dir` path.

## Cross-platform test gating

**CI is Linux-only (`ci.yml` runs `ubuntu-latest`, no OS matrix).** tmux is a hard runtime dependency (WSL2 on Windows) and native packaging was retired, so a `[ubuntu, macos, windows]` matrix burned minutes for no signal. The Windows/macOS defensive *code* stays (POSIX import guards, `os.replace`, `platformdirs` patching, the `bash` probe) — it is cheap correctness and dropping CI is not a license to write Linux-only code. But those defenses are now **CI-unverified**: a Windows/macOS regression won't be caught automatically, so reason about path/subprocess/POSIX-module code by hand when you touch it.

**Compare resolved `Path` objects across tools, never raw strings.** Git CLI output emits `/`, Python `str(Path)` emits `\` on Windows; Linux makes the asymmetry invisible, so the bug only surfaces on Windows CI. Assert `Path(p).resolve() in {Path(q).resolve() for q in git_out}`. Same for any cross-tool path comparison (lazygit, gh, etc.).

**Gate shell-exec tests with an output probe, not `shutil.which("bash")`.** On Windows GitHub runners `bash.exe` resolves to the WSL launcher; with no installed distribution every `bash -c …` prints "Windows Subsystem for Linux has no installed distributions." and exits 1 — yet `which` still passes. Run a real probe (`bash -c "echo grove-shell-probe"`) and verify the output before skipping. (`git` escapes this only by accident: Windows runners ship real Git.)

## Lint

**Run the full `make lint` before pushing, never just `ruff check`.** `make lint` runs `ruff format --check` too. A `ruff check --fix` that reflows code can still leave a format-check failure, so a green `ruff check` is not enough.

**Keep `import-linter` honest with `include_external_packages = true`.** Without it, contracts that forbid third-party modules (textual, rich, typer, click) silently pass.

## Session lessons

- Engine-side gotchas that *manifest* as test traps live with the engine: e.g. ROOT-placement tests must spy that `worktree_remove` / `branch_delete` are never *called*, not merely that the repo survives (git itself refuses both, so a survival-only assertion passes with the gate missing) — see [core](../src/grove/core/CLAUDE.md).
