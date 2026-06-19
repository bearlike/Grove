---
title: Grove
---

<div class="ms-hero">
  <p class="ms-hero__eyebrow">Grove · terminal workspace manager for AI coding agents</p>
  <h1 class="ms-hero__title">Run a forest of coding agents without losing your place</h1>
  <p class="ms-hero__lede">
    Run several coding agents at once. Each gets its own git worktree and tmux session, scoped to one
    repo. They share nothing, so they never collide. Spin a session up, watch the agent work, then tear
    it down. Every step is a single keypress. Each workspace is reachable asynchronously, from the
    terminal, the browser on any device, or another agent over MCP, so you can create, steer, or stop
    any of them from wherever you are.
  </p>
  <div class="ms-cta-row">
    <a class="ms-btn ms-btn--primary" href="getting-started/">Install &amp; first workspace</a>
    <a class="ms-btn ms-btn--secondary" href="use-tui/">Take the tour</a>
    <a class="ms-btn ms-btn--ghost" href="https://github.com/bearlike/Grove">Source on GitHub</a>
  </div>
</div>

<div class="ms-devices">
  <img class="no-border" src="img/mockups/hero-laptop.gif" alt="Grove on a MacBook, swapping between the terminal UI and the web dashboard" />
  <img class="no-border" src="img/mockups/webapp-phone-mockup.png" alt="Grove web dashboard on a phone, showing the composer-first workspace surface" />
  <p class="ms-devices__caption">Grove in your terminal and in the browser. Run agents in parallel, each in its own workspace, and reach any of them asynchronously from anywhere.</p>
</div>

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="img/screenshots/tui-list.png" alt="The Grove TUI: a project-scoped workspace list with a live agent peek rail showing the summary, recent commits, and transcript" /></div>
  <figcaption class="ms-shot__body">The terminal UI. A workspace list on the left, a live agent peek rail on the right. Every agent, one glance.</figcaption>
</figure>

## What is Grove? { .ms-h2-icon data-icon="target" }

Agents are productive in parallel but chaotic in the same folder. They overwrite each other's files,
collide on the same branch, and lose track of where they are. Grove gives each one its own bench.

A workspace is a dedicated git worktree on its own branch, paired with a tmux session and a window.
One agent, one worktree, one window. Nothing on one bench spills onto the next.

Every workspace is reachable asynchronously from your terminal, your browser on any device, or another
agent over MCP. The same isolated-workspace primitive backs human and agent orchestration alike. Grove
tends the worktrees and the sessions. Your git history stays yours. Grove never commits, never pushes,
and never touches a remote branch.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="img/screenshots/webapp-home-grid.png" alt="Grove web dashboard: a composer hero at the top for starting a workspace, the repo-grouped workspace cards below, a scope rail on the left, and the daemon status bar along the bottom" /></div>
  <figcaption class="ms-shot__body">The Grove web dashboard. Workspace cards are grouped by repository, the composer sits at the top, and the daemon status bar runs along the bottom.</figcaption>
</figure>

## What you get { .ms-h2-icon data-icon="grid" }

<div class="ms-grid ms-grid--4">
  <a class="ms-card" href="use-webapp/">
    <span class="ms-card__title">Async access, any device</span>
    <p class="ms-card__body">A composer starts a workspace from a prompt. A live, repo-grouped grid shows every agent. Pair a device once; the daemon stays loopback.</p>
  </a>
  <a class="ms-card" href="use-webapp/#the-workspace-ide-shell">
    <span class="ms-card__title">Transcript and terminal</span>
    <p class="ms-card__body">A split view shows the agent's conversation next to its live terminal. Read the transcript, send a follow-up, and answer structured questions inline.</p>
  </a>
  <a class="ms-card" href="use-mcp/">
    <span class="ms-card__title">Drive Grove from any agent</span>
    <p class="ms-card__body">Grove ships an MCP server. Claude Code, Codex, and other orchestrators use its tools to create workspaces, dispatch tasks, and steer the fleet.</p>
  </a>
  <a class="ms-card" href="use-tui/">
    <span class="ms-card__title">Terminal first</span>
    <p class="ms-card__body">Run <code>grove</code> in a repo to see only its workspaces. Create, attach, pause, and kill, each one keypress. A peek rail mirrors the selected agent's pane live.</p>
  </a>
</div>

## Install { .ms-h2-icon data-icon="plug" }

Grove needs `git` and `tmux`, and installs to your PATH as `grove` straight from the repo. With `uv`:

```bash
uv tool install "grove[daemon] @ git+https://github.com/bearlike/Grove"
uv tool upgrade grove        # update on demand

cd path/to/your/repo
grove config init            # scaffold .grove/config.json
grove                        # launch the TUI
```

No uv? Install with `pipx` or `pip` instead. See [Get Started](getting-started.md) for every install path.

## Built for teams { .ms-h2-icon data-icon="flow" }

Grove gives you the mechanism and leaves the policy to you. Commit a `.grove/config.json` to the repo
and you set the shared baseline. It pins the agent registry, the worktree layout, and the init script
that prepares every new workspace. The configuration cascade then lets each developer layer personal
tweaks on top, without touching the shared file. It works like a team `.editorconfig`: one agreed
default, with room for personal overrides.

Teams put this to work in familiar ways. They run an agent per feature branch. They pit Claude against
Aider on the same task and compare the results. They pause a long refactor and pick it up days later.

When you need the fleet beyond the terminal, point the web dashboard at the daemon for async access from
any device on the network. It shows the same status and live output as the TUI, and you can create a
workspace, steer an agent, and pause or kill it from there too. Pair a device once to grant it access,
and the daemon stays on loopback behind that handshake. Workspaces are not only yours to drive: through
Grove's [MCP server](use-mcp.md) an agent can spawn and steer workspaces of its own, the same isolated
workspace primitive behind both human and agent orchestration.

## Explore the docs { .ms-h2-icon data-icon="book" }

<div class="ms-grid ms-grid--3">
  <a class="ms-card" href="getting-started/">
    <span class="ms-card__title">Get Started</span>
    <span class="ms-card__body">Install, prerequisites, first run.</span>
  </a>
  <a class="ms-card" href="use-tui/">
    <span class="ms-card__title">Use</span>
    <span class="ms-card__body">TUI tour, CLI, web dashboard, authentication, daily workflow.</span>
  </a>
  <a class="ms-card" href="configure-project/">
    <span class="ms-card__title">Configure</span>
    <span class="ms-card__body">Project setup, agents, init scripts, the cascade.</span>
  </a>
  <a class="ms-card" href="features-workspace-lifecycle/">
    <span class="ms-card__title">Capabilities</span>
    <span class="ms-card__body">Lifecycle, branch provenance, agent activity, peek, status.</span>
  </a>
  <a class="ms-card" href="develop-architecture/">
    <span class="ms-card__title">Developer reference</span>
    <span class="ms-card__body">Architecture, public API, principles, contributing.</span>
  </a>
</div>
