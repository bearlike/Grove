import type { OnboardingDemand } from "./onboarding-store";

/**
 * One stop on the tour. `selector` is a CSS selector against a `data-testid`,
 * `data-pill` or `data-slot` that already exists on the page — the tour adds
 * no anchors of its own, so a surface renamed under it fails the source census
 * in `tests/unit/onboarding.test.ts` rather than silently pointing at nothing.
 */
export interface OnboardingStep {
  readonly selector: string;
  readonly title: string;
  readonly body: string;
  /** Which page the anchor lives on; the bridge navigates there before the step shows. */
  readonly route: "landing" | "workspace" | "any";
  /** Asked of the page BEFORE this step is shown — stage a file, write a brief, pick a tab. */
  readonly demand?: OnboardingDemand;
  readonly position?: "top" | "right" | "bottom" | "left" | "center";
}

export const SAMPLE_IMAGE_NAME = "tour-sample-annotated-photo.webp";
export const SAMPLE_IMAGE_URL = "/onboarding/tour-sample-annotated-photo.webp";

/**
 * The landing brief the tour writes. It is the SAME ask the sample workspace's
 * transcript answers from step 13 on — attach the ticket, open the PR, and
 * sketch the flow as a Grove diagram — so the reader sees a request here and
 * its outcome there, rather than two unrelated stories.
 */
export const ISSUE_OPS_PROMPT =
  "Work issue #412: attach it, open a draft PR that closes it, and sketch the request flow as a draw.io diagram with the Grove diagram tools so we can edit it together.";
/** Written into the WORKSPACE composer and left there: sending it is how the reader tries the diagram tools. */
export const DIAGRAM_QUERY =
  "Open a draw.io diagram with the Grove diagram tools and sketch this workspace's architecture as boxes and arrows; keep it open so I can edit it with you.";

/** Every brief the tour writes on the landing page, so the page can tell its own words from the user's. */
export const DEMO_PROMPTS: readonly string[] = [ISSUE_OPS_PROMPT];

/** What the tour needs to know about the fleet before it can build its steps. */
export interface TourContext {
  /** The workspace the workspace-page steps open, or null when the fleet is empty. */
  readonly workspaceId: string | null;
  /** Whether that workspace already has a Diagram tab; decides the diagram step's anchor. */
  readonly hasDiagram: boolean;
}

export const DIAGRAM_TAB_SELECTOR = '[data-testid="work-panel-tab-diagram"]';
const WORK_TABLIST_SELECTOR = '[data-testid="work-panel"] [role="tablist"]';

/**
 * The tour, in reading order: what the page is, how to configure a launch, the
 * two workflows that are not obvious from the chrome (attachments and
 * annotation, issue ops), the rail, then a workspace — transcript, steering,
 * the work panel, and drawing with the agent. Titles are one line and bodies
 * are two sentences at most: a tour is read standing up.
 *
 * Eighteen steps, by budget. What is visible on screen is not a step (search,
 * the New workspace button, Fleet and Usage in the rail); what the tour has to
 * DO to be understood — stage a file, open the annotator, write a query — is.
 */
export function buildSteps(context: TourContext): readonly OnboardingStep[] {
  const landing: OnboardingStep[] = [
    {
      // The whole composer — brand, greeting, bar and shelf — not the greeting
      // alone: the first stop names the page, so the hole is the page's one
      // object.
      selector: '[data-testid="launch-page"] > div',
      route: "landing",
      position: "bottom",
      title: "Welcome to Grove",
      body:
        "Every task gets its own workspace: a git worktree, a terminal and a coding agent, on this host or in a container. This page is where one starts.",
    },
    {
      selector: '[data-testid="launch-input"]',
      route: "landing",
      title: "Write the brief",
      body:
        "Describe the work in plain language. Enter sends; Shift+Enter breaks a line. The expand control beside send gives a long brief a whole dialog.",
    },
    {
      selector: '[data-pill="project"]',
      route: "landing",
      title: "Pick the project",
      body:
        "The repository the workspace is cut from. The directory pill beside it chooses where the agent starts inside it.",
    },
    {
      selector: '[data-pill="agent"]',
      route: "landing",
      title: "Choose the agent",
      body: "Claude Code, Codex or another configured roster entry. Each carries its own model catalog.",
    },
    {
      selector: '[data-pill="branch"]',
      route: "landing",
      title: "Branch and worktree",
      body:
        "A fresh branch off HEAD, an existing branch, or the repo root itself with no worktree at all — the dropdown lists what the repository has.",
    },
    {
      selector: '[data-pill="runtime"]',
      route: "landing",
      title: "Host or container",
      body:
        "Run on this machine, or inside the project's devcontainer. It qualifies every other choice on the shelf: a path in a container is not the same place as on the host.",
    },
    {
      selector: '[data-pill="model"]',
      route: "landing",
      title: "Model",
      body:
        "Override the agent's default model for this workspace, or type a custom id. \"Agent default\" means exactly that — no model flag is sent.",
    },
    {
      selector: '[data-testid="launch-attach"]',
      route: "landing",
      demand: { kind: "sample-image" },
      title: "Attach files",
      body:
        "Images, logs and documents ride along with the brief. Paste a screenshot straight into the editor, or pick files here — a sample image has been staged for you.",
    },
    {
      selector: '[data-testid="annotation-pane"]',
      route: "landing",
      position: "left",
      demand: { kind: "annotate" },
      title: "Annotate an image",
      body:
        "The pencil on any staged image opens it beside the page. Draw arrows, boxes and notes so the agent sees what you mean; saving replaces the file, closing leaves the original.",
    },
    {
      selector: '[data-testid="launch-input"]',
      route: "landing",
      demand: { kind: "prompt", text: ISSUE_OPS_PROMPT },
      title: "Tickets and diagrams, in one ask",
      body:
        "Name a ticket and the agent attaches it, opens the PR that closes it and can file new issues or upload files to them. Ask for a diagram and it opens a draw.io editor you edit together — the sample workspace ahead shows exactly this brief being worked.",
    },
    {
      selector: '[data-testid="rail-project-context"]',
      route: "landing",
      position: "right",
      demand: { kind: "reset" },
      title: "Switch project context",
      body:
        "Scope the rail and the fleet to one repository, or see everything. It changes what you are looking at, not what is running.",
    },
    {
      selector: '[data-testid="fleet-tree"]',
      route: "landing",
      position: "right",
      title: "Your workspaces",
      body:
        "Every live workspace, grouped by project, with its phase and whether the agent needs you. Fleet below is the same list as cards with lifecycle controls; Usage is tokens, time and cost across every session.",
    },
  ];

  const workspace: OnboardingStep[] = context.workspaceId
    ? [
        {
          selector: '[data-testid="transcript"]',
          route: "workspace",
          demand: { kind: "pane", view: "split" },
          title: "The transcript",
          body:
            "This is a sample workspace so every surface has something to show. A workspace opens on the agent's conversation: every turn, tool call and file edit as it happens; older turns load on demand above.",
        },
        {
          selector: '[data-testid="workspace-page"] [data-slot="composer-bar"]',
          route: "workspace",
          position: "top",
          title: "Steer and follow up",
          body:
            "Reply here to steer. Sent while the agent is still working, a message queues as a follow-up and is delivered when the turn ends — the queue card above the composer shows what is waiting.",
        },
        {
          selector: '[data-testid="work-panel-tab-terminal"]',
          route: "workspace",
          demand: { kind: "work-tab", tab: "terminal" },
          title: "The work panel",
          body:
            "Terminal is the agent's live pane; Changes and Files are the diff against the base branch; Controls holds the model switch, attach command, share link, skills and MCP servers.",
        },
        {
          selector: '[data-testid="work-panel-tab-info"]',
          route: "workspace",
          demand: { kind: "work-tab", tab: "info" },
          title: "Task, tickets and lifecycle",
          body:
            "Info reads the agent's own phase report and checklist, the tickets attached to this workspace with each one's state, and the pause, resume, respawn and kill actions.",
        },
        {
          selector: context.hasDiagram ? DIAGRAM_TAB_SELECTOR : WORK_TABLIST_SELECTOR,
          route: "workspace",
          // Left of the tab, over the transcript: the editor the step is about
          // must stay visible, and reactour's default lands the card on it.
          position: "left",
          demand: context.hasDiagram
            ? { kind: "work-tab", tab: "diagram" }
            : { kind: "workspace-prompt", text: DIAGRAM_QUERY },
          title: "Draw with the agent",
          body: context.hasDiagram
            ? "The agent was asked for an architecture sketch and opened this draw.io editor with Grove's diagram tools — the Diagram tab exists only because it did. Drag a box: the agent reads your edit on its next turn. Ask for flows or UI mockups the same way."
            : "A Diagram tab joins this strip when the agent calls Grove's diagram tools — architecture, flows, UI mockups, drawn together in draw.io. A query asking for exactly that is in the composer: send it to try.",
        },
      ]
    : [];

  const closing: OnboardingStep[] = [
    {
      selector: '[data-testid="account-menu"]',
      route: "any",
      position: "right",
      title: "Your account",
      body:
        "Theme, the host-wide session catalog, docs and sign-out. This tour lives here too — take it again whenever you like.",
    },
  ];

  return [...landing, ...workspace, ...closing];
}

/** The full list as the census sees it: every anchor either variant can name. */
export const ONBOARDING_STEPS: readonly OnboardingStep[] = buildSteps({
  workspaceId: "census",
  hasDiagram: false,
});
