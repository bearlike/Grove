import type {
  DashboardSnapshotView,
  DiagramDocumentView,
  PhaseView,
  SessionControlsView,
  SessionDetailView,
  SessionSummaryView,
  SessionTurnView,
  TicketRef,
  TodoListView,
  WatchList,
  WorkspaceActivityView,
  WorkspaceDiffView,
  WorkspaceHistoryView,
  WorkspacePeekView,
  WorkspaceQueueView,
  WorkspaceStateView,
  TicketProviderView,
} from "@/lib/grove/api";

/**
 * THE ONE WORKSPACE THE TOUR WALKS, AND IT IS NOT REAL.
 *
 * A tour that opens whichever workspace happens to be most recent shows a
 * different page to every reader — an empty Info tab, no tickets, a fresh
 * terminal — and the step text has to hedge about what is on screen. This
 * module is the answer: one fictional workspace whose every surface is
 * populated (changes, commits, dirty files, a task with a checklist, two
 * tickets in two states, a timeline, identity, controls, a queued follow-up,
 * and an OPEN diagram), served from the browser by `demo-interceptor.ts` for
 * exactly as long as the tour is open.
 *
 * Typed as the generated wire views for the reason `tests/fixtures/turns.ts`
 * gives: `codegen:check` keeps `types.gen.ts` honest against the daemon, so a
 * contract change fails this file at typecheck rather than letting the demo
 * rot into a shape the real app no longer renders.
 *
 * Every path, name and id here is fictional (`/home/demo/acme/sample-healthd`); nothing
 * is read from the host. The `.drawio` the diagram step shows is the screenshot
 * harness's own `grove-stack.drawio`, served as a static asset.
 */
export const DEMO_WORKSPACE_ID = "onboarding-demo-workspace";
export const DEMO_SESSION_ID = "onboarding-demo-session-0001";
export const DEMO_REPO_ROOT = "/home/demo/acme/sample-healthd";
export const DEMO_DIAGRAM_PATH = "docs/architecture/grove-stack.drawio";
export const DEMO_DIAGRAM_XML_URL = "/onboarding/grove-stack.drawio";

const T0 = "2026-09-14T08:12:03.000Z";
const T1 = "2026-09-14T09:41:27.000Z";

export const DEMO_TICKETS: TicketRef[] = [
  {
    provider: "gitea",
    id: "412",
    kind: "issue",
    title: "Health endpoint should report every project root",
    url: "https://forge.example.com/acme/healthd/issues/412",
    status: "open",
    draft: false,
    assignee: "demo",
    ambiguous: false,
  },
  {
    provider: "gitea",
    id: "418",
    kind: "pull_request",
    title: "✨ feat(api): GET /healthz with per-root readability",
    url: "https://forge.example.com/acme/healthd/pulls/418",
    status: "open",
    draft: true,
    assignee: "demo",
    ambiguous: false,
  },
];

export const DEMO_STATE: WorkspaceStateView = {
  id: DEMO_WORKSPACE_ID,
  // "Sample" in the TITLE, not only in the docs: the rail, the header and the
  // tab title all print it, and a reader with a real workspace of the same
  // name must never mistake one for the other.
  title: "Sample · Health endpoint for the API",
  repo_root: DEMO_REPO_ROOT,
  branch: "grove/health-endpoint-20260914",
  base_branch: "main",
  base_commit: "b7c4e19",
  worktree_path: `${DEMO_REPO_ROOT}/.worktrees/health-endpoint-20260914`,
  tmux_session: "grove-health-endpoint-20260914",
  agent_name: "Claude Code",
  status: "active",
  created_at: T0,
  updated_at: T1,
  paused_at: null,
  error_detail: null,
  description: "Add GET /healthz reporting version, uptime and per-root readability.",
  init_status: "ok",
  init_duration_ms: 1840,
  branch_provenance: "grove",
  placement: "worktree",
  ticket_refs: DEMO_TICKETS,
  runtime: "host",
  runtime_fallback_reason: null,
  provision_status: "skipped",
  provision_duration_ms: null,
  provision_started_at: null,
  container: null,
  runtime_default_config: false,
  runtime_no_tmux: false,
  native: true,
  telemetry_session_id: "",
  share_token: null,
  share_session_id: null,
  panels: [],
  diagram: { path: DEMO_DIAGRAM_PATH, session_id: "d".repeat(32), mode: "active" },
};

export const DEMO_PHASE: PhaseView = {
  phase: "verify",
  note: "running the API suite against the stubbed unreadable root",
  blocked: false,
  updated_at: T1,
  index: 3,
  total: 6,
  tickets: [
    { ticket: "gitea:412", phase: "verify", note: "fix written, suite running", blocked: false, index: 3 },
    { ticket: "gitea:418", phase: "deliver", note: "draft PR open", blocked: false, index: 4 },
  ],
};

export const DEMO_TODO: TodoListView = {
  items: [
    { content: "Map the router module", status: "completed", active_form: "Mapping the router module" },
    { content: "Add GET /healthz", status: "completed", active_form: "Adding GET /healthz" },
    { content: "Report each project root's readability", status: "completed", active_form: "Reporting root readability" },
    { content: "Test the unreadable-root case", status: "in_progress", active_form: "Testing the unreadable-root case" },
    { content: "Wire the route into the OpenAPI export", status: "pending", active_form: "Wiring the OpenAPI export" },
  ],
};

export const DEMO_PEEK: WorkspacePeekView = {
  state: DEMO_STATE,
  base_ahead: 3,
  base_behind: 1,
  diff_added: 184,
  diff_removed: 27,
  dirty_files: 4,
  recent_commits: [
    { sha: "9f2c1ad", subject: "✅ test(api): stub an unreadable project root", committed_at: "2026-09-14T09:30:11+00:00", scope: "since_fork_point" },
    { sha: "4e81b07", subject: "✨ feat(api): report per-root readability on /healthz", committed_at: "2026-09-14T08:58:44+00:00", scope: "since_fork_point" },
    { sha: "c03d5e2", subject: "🎉 feat(api): add GET /healthz", committed_at: "2026-09-14T08:27:09+00:00", scope: "since_fork_point" },
  ],
  agent_snapshot: [
    "[1m╭─ Claude Code ───────────────────────────────────────────╮[0m",
    "[2m│[0m  [32m●[0m Running tests: [1mpytest tests/api -q[0m                      [2m│[0m",
    "[2m│[0m  [32m....................[0m[33mF[0m[32m.........[0m 29 passed, 1 failed   [2m│[0m",
    "[2m│[0m                                                          [2m│[0m",
    "[2m│[0m  [31mFAILED[0m tests/api/test_health.py::test_unreadable_root      [2m│[0m",
    "[2m│[0m  AssertionError: expected 'EACCES' in root report         [2m│[0m",
    "[2m│[0m                                                          [2m│[0m",
    "[2m│[0m  [36m⏺[0m The stat error is swallowed before the report is        [2m│[0m",
    "[2m│[0m    built. Fixing the handler to keep the OS error…         [2m│[0m",
    "[1m╰──────────────────────────────────────────────────────────╯[0m",
    "[2m  ? for shortcuts · esc to interrupt[0m",
  ].join("\n"),
  snapshot_taken_at: T1,
};

export const DEMO_DIFF: WorkspaceDiffView = {
  available: true,
  truncated: false,
  files: 4,
  patch: [
    "diff --git a/src/api/health.py b/src/api/health.py",
    "new file mode 100644",
    "--- /dev/null",
    "+++ b/src/api/health.py",
    "@@ -0,0 +1,31 @@",
    "+from fastapi import APIRouter",
    "+",
    "+from app.config import project_roots",
    "+from app.version import VERSION, uptime_seconds",
    "+",
    "+router = APIRouter()",
    "+",
    "+",
    "+@router.get(\"/healthz\")",
    "+def healthz() -> dict:",
    "+    roots = []",
    "+    for root in project_roots():",
    "+        try:",
    "+            root.stat()",
    "+            roots.append({\"path\": str(root), \"readable\": True})",
    "+        except OSError as error:",
    "+            roots.append({\"path\": str(root), \"readable\": False, \"error\": error.strerror})",
    "+    return {\"version\": VERSION, \"uptime_seconds\": uptime_seconds(), \"roots\": roots}",
    "diff --git a/src/api/routes.py b/src/api/routes.py",
    "--- a/src/api/routes.py",
    "+++ b/src/api/routes.py",
    "@@ -3,6 +3,7 @@",
    " from fastapi import FastAPI",
    " ",
    "+from .health import router as health",
    " from .projects import router as projects",
    " ",
    " app = FastAPI()",
    "+app.include_router(health)",
    " app.include_router(projects)",
    "diff --git a/tests/api/test_health.py b/tests/api/test_health.py",
    "new file mode 100644",
    "--- /dev/null",
    "+++ b/tests/api/test_health.py",
    "@@ -0,0 +1,18 @@",
    "+def test_reports_unreadable_root(client, unreadable_root):",
    "+    body = client.get(\"/healthz\").json()",
    "+    assert body[\"roots\"][-1][\"readable\"] is False",
    "+    assert \"EACCES\" in body[\"roots\"][-1][\"error\"]",
    "diff --git a/openapi.yaml b/openapi.yaml",
    "--- a/openapi.yaml",
    "+++ b/openapi.yaml",
    "@@ -12,6 +12,9 @@",
    " paths:",
    "+  /healthz:",
    "+    get:",
    "+      operationId: healthz",
    "   /projects:",
  ].join("\n"),
};

export const DEMO_CONTROLS: SessionControlsView = {
  commands: [
    { name: "/compact", scope: "builtin", detail: "Summarise the conversation so far" },
    { name: "/review", scope: "project", detail: "Review the diff against main" },
    { name: "/ship", scope: "project", detail: "Open the PR and attach it to the ticket" },
  ],
  skills: [
    { name: "working-in-grove", scope: "user", detail: "Report phase and attach tickets" },
    { name: "collaborating-on-diagrams", scope: "user", detail: "Edit a .drawio with a human" },
  ],
  mcp_servers: [
    { name: "grove", scope: "project", detail: "Workspace and diagram tools" },
    { name: "forge", scope: "user", detail: "Issues, pull requests, attachments" },
  ],
  models: ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
  current_model: "claude-opus-5",
  permission_mode: "acceptEdits",
};

export const DEMO_QUEUE: WorkspaceQueueView = {
  supported: true,
  messages: [
    { text: "When the suite is green, also add the endpoint to docs/api.md.", sent_at: T1, position: 1 },
  ],
};

/** One CI watch still running and one timer that already fired — the two
 * states a reader meets first. The address is the demo's own id, which the
 * daemon's pattern would refuse; nothing here is ever sent to it. */
export const DEMO_WATCHES: WatchList = {
  watches: [
    {
      id: "wch_00000000000000000000000000000001",
      recipient: { workspace_id: DEMO_WORKSPACE_ID, agent: "" },
      predicate: { kind: "ci", provider: "github", owner: "acme", repo: "sample-healthd", head_sha: "9f2c1ad" },
      state: "pending",
      note: "Checks on the /healthz change",
      every: "PT30S",
      created_at: T1,
      expires_at: "2026-09-14T10:26:27.000Z",
      next_due: T1,
    },
    {
      id: "wch_00000000000000000000000000000002",
      recipient: { workspace_id: DEMO_WORKSPACE_ID, agent: "" },
      predicate: { kind: "timer", at: T0 },
      state: "fired",
      note: "",
      every: "PT30S",
      created_at: T0,
      expires_at: T1,
      settled_at: T0,
      outcome: { ok: true, summary: `The timer you set for ${T0} has elapsed.`, url: null },
      receipt: "delivered",
    },
  ],
};

export const DEMO_HISTORY: WorkspaceHistoryView = {
  name: { workspace_id: DEMO_WORKSPACE_ID, title: DEMO_STATE.title, description: DEMO_STATE.description ?? null, repo_root: DEMO_REPO_ROOT, first_seen: T0, last_seen: T1, deleted_at: null },
  names: [{ title: DEMO_STATE.title, description: DEMO_STATE.description ?? null, recorded_at: T0 }],
  progress: [
    { recorded_at: T0, phase: "scope", blocked: false, note: "reading the router and the config loader", ticket_key: null },
    { recorded_at: "2026-09-14T08:20:00.000Z", phase: "build", blocked: false, note: "adding GET /healthz", ticket_key: "gitea:412" },
    { recorded_at: "2026-09-14T09:05:00.000Z", phase: "deliver", blocked: false, note: "draft PR open", ticket_key: "gitea:418" },
    { recorded_at: T1, phase: "verify", blocked: false, note: "running the API suite", ticket_key: "gitea:412" },
  ],
  tickets: [
    { ticket_key: "gitea:412", provider: "gitea", ticket_id: "412", kind: "issue", first_seen: T0, last_seen: T1 },
    { ticket_key: "gitea:418", provider: "gitea", ticket_id: "418", kind: "pull_request", first_seen: "2026-09-14T09:05:00.000Z", last_seen: T1 },
  ],
};

export const DEMO_PROVIDERS: TicketProviderView[] = [
  { provider: "gitea", label: "Gitea", configured: true, context: "acme/healthd" },
];

const SESSION: SessionSummaryView = {
  session_id: DEMO_SESSION_ID,
  adapter_kind: "claude_code",
  provenance: "hook",
  primary: true,
  workspace_id: DEMO_WORKSPACE_ID,
  workspace_title: DEMO_STATE.title,
  workspace_branch: DEMO_STATE.branch,
  git_branch: DEMO_STATE.branch,
  created_at: T0,
  modified_at: T1,
  size_bytes: 184_320,
  title: DEMO_STATE.title,
  first_prompt: "Add a health endpoint to the API.",
  last_prompt: "When the suite is green, also add the endpoint to docs/api.md.",
  activity: null,
  cwd: DEMO_STATE.worktree_path,
  project: { repo_root: DEMO_REPO_ROOT, repo_name: "sample-healthd", is_worktree: true, is_grove_managed: true },
  live: true,
  duration: { active_ms: 5_364_000, execution_ms: 4_120_000, generation_ms: 2_980_000, tool_ms: 1_140_000, elapsed_span_ms: 5_364_000, confidence: "measured" },
  turn_count: 3,
};

const TURNS: SessionTurnView[] = [
  {
    user_text:
      "Add a health endpoint to the API: GET /healthz with the daemon's version, its uptime, and whether every configured project root is readable.\n\nWork issue #412: attach it, open a draft PR that closes it, and sketch the request flow as a draw.io diagram with the Grove diagram tools so we can edit it together.",
    started_at: T0,
    sent_at: T0,
    entries: [
      { role: "assistant", text: "I'll orient on the router and the config loader first, then attach the ticket.", question: null, file_edit: null, todo: null },
      { role: "tool", text: "Read src/api/routes.py", tool: { name: "Read", tool_use_id: "t1", status: "ok", input: { file_path: "src/api/routes.py" }, result: "from fastapi import FastAPI\n…", duration_ms: 42, body: "inline" }, question: null, file_edit: null, todo: null },
      { role: "tool", text: "grove tickets attach 412", tool: { name: "Bash", tool_use_id: "t2", status: "ok", input: { command: "grove tickets attach 412" }, result: "  gitea#412  (issue)", duration_ms: 310, body: "inline" }, question: null, file_edit: null, todo: null },
      { role: "todo", text: "0/5 done", question: null, file_edit: null, todo: { items: DEMO_TODO.items.map((item) => ({ ...item, status: "pending" as const })) } },
      {
        role: "file_edit",
        text: "src/api/health.py",
        question: null,
        todo: null,
        file_edit: {
          path: `${DEMO_STATE.worktree_path}/src/api/health.py`,
          display_path: "src/api/health.py",
          old_text: "",
          new_text: "@router.get(\"/healthz\")\ndef healthz() -> dict:\n    roots = [{\"path\": str(r), \"readable\": r.exists()} for r in project_roots()]\n    return {\"version\": VERSION, \"uptime_seconds\": uptime_seconds(), \"roots\": roots}\n",
        },
      },
      { role: "assistant", text: "Endpoint added and wired into the app. Opening a draft PR against `main` that closes #412.", question: null, file_edit: null, todo: null },
      { role: "tool", text: "tea pr create --draft", tool: { name: "Bash", tool_use_id: "t3", status: "ok", input: { command: "tea pr create --draft --base main --title '✨ feat(api): GET /healthz with per-root readability'" }, result: "#418 https://forge.example.com/acme/healthd/pulls/418", duration_ms: 1_204, body: "inline" }, question: null, file_edit: null, todo: null },
    ],
  },
  {
    user_text: "Endpoint looks good. Now the diagram — keep it open so I can move things around while you work.",
    started_at: "2026-09-14T09:12:00.000Z",
    sent_at: "2026-09-14T09:12:00.000Z",
    entries: [
      { role: "tool", text: "grove_open_diagram", tool: { name: "mcp__grove__grove_open_diagram", tool_use_id: "t4", status: "ok", input: { path: DEMO_DIAGRAM_PATH }, result: "{\"diagram\":{\"mode\":\"active\"}}", duration_ms: 88, body: "inline" }, question: null, file_edit: null, todo: null },
      { role: "assistant", text: "The diagram is open in the **Diagram** tab: clients on the left, the engine in the middle, side effects on the right. Drag any box and I'll see the change on my next read.", question: null, file_edit: null, todo: null },
    ],
  },
  {
    user_text: "Looks right. Now make the unreadable-root case actually fail before you fix it.",
    started_at: "2026-09-14T09:26:00.000Z",
    sent_at: "2026-09-14T09:26:00.000Z",
    entries: [
      { role: "tool", text: "pytest tests/api -q", tool: { name: "Bash", tool_use_id: "t5", status: "error", input: { command: "pytest tests/api -q" }, result: "29 passed, 1 failed\nFAILED tests/api/test_health.py::test_unreadable_root", duration_ms: 8_410, body: "inline" }, question: null, file_edit: null, todo: null },
      { role: "todo", text: "3/5 done", question: null, file_edit: null, todo: DEMO_TODO },
      { role: "assistant", text: "The stat error is swallowed before the report is built. Fixing the handler to keep the OS error, then re-running the suite.", question: null, file_edit: null, todo: null },
    ],
  },
];

export const DEMO_TURNS: SessionDetailView = {
  session: SESSION,
  turns: TURNS,
  total_turns: TURNS.length,
  first_turn_index: 0,
  incremental: false,
};

export const DEMO_ACTIVITY: WorkspaceActivityView = {
  state: DEMO_STATE,
  sessions: [
    {
      session: { session_id: DEMO_SESSION_ID, adapter_kind: "claude_code", provenance: "hook", tmux_window: "0", parent_session_id: null },
      activity: {
        state: "working",
        title: DEMO_STATE.title,
        current_task: "Testing the unreadable-root case",
        human_turns: 3,
        assistant_replies: 5,
        replies_per_turn: [2, 1, 2],
        tool_calls: 5,
        active_subagents: 0,
        model: "claude-opus-5",
        tokens_in: 412_800,
        tokens_out: 21_640,
        // The window as the harness reports it after a turn: a real fraction,
        // never a 0 % (the meter draws nothing for an unreported one).
        context: { size: 200_000, used: 61_400, used_fraction: 0.307 },
        last_event_at: T1,
        needs_attention: false,
        error_detail: null,
        interpreted_status: null,
        questions: [],
        live: { tokens_in: 3_120, tokens_out: 410, generating_since: T1 },
      },
      duration: SESSION.duration ?? null,
      tokens: { fresh_input: 38_400, cache_read: 374_400, cache_creation: 12_800, reasoning: 18_204, output: 21_640, provider_total: 447_244 },
      latency: { avg_ms: 2_140, calls: 5 },
    },
  ],
  base_ahead: DEMO_PEEK.base_ahead,
  base_behind: DEMO_PEEK.base_behind,
  diff_added: DEMO_PEEK.diff_added,
  diff_removed: DEMO_PEEK.diff_removed,
  dirty_files: DEMO_PEEK.dirty_files,
  pane_target: "grove-health-endpoint-20260914:0.0",
  needs_attention: false,
  recent_commits: DEMO_PEEK.recent_commits,
  observed_at: T1,
  phase: DEMO_PHASE,
  todo: { total: 5, completed: 3, in_progress: 1, pending: 1 },
  queue: { pending: 1 },
  fleet: null,
};

/** The fleet snapshot with the demo workspace spliced in as its own project. */
export function withDemoWorkspace(snapshot: DashboardSnapshotView): DashboardSnapshotView {
  if (snapshot.projects.some((project) => project.repo_root === DEMO_REPO_ROOT)) return snapshot;
  return {
    ...snapshot,
    projects: [
      { repo_root: DEMO_REPO_ROOT, repo_name: "sample-healthd", cwd: DEMO_REPO_ROOT, workspaces: [DEMO_ACTIVITY] },
      ...snapshot.projects,
    ],
  };
}

/** The snapshot with the demo project removed — the exact inverse of the splice. */
export function withoutDemoWorkspace(snapshot: DashboardSnapshotView): DashboardSnapshotView {
  const projects = snapshot.projects.filter((project) => project.repo_root !== DEMO_REPO_ROOT);
  return projects.length === snapshot.projects.length ? snapshot : { ...snapshot, projects };
}

export function demoDiagram(xml: string): DiagramDocumentView {
  return { diagram: DEMO_STATE.diagram!, revision: "onboarding-demo-revision", xml };
}
