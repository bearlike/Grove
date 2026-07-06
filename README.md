<p align="center">
  <img src="docs/logos/grove-logo.png" alt="Grove" width="84" />
</p>

<h1 align="center">Grove</h1>
<p align="center"><em>The terminal workspace manager for AI coding agents. Spin up a forest of isolated agent workspaces. Reach any of them asynchronously from your terminal, your browser, or another agent.</em></p>

<p align="center">
  <a href="https://github.com/bearlike/Grove/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/bearlike/Grove/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/bearlike/Grove/actions/workflows/docs.yml"><img alt="Docs" src="https://github.com/bearlike/Grove/actions/workflows/docs.yml/badge.svg"></a>
  <a href="https://www.python.org/"><img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
</p>

<p align="center">
  <img src="docs/img/mockups/hero-laptop.gif" alt="Grove on a MacBook, swapping between the terminal UI and the web dashboard" height="316" />
  &nbsp;&nbsp;
  <img src="docs/img/mockups/webapp-phone-mockup.png" alt="Grove web dashboard on a phone, showing the workspace surface" height="316" />
</p>
<p align="center">
  <sub>Grove in the terminal and the browser, and on your phone. One agent, one worktree, one window.</sub>
</p>

## 🌳 Overview

Grove runs several AI coding agents at once, each in its own isolated **workspace**: a dedicated git worktree on its own branch, paired with a tmux session and a window. The rule is one agent, one worktree, one window. Agents are productive in parallel but chaotic in the same folder, where they overwrite each other's files and collide on the same branch. Grove gives each one its own bench.

Every workspace is reachable asynchronously, from the terminal TUI, the CLI, the web IDE on any device, or another agent over [MCP](https://bearlike.github.io/Grove/latest/use-mcp/). The same isolated-workspace primitive backs human and agent orchestration alike. Through all of it your git history stays yours: Grove never commits, never pushes, and never touches a remote branch.

## ✨ Features

<table>
<tr>
<td width="50%" valign="middle">

### A web IDE for your agents

Reach the whole fleet over your network. A session rail lists every agent thread by recency; the landing is a composer, so a prompt spins up a workspace. Pair a device once and the daemon stays loopback. Space-black, dark-first.

[Docs →](https://bearlike.github.io/Grove/latest/use-webapp/)

</td>
<td width="50%">
  <a href="https://bearlike.github.io/Grove/latest/use-webapp/"><img src="docs/img/screenshots/webapp-home.png" alt="Grove's web IDE: a session rail listing every agent thread on the left, a composer at the center to start a workspace, on a dark space-black canvas" width="100%" /></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Transcript, terminal, and diff in one shell

Open a session and land in a three-zone shell: the rail, the agent's live transcript, and a tabbed work panel with terminal, diff, and info. Steer the agent with a follow-up while it works, answer its structured questions inline, and watch the diff grow.

[Docs →](https://bearlike.github.io/Grove/latest/use-webapp/#the-workspace-ide-shell)

</td>
<td width="50%">
  <a href="https://bearlike.github.io/Grove/latest/use-webapp/#the-workspace-ide-shell"><img src="docs/img/screenshots/webapp-workspace.png" alt="A Grove session page: the session rail, the agent's live transcript with an inline multiple-choice question, and a tabbed work panel showing the terminal and diff" width="100%" /></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Drive Grove from any agent

Grove ships an MCP server. Claude Code, Codex, OpenCode, and other orchestrators use its tools to create workspaces, dispatch tasks, send follow-ups, and steer the fleet. Your agents manage work at the scale of a whole project.

[Docs →](https://bearlike.github.io/Grove/latest/use-mcp/)

</td>
<td width="50%">
  <a href="https://bearlike.github.io/Grove/latest/use-mcp/"><img src="docs/img/screenshots/grove-mcp-tools.png" alt="Claude Code listing Grove's MCP tools: create, list, peek, pause, and steer workspaces" width="100%" /></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Terminal UI

Grove is a terminal program first. Run `grove` in a repo to see only its workspaces. Create, attach, pause, and kill, each one keypress. A peek rail mirrors the selected agent's pane live, next to its git position.

[Docs →](https://bearlike.github.io/Grove/latest/use-tui/)

</td>
<td width="50%">
  <a href="https://bearlike.github.io/Grove/latest/use-tui/"><img src="docs/img/screenshots/tui-list.png" alt="The Grove TUI: a project-scoped workspace list with a live agent peek rail showing the summary, recent commits, and transcript" width="100%" /></a>
</td>
</tr>
</table>

**Also in the box:**

- **[Branch-aware lifecycle](https://bearlike.github.io/Grove/latest/features-workspace-lifecycle/).** Create, pause, resume, respawn, and kill. Pause keeps the branch and drops the worktree. Kill deletes only branches Grove created, never remotes.
- **[Configuration cascade](https://bearlike.github.io/Grove/latest/features-cascade/).** A committed `.grove/config.json` sets the team baseline. Six layers let each developer override locally without touching it.
- **[Per-agent model selection](https://bearlike.github.io/Grove/latest/use-webapp/).** Each agent exposes its own model catalog. Pick the model per workspace: a cheap one for scaffolding, a strong one for the hard refactor, side by side.
- **[Session recovery](https://bearlike.github.io/Grove/latest/features-workspace-lifecycle/).** Grove pane-verifies each agent session and re-adopts it across daemon restarts and a different user attaching. A stale pointer remaps to the recovered session in a click.
- **[Ticket providers](https://bearlike.github.io/Grove/latest/features-ticket-providers/).** Branch-aware Gitea, GitHub, and Linear context, surfaced next to the workspace.
- **[Push notifications](https://bearlike.github.io/Grove/latest/features-notifications/).** Get pinged when an agent finishes a turn or needs you.

## 🚀 Get started

Grove needs `git` and `tmux`, and installs to your PATH as `grove` straight from the repo. With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install "grove[daemon] @ git+https://github.com/bearlike/Grove"

cd path/to/your/repo
grove config init        # scaffold .grove/config.json
grove                    # launch the TUI
uv tool upgrade grove    # update later
```

No uv? Use `pipx install "grove[daemon] @ git+https://github.com/bearlike/Grove"` or plain `pip install --user`. See [Get Started](https://bearlike.github.io/Grove/latest/getting-started/) for prerequisites and every install path.

> [!IMPORTANT]
> Always install with the full `@ git+...` form above. The name `grove` on PyPI belongs to an unrelated package, so a bare `uv tool install grove` installs the wrong product.

<details>
<summary><b>🤖 Let an AI agent configure Grove for you</b></summary>

<br>

Configuration has a few layers and many knobs, so you do not have to write it by hand. Hand the prompt below to Claude Code, Codex, or any coding agent; it reads Grove's config skill and sets things up with you, verifying every field against your installed version.

```text
Read https://raw.githubusercontent.com/bearlike/Grove/current/.claude/skills/configuring-grove/SKILL.md. It is the skill for configuring Grove, a terminal workspace manager for AI coding agents. Help me write my Grove user and project config, and verify every field against my installed version with `grove config schema --stdout`.
```

</details>

## 📚 Documentation

Full documentation lives at **<https://bearlike.github.io/Grove/latest/>**.

| Section | Covers |
| --- | --- |
| [Get Started](https://bearlike.github.io/Grove/latest/getting-started/) | Install, prerequisites, first run, verify. |
| [Configure](https://bearlike.github.io/Grove/latest/configure-project/) | Project setup, agents, init scripts, configuration reference. |
| [Use](https://bearlike.github.io/Grove/latest/use-tui/) | TUI tour, CLI, web IDE, authentication, daily workflow. |
| [Capabilities](https://bearlike.github.io/Grove/latest/features-workspace-lifecycle/) | Lifecycle, branch provenance, live activity, status semantics, configuration cascade. |
| [Develop](https://bearlike.github.io/Grove/latest/develop-architecture/) | Architecture, public API, engineering principles, contributing, design system. |
| [Troubleshooting](https://bearlike.github.io/Grove/latest/troubleshooting/) | Symptom → cause → fix for common failures. |

## 🤝 Contributing

Bugs and feature requests on the [issue tracker](https://github.com/bearlike/Grove/issues). For development setup, lint/test commands, and PR conventions, see the [contributing guide](https://bearlike.github.io/Grove/latest/develop-contributing/) and [`CLAUDE.md`](./CLAUDE.md).

## 📄 License

[MIT](./LICENSE) © Krishnakanth Alagiri.
