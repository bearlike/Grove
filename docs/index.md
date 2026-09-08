---
title: Grove
---

<div class="ms-hero">
  <p class="ms-hero__eyebrow">Grove · workspaces for AI coding agents</p>
  <h1 class="ms-hero__title">Run a forest of coding agents</h1>
  <p class="ms-hero__lede">
    Twenty agents, twenty isolated workspaces, and you never lose your place. Grove does not touch
    your agent. It owns everything around it, and every workspace reports to the ticket you filed.
  </p>
  <div class="ms-cta-row">
    <a class="ms-btn ms-btn--primary" href="getting-started/">
      <iconify-icon icon="lucide:download" width="18" height="18" aria-hidden="true"></iconify-icon>
      Install &amp; first workspace
    </a>
    <a class="ms-btn ms-btn--secondary" href="use-tui/">
      <iconify-icon icon="lucide:compass" width="18" height="18" aria-hidden="true"></iconify-icon>
      Take the tour
    </a>
    <a class="ms-btn ms-btn--ghost" href="https://github.com/bearlike/Grove">
      <iconify-icon icon="lucide:github" width="18" height="18" aria-hidden="true"></iconify-icon>
      Source on GitHub
    </a>
  </div>
</div>

<div class="ms-pills" aria-label="Your agentic development environment">
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:bot" width="14" height="14" aria-hidden="true"></iconify-icon>
    Bring your agents
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:users" width="14" height="14" aria-hidden="true"></iconify-icon>
    Shared team configuration
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:container" width="14" height="14" aria-hidden="true"></iconify-icon>
    Isolated agent workspaces
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:ticket" width="14" height="14" aria-hidden="true"></iconify-icon>
    Issue driven workflows
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:pencil-ruler" width="14" height="14" aria-hidden="true"></iconify-icon>
    Shared diagrams and annotations
  </span>
  <span class="ms-pill">
    <iconify-icon class="ms-pill__icon" icon="lucide:activity" width="14" height="14" aria-hidden="true"></iconify-icon>
    Agent activity monitoring
  </span>
</div>

<div class="swiper ms-shots">
  <div class="swiper-wrapper">
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/webapp-composer.png" alt="Grove's web dashboard on its launch page: a composer for describing a task, with pills for the project, working directory, agent, runtime and branch">
        <figcaption>Start here. Describe the task, pick the agent and runtime, and a workspace opens around it.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/demos/cli-completion.gif" alt="Grove CLI tab completion stepping through commands, configured agents, model names, workspace ids, branches and recorded sessions">
        <figcaption>CLI. Every command and live value completes in your shell, from agent models to workspace ids.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/tui-list.png" alt="Grove's terminal UI showing four workspaces in mixed states beside a live peek rail">
        <figcaption>Terminal fleet. Create, attach, pause and steer beside a live peek at the selected agent.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/tui-dashboard.png" alt="Grove's terminal activity dashboard: one tile per workspace, grouped by project, with the busiest tiles expanded to show a live terminal tail">
        <figcaption>Activity dashboard. Every project on one wall, sized by which workspace needs you.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/issue-ops-sticky-comment.png" alt="Grove's status comment on a tracker issue: a table of phase, checklist, branch and commit above a six step progress diagram running from Scoping to Done">
        <figcaption>Issue ops. The phase, checklist and pull request stay current on the ticket that started the work.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/demos/grove-demo-devcontainer.gif" alt="Grove creating a workspace with the Container runtime, then running Claude Code inside its devcontainer under visible CPU and memory limits">
        <figcaption>Containerized agents. One complete stack per workspace, built from the repository's own devcontainer.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/webapp-workspace.png" alt="A Grove workspace page with an agent transcript in the center and the work panel open beside it to show branch, model, runtime and ticket details">
        <figcaption>Workspace detail. Read the transcript and inspect the branch, runtime, tickets and diff without attaching.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/webapp-diagram.png" alt="A Grove workspace's Diagram tab filling the work pane, with an architecture diagram the agent drew rendered in the embedded draw.io editor and marked Saved">
        <figcaption>Diagrams. An agent draws a mockup or an architecture spec in draw.io, Grove validates the XML, you approve the picture.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/webapp-annotate.png" alt="A Grove workspace page with the agent transcript on the left and the image annotation pane open beside it, holding an aerial photo of a crosswalk with every pedestrian boxed and labelled in the marker.js editor">
        <figcaption>Annotation. Paste a screenshot, box what matters, and the agent is handed the marked-up copy.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/webapp-sessions.png" alt="Grove's host-wide session catalog: searchable agent sessions with project, model, turn count, recency and context usage columns">
        <figcaption>Session catalog. Search every recorded agent session on the host, including work Grove did not launch.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/webapp-usage.png" alt="Grove's usage audit showing subscription windows, measured totals, a daily token heatmap and weekly model mix">
        <figcaption>Usage. Subscription windows, burn rate and a year of tokens, cost and latency from local transcripts.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/grove-mcp-tools.png" alt="Claude Code listing Grove's MCP tools for creating, listing, inspecting, pausing and steering workspaces">
        <figcaption>MCP. Claude Code, Codex and other orchestrators can create workspaces and steer the fleet themselves.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/telemetry-trace.png" alt="One Claude Code turn as a Langfuse trace: an agent-turn root with model generations and a span for each tool call">
        <figcaption>Telemetry. Every turn can become one trace with model generations, tool calls, latency and cost.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="img/screenshots/webapp-home.png" alt="Grove's web fleet dashboard: an attention-sorted grid of workspace cards beside a session rail listing every workspace by recency">
        <figcaption>Web fleet. Reach every workspace from any device, sorted by the one that needs you.</figcaption>
      </figure>
    </div>
  </div>
  <div class="swiper-pagination"></div>
  <div class="swiper-button-prev"></div>
  <div class="swiper-button-next"></div>
</div>

## What is Grove? { .ms-h2-icon data-icon="target" }

- **Writing the code stopped being the slow part.** Deciding what to build did. More agents can build in parallel but shared checkouts cause conflicts and laptops limit capacity. Grove adds IDE tools around those agents without replacing them. It manages their workspaces and runtimes and gives you tools to guide their work.
- **Give each task its own workspace.** Each agent gets its own worktree and branch in a separate window. Agents report progress phases for the workspace and each attached ticket independently. You can see what is being planned, built or verified across the fleet.
- **Choose how each agent runs.** Run agents on your host with the environment they normally inherit. Or use a [container](features-containers.md) defined by your repository's `.devcontainer/`. It provides separate services and ports with resource limits. Use that isolation for runs with agent permission checks disabled.
- **Choose where the work runs.** Tests, builds and scripts compete for CPU and memory across concurrent workspaces. Move those workloads off your laptop when it slows down. Reuse the container configuration on a shared team machine or serverless cloud infrastructure.
- **Let the tracker start the work.** Assign an issue and Grove starts a workspace for it. Progress returns to [the same ticket](issue-ops.md). Follow the task where you defined it.
- **Show it instead of describing it.** Mark up a [screenshot](features-attachments.md) or collaborate with the agent on a [diagram](features-diagrams.md). Approve the diagram or mockup and attach it to a ticket. Then coordinate your fleet around it.

## Choose your surface { .ms-h2-icon data-icon="route" }

Every workspace is reachable from all four.

<div class="ms-grid ms-grid--4">
<a class="ms-card" href="use-tui/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:terminal" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Terminal</span>
  <span class="ms-card__body">Manage multiple coding agents without leaving your terminal. Switch between workspaces and inspect live output to see which agent needs you.</span>
</a>
<a class="ms-card" href="use-webapp/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:layout-panel-top" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Web dashboard</span>
  <span class="ms-card__body">Manage your agents from any device and track their transcripts and task progress. Review diagrams and annotate screenshots to show what needs changing.</span>
</a>
<a class="ms-card" href="issue-ops/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:ticket" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Your tracker</span>
  <span class="ms-card__body">Delegate work from Linear, GitHub or Gitea without opening another tool. Assign an issue to start a workspace and follow its phase and checklist on the same ticket.</span>
</a>
<a class="ms-card" href="use-mcp/">
  <span class="ms-card__icon">
    <iconify-icon icon="lucide:plug" width="20" height="20" aria-hidden="true"></iconify-icon>
  </span>
  <span class="ms-card__title">Another agent</span>
  <span class="ms-card__body">Give an orchestrating agent the tools to delegate across isolated workspaces. Through MCP it can launch agents, inspect their progress and send follow ups while you retain oversight.</span>
</a>
</div>

## What you get { .ms-h2-icon data-icon="grid" }

Built for the founder or lead who wants a whole team running agents the same way, and able to see what every one of them did.

<div class="ms-grid ms-grid--3">
  <a class="ms-card" href="features-cascade/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:layers" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Shared configuration</span>
    <span class="ms-card__body">A committed <code>.grove/config.json</code> carries the agents, models, setup and container policy every workspace starts from. Each engineer overrides locally, and a workspace picks its own model without touching the shared file.</span>
  </a>
  <a class="ms-card" href="features-telemetry/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:waypoints" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Agent monitoring</span>
    <span class="ms-card__body">Every turn becomes one trace with its model calls, tool calls, latency and cost, in Langfuse or any OTLP backend. Usage, subscription windows and a year of spend come from transcripts on your own disk.</span>
  </a>
  <a class="ms-card" href="features-containers/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:box" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Isolated containers</span>
    <span class="ms-card__body">Each agent runs in the repository's own devcontainer with resource and egress ceilings, so permissions off is safe and twenty agents cannot saturate one machine. Sessions survive restarts and re-adopt on their own.</span>
  </a>
  <a class="ms-card" href="features-diagrams/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:pencil-ruler" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Shared diagrams</span>
    <span class="ms-card__body">A UI mockup or an architecture spec lives as one draw.io file both of you edit, with revision checks. Grove validates the XML and you approve the picture before code exists.</span>
  </a>
  <a class="ms-card" href="features-attachments/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:image-plus" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Image annotation</span>
    <span class="ms-card__body">Drop a screenshot or a file into either composer, draw on the image, and the agent is handed a path inside its own worktree. The day to day handoffs your agent harness leaves out.</span>
  </a>
  <a class="ms-card" href="use-mcp/">
    <span class="ms-card__icon">
      <iconify-icon icon="lucide:plug" width="20" height="20" aria-hidden="true"></iconify-icon>
    </span>
    <span class="ms-card__title">Remote access</span>
    <span class="ms-card__body">The same fleet from a terminal, a browser on any device, a tracker comment, or another agent over MCP. One daemon on loopback, a pairing handshake, and push notifications when an agent needs you.</span>
  </a>
</div>

<span id="install"></span>
<span id="explore-the-docs"></span>

## From first workspace to a whole fleet { .ms-h2-icon data-icon="route" }

Start with one agent, share your setup with the team and choose how you manage the work.

<div class="ms-lifecycle">
  <div class="ms-step">
    <p class="ms-step__title">Install</p>
    <div class="ms-step__links">
      <p class="ms-card__body">Set up Grove and launch your first isolated workspace.</p>
      <ul class="ms-step__links">
        <li><a href="getting-started/">Get Started</a></li>
        <li><a href="getting-started/#prerequisites">Prerequisites</a></li>
        <li><a href="configure-project/">Project setup</a></li>
        <li><a href="troubleshooting/">Troubleshooting</a></li>
      </ul>
    </div>
  </div>
  <div class="ms-step">
    <p class="ms-step__title">Configure</p>
    <div class="ms-step__links">
      <p class="ms-card__body">Choose your agents and share workspace defaults across the team.</p>
      <ul class="ms-step__links">
        <li><a href="configure-agents/">Agents and models</a></li>
        <li><a href="configure-init-scripts/">Init scripts</a></li>
        <li><a href="issue-ops/">Issue ops</a></li>
        <li><a href="configure-reference/">Configuration reference</a></li>
      </ul>
    </div>
  </div>
  <div class="ms-step">
    <p class="ms-step__title">Use</p>
    <div class="ms-step__links">
      <p class="ms-card__body">Run your fleet and follow its work from the surface that suits you.</p>
      <ul class="ms-step__links">
        <li><a href="use-tui/">Terminal tour</a></li>
        <li><a href="use-cli/">CLI commands</a></li>
        <li><a href="use-webapp/">Web dashboard</a></li>
        <li><a href="use-mcp/">MCP server</a></li>
      </ul>
    </div>
  </div>
  <div class="ms-step">
    <p class="ms-step__title">Capabilities</p>
    <div class="ms-step__links">
      <p class="ms-card__body">Contain each agent's environment and make its work easier to review.</p>
      <ul class="ms-step__links">
        <li><a href="features-catalog/">Capability catalog</a></li>
        <li><a href="features-containers/">Containerized agents</a></li>
        <li><a href="features-diagrams/">Diagram collaboration</a></li>
        <li><a href="features-attachments/">Attachments and annotation</a></li>
      </ul>
    </div>
  </div>
  <div class="ms-step">
    <p class="ms-step__title">Developers</p>
    <div class="ms-step__links">
      <p class="ms-card__body">Build on Grove's public API or contribute to the engine and its clients.</p>
      <ul class="ms-step__links">
        <li><a href="develop-architecture/">Architecture</a></li>
        <li><a href="develop-public-api/">Public API</a></li>
        <li><a href="develop-principles/">Engineering principles</a></li>
        <li><a href="develop-contributing/">Contributing</a></li>
      </ul>
    </div>
  </div>
</div>
