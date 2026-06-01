---
title: Grove
---

<p class="grove-lede">
A TUI for managing git worktrees, one per coding agent. Each workspace
pairs a worktree with a tmux session running the agent of your choice.
The TUI lists every workspace in the current repository and exposes
create, attach, pause, resume, and kill as one-key actions. An optional
daemon serves the same view to a read-only web dashboard, so you can
glance at your workspaces from a phone or another machine while the
daemon stays loopback-only behind a paired session.
</p>

<div class="ms-cta-row">
  <a class="ms-btn ms-btn--primary" href="getting-started/">Install and first workspace</a>
  <a class="ms-btn ms-btn--ghost" href="https://github.com/bearlike/Grove">Source on GitHub</a>
</div>

<figure class="grove-hero-shot" markdown>
  <span class="grove-hero-shot__frame">
    ![Grove TUI showing four workspaces in mixed states with a live peek rail](img/screenshots/tui-list.png)
  </span>
  <figcaption class="grove-hero-shot__caption">
    Grove TUI, workspace list with live peek rail.
  </figcaption>
</figure>

## Status

A workspace lives in one of seven states. Three are persisted intents
that Grove writes to disk. Four are computed at read time from tmux
and the filesystem. The colors below are the same hex values the TUI
uses, so the page and the running app match.

<div class="grove-status-grid" markdown>

<div class="grove-status-chip" data-status="active">
  <span class="grove-status-chip__glyph">●</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Active</p>
    <p class="grove-status-chip__body">Session up. The agent pane produced output within the activity threshold.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="idle">
  <span class="grove-status-chip__glyph">◐</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Idle</p>
    <p class="grove-status-chip__body">Session up. The pane has been quiet past the threshold.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="paused">
  <span class="grove-status-chip__glyph">‖</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Paused</p>
    <p class="grove-status-chip__body">Worktree removed by you; branch retained. <code>R</code> resumes.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="offline">
  <span class="grove-status-chip__glyph">○</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Offline</p>
    <p class="grove-status-chip__body">tmux session vanished externally. <code>o</code> respawns it from the worktree.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="orphaned">
  <span class="grove-status-chip__glyph">⊘</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Orphaned</p>
    <p class="grove-status-chip__body">Worktree directory missing on disk; respawn no longer applies. <code>k</code> only.</p>
  </div>
</div>

<div class="grove-status-chip" data-status="error">
  <span class="grove-status-chip__glyph">✗</span>
  <div class="grove-status-chip__text">
    <p class="grove-status-chip__label">Error</p>
    <p class="grove-status-chip__body">Lifecycle failed mid-flight (init script non-zero, git add failed, etc.).</p>
  </div>
</div>

</div>

The reconciler that promotes intents into views lives at one site,
[`WorkspaceManager._reconcile_status`](https://github.com/bearlike/Grove/blob/current/src/grove/core/manager.py).
[Status semantics](features-status.md) walks the full transition table.

## User guide

| Section | Pages |
|---|---|
| [Get Started](getting-started.md) | install, first run, verify |
| [Configure](configure-project.md) | [project setup](configure-project.md), [agents](configure-agents.md), [init scripts](configure-init-scripts.md), [reference](configure-reference.md), [cascade](features-cascade.md) |
| [Use](use-tui.md) | [TUI tour](use-tui.md), [CLI](use-cli.md), [web dashboard](use-webapp.md), [authentication](use-auth.md), [daily workflow](use-workflow.md) |
| [Capabilities](features-workspace-lifecycle.md) | [lifecycle](features-workspace-lifecycle.md), [branch provenance](features-branch-provenance.md), [peek](features-peek.md), [status](features-status.md) |
| [Troubleshooting](troubleshooting.md) | symptom, cause, fix |

## Developer reference

| Section | Pages |
|---|---|
| [Architecture](develop-architecture.md) | engine and TUI packages, the import-linter boundary, side effects |
| [Public API](develop-public-api.md) | what `from grove.core import X` may reach for |
| [Engineering principles](develop-principles.md) | rules that keep the codebase navigable |
| [Contributing](develop-contributing.md) | setup, lint, test, commit format |
