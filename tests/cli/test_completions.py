"""Shell completion — the candidate values and the install location.

The load-bearing test here is the ROUND TRIP one: it drives Click's own
``ShellComplete`` rather than calling the completer functions directly, because
Typer resolves a callback's *string* annotations at completion time
(``inspect.signature(eval_str=True)``) to decide which parameter is the context.
Calling ``Complete.models(ctx, "")`` in a test never exercises that step, so a
``click`` import that only exists under ``TYPE_CHECKING`` passes ruff, mypy, an
import of the module AND a direct-call test, then dies with ``NameError`` on the
first real TAB. That regression shipped once during this feature's development
and only a round trip caught it.

Everything else here is ordinary: real tmp git repo + the FakeTmux seam, the
shape ``tests/cli/test_edit_command.py`` uses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.shell_completion import CompletionItem, ShellComplete
from typer.completion import shell_complete
from typer.main import get_command
from typer.testing import CliRunner

from grove.tui.cli import app
from grove.tui.cli_complete import Complete, CompletionScript
from tests.conftest import FakeTmux


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Path:
    del tmp_state_dir, fake_tmux  # used via monkeypatch
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _complete(args: list[str], incomplete: str = "") -> list[CompletionItem]:
    """Everything the shell would be offered for ``args`` + a partial word.

    Goes through the same entry point the generated ``_grove`` function reaches,
    so parameter resolution, annotation evaluation and Typer's prefix filter are
    all exercised — see this module's docstring for why that matters.
    """
    completion = ShellComplete(get_command(app), {}, "grove", "_GROVE_COMPLETE")
    return completion.get_completions(args, incomplete)


def _create(runner: CliRunner, title: str = "completes") -> str:
    created = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert created.exit_code == 0, created.output
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in:\n{created.output}")


# ─── the round trip ──────────────────────────────────────────────────────────


def test_workspace_argument_completes_a_real_workspace(runner: CliRunner, project: Path) -> None:
    """`grove show <TAB>` offers the ids in this repo, helped by title."""
    del project
    ws_id = _create(runner, "completes")

    values = {item.value: item.help for item in _complete(["show"])}

    assert ws_id in values
    assert "completes" in (values[ws_id] or "")


def test_a_context_taking_completer_resolves_its_annotations(
    runner: CliRunner, project: Path
) -> None:
    """The regression guard named in this module's docstring.

    ``Complete.models`` takes ``ctx: click.Context``. Typer must be able to
    EVALUATE that annotation string at completion time to route the argument; if
    ``click`` is not a real module-scope import in ``cli_complete``, this raises
    ``NameError`` instead of returning candidates. Asserting "did not raise" is
    the whole point, so the emptiness of the result is not the subject.
    """
    del project, runner

    items = _complete(["create", "--agent", "claude", "--model"])

    assert all(isinstance(item, CompletionItem) for item in items)


def test_model_completion_is_scoped_to_the_agent_already_typed(
    runner: CliRunner, project: Path
) -> None:
    """`--model` reads `--agent` off `ctx.params`, so it offers that catalog."""
    del project, runner

    claude = {item.value for item in _complete(["create", "--agent", "claude", "--model"])}

    # The built-in claude roster entry offers its stable aliases; a codex-only
    # id must not appear when the line already names claude.
    assert "sonnet" in claude
    assert "gpt-5.5" not in claude


def test_agent_completion_comes_from_the_config_cascade(runner: CliRunner, project: Path) -> None:
    """The roster is config, so completion reflects config — not a literal list."""
    del project, runner

    names = {item.value for item in _complete(["create", "--agent"])}

    assert {"claude", "codex", "shell"} <= names


def test_phase_argument_completes_both_phases_and_workspaces(
    runner: CliRunner, project: Path
) -> None:
    """`grove phase <TAB>` accepts either domain, so it must offer both."""
    del project
    ws_id = _create(runner, "phase target")

    values = {item.value for item in _complete(["phase"])}

    assert "implementing" in values
    assert ws_id in values


def test_completion_filters_on_the_partial_word(runner: CliRunner, project: Path) -> None:
    """Typer applies the prefix filter, which is why no completer does."""
    del project, runner

    assert {item.value for item in _complete(["phase"], "impl")} == {"implementing"}


def test_command_names_still_complete(runner: CliRunner, project: Path) -> None:
    """A wired-up completer must not disturb ordinary command completion."""
    del project, runner

    assert "create" in {item.value for item in _complete([], "cre")}


# ─── the never-raise contract ────────────────────────────────────────────────


def test_completers_return_empty_outside_a_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outside a git repo every completer answers "nothing", never a traceback.

    This is the state a user is in most often when completion misfires — a home
    directory, a scratch dir — and it must degrade to Typer's `_files`.
    """
    monkeypatch.chdir(tmp_path)

    assert Complete.workspaces("") == []
    assert Complete.agents("") == []
    assert Complete.local_branches("") == []
    assert Complete.cwds("") == []


def test_help_text_is_always_one_short_line(runner: CliRunner, project: Path) -> None:
    """A newline in a description breaks the whole `_arguments` spec.

    Typer renders help through Rich, which wraps to console width — so a
    description longer than the terminal comes back containing newlines, which
    separate ENTRIES inside the zsh spec. The menu then silently never appears
    for that parameter, while short descriptions on other flags keep working.

    `zsh -n` cannot catch it (a newline inside quotes is valid syntax), so this
    asserts the property directly, over the completer whose configured help is
    long enough to have triggered it.
    """
    del project, runner

    for item in _complete(["create", "--agent"]):
        assert item.help is not None
        assert "\n" not in item.help
        assert len(item.help) <= 60


def test_a_completion_emits_no_log_output(
    runner: CliRunner, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A TAB must print nothing but candidates — not even on stderr.

    The shell captures stdout, so log lines go to the TERMINAL and paint over
    the user's prompt. That makes the bug invisible from the shell's side (the
    completion still works) and it survived a review here until a real TAB press
    in an interactive zsh showed the engine's git and config DEBUG lines
    scrolling across the screen.
    """
    del project
    _create(runner, "quiet please")
    capsys.readouterr()  # drop whatever `create` produced

    Complete.workspaces("")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_failing_seam_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An engine that raises must cost a completion, never the shell."""

    def boom() -> None:
        raise RuntimeError("engine is on fire")

    monkeypatch.setattr("grove.tui.cli_complete._manager", boom)

    assert Complete.workspaces("") == []


# ─── the install location ────────────────────────────────────────────────────


def test_render_returns_the_zsh_script_typer_would_print() -> None:
    """Grove re-authors nothing; the script is Typer's own."""
    script = CompletionScript.render("zsh")

    assert "#compdef grove" in script
    assert "_GROVE_COMPLETE" in script


def test_the_zsh_script_completes_on_the_FIRST_tab() -> None:
    """Typer's template is written to be sourced; Grove installs it on $fpath.

    Autoloaded via `#compdef`, the file's body IS the completion — but Typer's
    body only defines `_grove_completion` and calls `compdef`, so the first
    invocation registers and returns nothing. Measured in a clean zsh with a
    fresh compdump: unguarded, the first TAB does nothing and the second works;
    guarded, the first works. A user never presses TAB twice to find out, so
    that reads as "completion is broken".

    Asserting on the script text is legitimate here precisely because this
    branch is GROVE's addition, not Typer's — unlike the rest of the body, which
    this suite deliberately never pins.
    """
    script = CompletionScript.render("zsh")

    assert "loadautofunc" in script
    assert '_grove_completion "$@"' in script


@pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
def test_the_script_asks_back_with_an_instruction_the_runtime_accepts(
    shell: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The script and the handler that answers it must agree. They once did not.

    Building the script from Click's registry instead of Typer's template
    produced one that calls back with `_GROVE_COMPLETE=zsh_complete`, while
    Typer's handler parses that as `<instruction>_<shell>` — the reverse of
    Click's own order — read the shell as "complete", and printed **"Shell
    complete not supported."** on every TAB.

    Nothing about the script's text reveals this: it is well-formed zsh, it names
    the right variable, and it is what a reasonable reading of the public API
    hands you. Only running the round trip the script itself would run shows it,
    which is why this test extracts the instruction from the generated script
    rather than hard-coding the expected one — hard-coding it would pin today's
    string while re-testing nothing about agreement.
    """
    # What each shell's own wrapper exports before calling back. Supplied because
    # the handler reads them directly; their absence is a missing environment,
    # not the disagreement under test.
    monkeypatch.setenv("COMP_WORDS", "grove ")
    monkeypatch.setenv("COMP_CWORD", "1")
    monkeypatch.setenv("_TYPER_COMPLETE_ARGS", "grove ")

    script = CompletionScript.render(shell)
    match = re.search(r"_GROVE_COMPLETE=([A-Za-z_]+)", script)
    assert match, f"no _GROVE_COMPLETE=<instruction> in the {shell} script:\n{script}"

    status = shell_complete(
        get_command(app), {}, "grove", CompletionScript.complete_var(), match.group(1)
    )

    assert status == 0, (
        f"the {shell} script calls back with {match.group(1)!r}, which Typer's "
        f"runtime handler rejects — every TAB would print an error"
    )


def test_zsh_install_prefers_a_writable_user_dir_already_on_fpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rule: land where the shell already looks, in the shell's own order."""
    home = tmp_path / "home"
    first = home / ".local/share/zsh/site-functions"
    second = home / ".zsh/completions"
    for directory in (first, second):
        directory.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("FPATH", f"/usr/share/zsh/site-functions:{first}:{second}")

    assert CompletionScript.zsh_target() == first  # fpath order, not a ranked name list


def test_zsh_install_skips_directories_outside_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A package manager owns /usr/share; Grove must not drop a file there."""
    home = tmp_path / "home"
    home.mkdir()
    system = tmp_path / "usr/share/zsh/site-functions"
    system.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("FPATH", str(system))

    directory = CompletionScript.zsh_target()

    assert system not in directory.parents
    assert directory == home / ".local/share/zsh/site-functions"


def test_install_writes_the_script_and_reports_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    target = home / ".zsh/completions"
    target.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("FPATH", str(target))

    path = CompletionScript.install("zsh")

    assert path == target / "_grove"
    assert "#compdef grove" in path.read_text(encoding="utf-8")


def test_install_never_touches_zshrc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The rule that separates this from Typer's own installer.

    Typer appends an `fpath+=…; compinit` line to ~/.zshrc, which under
    oh-my-zsh lands after oh-my-zsh has already run compinit. Grove prints that
    line instead of writing it, so the file must come back byte-identical.
    """
    home = tmp_path / "home"
    target = home / ".zsh/completions"
    target.mkdir(parents=True)
    zshrc = home / ".zshrc"
    zshrc.write_text("# my careful setup\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("FPATH", str(target))

    CompletionScript.install("zsh")

    assert zshrc.read_text(encoding="utf-8") == "# my careful setup\n"


def _install_with_probe(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    completes: bool | None,
) -> str:
    """Run `completions install` with the zsh probe answering ``completes``.

    The probe shells out to the developer's OWN zsh, so leaving it live makes
    these assertions depend on whether that machine happens to have a Grove
    completion installed — which is how this test first failed. Stub it and the
    subject becomes what it should be: the message the command prints for each
    outcome.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("FPATH", "/usr/share/zsh/site-functions")
    monkeypatch.setattr(CompletionScript, "zsh_completes", staticmethod(lambda: completes))

    result = runner.invoke(app, ["completions", "install", "--shell", "zsh"])
    assert result.exit_code == 0, result.output
    return result.output


def test_install_reports_when_zsh_does_not_resolve_it(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An install the shell cannot see must SAY so — it looks like it worked.

    Both causes are named because they are indistinguishable from here: a stale
    completion cache and a directory `compinit` never scanned produce the same
    observation.
    """
    output = _install_with_probe(runner, monkeypatch, tmp_path, completes=False)

    assert "does not complete `grove` yet" in output
    assert "rm -f ~/.zcompdump*" in output
    assert "fpath=(" in output


def test_install_confirms_when_zsh_completes_grove(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The claim is "zsh completes grove" and deliberately not "from this file".

    `zsh_completes` cannot know which file wins — resolving that needs the fpath
    as it was at `compinit` time, which no longer exists once the rc has run — so
    the message must not imply it. Asserting the narrower wording here is what
    keeps a future "improvement" from over-claiming again.
    """
    output = _install_with_probe(runner, monkeypatch, tmp_path, completes=True)

    assert "Verified: zsh completes `grove`" in output
    assert "this file" not in output


def test_install_says_so_when_zsh_cannot_be_run(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No zsh on the box is not a failed install — report the gap, not a verdict."""
    output = _install_with_probe(runner, monkeypatch, tmp_path, completes=None)

    assert "could not run zsh to verify" in output


def test_bash_and_fish_install_into_their_conventional_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One rule — "a directory the shell already searches" — three shells."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    bash = CompletionScript.install("bash")
    fish = CompletionScript.install("fish")

    assert bash == home / ".local/share/bash-completion/completions/grove"
    assert fish == home / ".config/fish/completions/grove.fish"


def test_an_unsupported_shell_is_refused_by_name(runner: CliRunner) -> None:
    result = runner.invoke(app, ["completions", "show", "--shell", "nushell"])

    assert result.exit_code == 1
    assert "nushell" in result.output
