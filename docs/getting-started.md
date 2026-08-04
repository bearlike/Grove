# Get Started

## From install to first agent

Grove pairs one git worktree with one tmux session per agent, scoped to
the repository it launches from.

## Prerequisites

| Platform | Requirement |
|---|---|
| Linux   | `git` ≥ 2.30, `tmux` ≥ 3.0 |
| macOS   | `git` (Xcode CLT), `tmux` (`brew install tmux`) |
| Windows | WSL2 with the above. Windows-native runs only the non-tmux subcommands (`grove version`, `grove config show`, `grove debug`). The TUI needs WSL. |

## Install

Grove installs to your user bin (`~/.local/bin`) as `grove`, straight
from the repo. The `[daemon]` extra adds the web dashboard backend,
drop it for TUI-only.

!!! warning "Install from the repo, never by bare name"
    `grove` on PyPI is an unrelated log-collection framework. A bare
    `uv tool install grove` (or `pipx`, `pip`) installs that instead,
    failing with `Failed to initialise configuration handler`. Use the
    full `grove[daemon] @ git+...` form below. See
    [Troubleshooting](troubleshooting.md) if this happened.

=== "uv (recommended)"

    [uv](https://docs.astral.sh/uv/) isolates Grove and links `grove`
    onto your `$PATH`.

    ```bash
    uv tool install "grove[daemon] @ git+https://github.com/bearlike/Grove"
    uv tool upgrade grove      # update on demand
    uv tool uninstall grove    # remove
    ```

    No uv yet? `curl -LsSf https://astral.sh/uv/install.sh | sh`.

=== "pipx"

    No uv required:

    ```bash
    pipx install "grove[daemon] @ git+https://github.com/bearlike/Grove"
    pipx upgrade grove
    ```

=== "pip"

    Only Python and pip required:

    ```bash
    pip install --user "grove[daemon] @ git+https://github.com/bearlike/Grove"
    ```

    Re-run with `--upgrade` to update. On an externally managed Python,
    add `--break-system-packages`, or use pipx instead.

## Configure it

Grove runs on defaults you can tune later, from two files that layer
together. The **project config** at `<repo>/.grove/config.json`
(scaffolded by `grove config init`) is committed and shared, pinning the
agent roster, worktree layout, and workspace init script. The **user
config** at `${user_config_dir}/grove/config.json` (`~/.config/grove/config.json`
on Linux) holds your personal defaults across every repo. The project
layer wins on a shared option.

!!! tip "Let an agent configure it for you"
    Hand this prompt to Claude Code, Codex, or any coding agent to
    configure it with you.

    ```text
    Read https://raw.githubusercontent.com/bearlike/Grove/current/.claude/skills/configuring-grove/SKILL.md. It is the skill for configuring Grove, a terminal workspace manager for AI coding agents. Help me write my Grove user and project config, and verify every field against my installed version with `grove config schema --stdout`.
    ```

!!! note "Or write it yourself"
    [Project setup](configure-project.md), [Agents](configure-agents.md),
    and [Init scripts](configure-init-scripts.md) cover each by hand.
    Every field is in the [Configuration reference](configure-reference.md),
    and the [Configuration cascade](features-cascade.md) explains the six
    layers.

---

## First run

```bash
cd path/to/your/git/repo
grove config init      # writes .grove/config.json with sensible defaults
grove                  # launch the TUI
```

A fresh repo lands on the empty state, prompting a first workspace.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-empty.svg" alt="Grove TUI empty state" /></div>
  <figcaption class="ms-shot__body">Empty state. Keys available are listed in the footer.</figcaption>
</figure>

Press ++n++, pick an agent, choose a branch source, and type a title.
Grove resolves the branch, creates the worktree, runs the init script if
configured, spawns a tmux session with `agent` and `shell` windows, and
sends in the agent's command.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/tui-create-modal.svg" alt="Create workspace modal" /></div>
  <figcaption class="ms-shot__body">Create modal. Picking a branch source activates only that variant's inputs.</figcaption>
</figure>

Press ++enter++ (or ++a++) to attach, `Ctrl-B d` to detach. The workspace
keeps running, and the activity rail flips ACTIVE/IDLE on its own.

## Verify

```bash
grove version          # prints the installed version
grove debug            # prints the resolved config + state paths
grove ls               # JSON list of this repo's workspaces
```

Set `GROVE_DEBUG=1` for verbose loguru output on stderr, with one
`initialized <path>` line per directory created on first run.

## Next steps

- [TUI tour](use-tui.md), every screen and keybinding.
- [Project setup](configure-project.md), the per-repo `.grove/config.json`.
- [Custom agents](configure-agents.md), wiring Aider, Cursor, or any command.
- [Daily workflow](use-workflow.md), create, attach, pause, resume, kill.
- [Agent activity and sessions](features-activity.md), the fleet on one wall, replayed.
- [Web dashboard](use-webapp.md), the fleet in the browser or your phone.
- [MCP server](use-mcp.md), for MCP agents (needs `grove[mcp]` or `grove[all]`, not `grove[daemon]`).
