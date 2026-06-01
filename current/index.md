---
title: Grove
---

<div class="ms-hero">
  <p class="ms-hero__eyebrow">Grove · terminal workspace manager for AI coding agents</p>
  <h1 class="ms-hero__title">Run a forest of coding agents without losing your place</h1>
  <p class="ms-hero__lede">
    Run several coding agents at once. Each gets its own git worktree and tmux session, scoped to one
    repo. They share nothing, so they never collide. Spin a session up, watch the agent work, then tear
    it down. Every step is a single keypress. Step away from your desk, and a read-only web dashboard
    keeps every agent in view from your phone.
  </p>
  <div class="ms-cta-row">
    <a class="ms-btn ms-btn--primary" href="getting-started/">Install &amp; first workspace</a>
    <a class="ms-btn ms-btn--secondary" href="use-tui/">Take the tour</a>
    <a class="ms-btn ms-btn--ghost" href="https://github.com/bearlike/Grove">Source on GitHub</a>
  </div>
</div>

<figure class="grove-hero-shot" markdown>
  <span class="grove-hero-shot__frame">
    ![Grove TUI showing four workspaces in mixed states with a live peek rail](img/screenshots/tui-list.png)
  </span>
  <figcaption class="grove-hero-shot__caption">
    Project-scoped workspaces on the left. On the right, a live rail mirrors the selected agent's terminal and its git position.
  </figcaption>
</figure>

## What is Grove? { .ms-h2-icon data-icon="target" }

You get the most out of coding agents when you run several at once. One drafts a feature. Another
chases a flaky test. A third rewrites the docs. Run them in the same folder, though, and they fight
over the same files and the same branch.

Grove gives each agent a space of its own. Think of a workspace as a private workbench. The agent gets
its own branch, its own copy of the code, and its own terminal to work at. Nothing on one bench spills
onto the next. The rule is simple: one agent, one worktree, one window.

Launch `grove` inside a repository and you see only that repository's workspaces. From there you
create, attach, pause, resume, and kill them, and each action is a single keypress. A rail on the right
mirrors whatever the selected agent is doing.

Grove tends the worktrees and the sessions. Your git history stays yours. Grove never commits, never
pushes, and never touches a remote branch.

## What you get { .ms-h2-icon data-icon="grid" }

<div class="ms-grid ms-grid--4">
  <div class="ms-card">
    <span class="ms-card__title">Isolated workspaces</span>
    <p class="ms-card__body">One worktree and one tmux session per agent. Each works at its own bench, so they never touch each other's files.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">One-key lifecycle</span>
    <p class="ms-card__body">Create, attach, pause, resume, and kill. Each is one keypress. Pause frees the worktree but keeps the branch, so you can return later.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Live activity peek</span>
    <p class="ms-card__body">A side rail mirrors each agent's terminal. It also shows where the branch stands, with ahead, behind, and dirty counts.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Bring your own agent</span>
    <p class="ms-card__body">Claude Code, Aider, Cursor, or a plain shell. Anything on your <code>$PATH</code> runs in its own window.</p>
  </div>
</div>

## Install { .ms-h2-icon data-icon="plug" }

Grove needs `git` and `tmux`.

```bash
uvx grove                    # run without installing
uv tool install grove        # or install on your $PATH

cd path/to/your/repo
grove config init            # scaffold .grove/config.json
grove                        # launch the TUI
```

See [Get Started](getting-started.md) for prerequisites and the bootstrap installer.

## Built for teams { .ms-h2-icon data-icon="flow" }

Grove gives you the mechanism and leaves the policy to you. Commit a `.grove/config.json` to the repo
and you set the shared baseline. It pins the agent registry, the worktree layout, and the init script
that prepares every new workspace. The configuration cascade then lets each developer layer personal
tweaks on top, without touching the shared file. It works like a team `.editorconfig`: one agreed
default, with room for personal overrides.

Teams put this to work in familiar ways. They run an agent per feature branch. They pit Claude against
Aider on the same task and compare the results. They pause a long refactor and pick it up days later.

When others need to watch the fleet, point the read-only web dashboard at the daemon. It shows the same
status and live output as the TUI, in any browser or on a phone. Lifecycle control stays in the
terminal, where it belongs.

<figure class="grove-shot" markdown>
  <span class="grove-shot__frame">
    ![Grove read-only web dashboard showing a workspace detail page with summary and live agent panels](img/screenshots/webapp-workspace-detail.png)
  </span>
  <p class="grove-shot__body">The read-only <a href="use-webapp/">web dashboard</a>. Glance at any workspace's status and live agent output from any device on your network.</p>
</figure>

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
    <span class="ms-card__body">Lifecycle, branch provenance, peek, status.</span>
  </a>
  <a class="ms-card" href="develop-architecture/">
    <span class="ms-card__title">Developer reference</span>
    <span class="ms-card__body">Architecture, public API, principles, contributing.</span>
  </a>
</div>
