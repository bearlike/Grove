---
title: Grove
---

<div class="ms-hero">
  <p class="ms-hero__eyebrow">Grove · workspace manager for AI coding agents</p>
  <h1 class="ms-hero__title">Run a forest of coding agents without losing your place</h1>
  <p class="ms-hero__lede">
    Grove does not touch your agent. It owns everything around it. Each agent gets an isolated
    workspace, takes its work off your tracker, and reports back to the same ticket you filed. You stop
    writing the code. You do not stop owning it.
  </p>
  <div class="ms-cta-row">
    <a class="ms-btn ms-btn--primary" href="getting-started/">Install &amp; first workspace</a>
    <a class="ms-btn ms-btn--secondary" href="use-tui/">Take the tour</a>
    <a class="ms-btn ms-btn--ghost" href="https://github.com/bearlike/Grove">Source on GitHub</a>
  </div>
</div>

<div class="ms-pills" aria-label="What makes Grove different">
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:git-branch" width="14" height="14" aria-hidden="true"></iconify-icon>
    One agent, one worktree, one window
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:box" width="14" height="14" aria-hidden="true"></iconify-icon>
    A whole stack per agent, Docker in Docker
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:ticket" width="14" height="14" aria-hidden="true"></iconify-icon>
    Work arrives from your tracker
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:smartphone" width="14" height="14" aria-hidden="true"></iconify-icon>
    Terminal, browser, or another agent
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:check-check" width="14" height="14" aria-hidden="true"></iconify-icon>
    Claude Code and Codex compatible
  </span>
</div>

<video autoplay muted loop playsinline preload="auto" poster="img/posters/terminal-still.png" style="width:100%;max-width:960px;height:auto;display:block;margin:0 auto 1.75rem;">
  <source src="videos/1-grove-terminal.mp4" type="video/mp4" />
</video>

## What is Grove? { .ms-h2-icon data-icon="target" }

Two or three agents in one checkout overwrite each other's files and fight over the same branch. Past
that you are reading terminals to find the one that is stuck.

Grove gives each agent an isolated **workspace**. One git worktree, one branch, one tmux window, and
optionally a container holding a whole stack of its own. Nothing on one bench spills onto the next.

Your tool runs exactly as it does today. Grove passes its flags through untouched and never second
guesses what it does. What Grove manages is the part no coding agent manages for you: the worktree, the
container, the ticket it came from, and the twenty other agents running beside it.

## Choose your surface { .ms-h2-icon data-icon="route" }

Every workspace is reachable from all four. Pick whichever one you are already sitting in.

<div class="ms-grid ms-grid--4">
<a class="ms-card" href="use-tui/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:terminal" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Terminal</span>
  <span class="ms-card__body">For the engineer running the fleet. Create, attach, pause and kill in one keypress each, with a peek rail mirroring the selected agent live.</span>
</a>
<a class="ms-card" href="use-webapp/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:layout-panel-top" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Web console</span>
  <span class="ms-card__body">For when you are away from the desk. The rail lists every thread, the landing is a composer, and a prompt spins up a workspace. Pair a device once.</span>
</a>
<a class="ms-card" href="issue-ops/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:ticket" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Your tracker</span>
  <span class="ms-card__body">For everyone who does not want a terminal. Assign an issue on Linear, GitHub or Gitea and a workspace starts against it, with status reported back in place.</span>
</a>
<a class="ms-card" href="use-mcp/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:plug" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Another agent</span>
  <span class="ms-card__body">For orchestration at project scale. Grove ships an MCP server, so Claude Code, Codex and others create and steer workspaces themselves.</span>
</a>
</div>

<video autoplay muted loop playsinline preload="auto" poster="img/posters/web-still.png" style="width:100%;max-width:960px;height:auto;display:block;margin:0 auto 1.75rem;">
  <source src="videos/2-grove-web.mp4" type="video/mp4" />
</video>

## The ticket you filed is the ticket you check { .ms-h2-icon data-icon="flow" }

A workspace treats its issue as the spec and reports back to the same place. One comment, rewritten as
the work moves, carrying the phase the agent is in, what it has ticked off and the pull request it
opened. Nobody reads code to follow along, and nobody watches a terminal, because Grove pings you when
an agent finishes or gets stuck.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img src="img/screenshots/issue-ops-sticky-comment.png" alt="Grove's status comment on a tracker issue: a table of phase, checklist, branch and commit above a six step progress diagram running from Scoping to Done" width="100%" /></div>
  <figcaption class="ms-shot__body">One comment per ticket, rewritten in place. No second dashboard to check.</figcaption>
</figure>

## What you get { .ms-h2-icon data-icon="grid" }

<div class="ms-grid ms-grid--3">
  <a class="ms-card" href="features-containers/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:box" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">A stack per agent</span>
    <span class="ms-card__body">Docker in Docker, so the agent starts its own database, services and ports. Run it with permissions off and the blast radius is that sandbox.</span>
  </a>
  <a class="ms-card" href="use-webapp/#the-workspace-ide-shell">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:columns-3" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Transcript, terminal, diff</span>
    <span class="ms-card__body">A three zone shell. Steer the agent while it works, answer its structured questions inline, and watch the diff grow.</span>
  </a>
  <a class="ms-card" href="use-webapp/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:sliders-horizontal" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">A model per workspace</span>
    <span class="ms-card__body">Each agent exposes its own catalog. A cheap model for scaffolding and a strong one for the hard refactor, side by side.</span>
  </a>
  <a class="ms-card" href="features-cascade/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:layers" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Principles, versioned</span>
    <span class="ms-card__body">Commit one config and the team's agents, setup and container policy travel with the code. Six layers let anyone override locally without touching it.</span>
  </a>
  <a class="ms-card" href="features-workspace-lifecycle/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:rotate-ccw" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Session recovery</span>
    <span class="ms-card__body">Grove re-adopts each agent session across daemon restarts and a different user attaching. A stale pointer remaps in a click.</span>
  </a>
  <a class="ms-card" href="features-notifications/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:bell" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Push notifications</span>
    <span class="ms-card__body">Get pinged when an agent finishes a turn or needs an answer, so watching is never the plan.</span>
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

## Explore the docs { .ms-h2-icon data-icon="book" }

<div class="ms-grid ms-grid--3">
  <a class="ms-card" href="getting-started/">
    <span class="ms-card__title">Get Started</span>
    <span class="ms-card__body">Install, prerequisites, first run.</span>
  </a>
  <a class="ms-card" href="use-tui/">
    <span class="ms-card__title">Use</span>
    <span class="ms-card__body">TUI tour, CLI, web console, authentication, daily workflow.</span>
  </a>
  <a class="ms-card" href="configure-project/">
    <span class="ms-card__title">Configure</span>
    <span class="ms-card__body">Project setup, agents, init scripts, the cascade.</span>
  </a>
  <a class="ms-card" href="features-containers/">
    <span class="ms-card__title">Capabilities</span>
    <span class="ms-card__body">Containers, lifecycle, issue ops, status axes, notifications.</span>
  </a>
  <a class="ms-card" href="develop-architecture/">
    <span class="ms-card__title">Developer reference</span>
    <span class="ms-card__body">Architecture, public API, principles, contributing.</span>
  </a>
</div>
