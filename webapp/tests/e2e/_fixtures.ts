export const FIXTURE_WORKSPACES = [
  {
    id: "w-grove-1",
    title: "feat dashboard",
    repo_root: "/repos/Grove",
    branch: "dev/feat-dashboard",
    base_branch: "main",
    worktree_path: "/repos/Grove/.worktrees/dash",
    tmux_session: "grove-dash",
    agent_name: "claude",
    status: "active",
    created_at: "2026-05-09T10:00:00Z",
    updated_at: "2026-05-09T10:30:00Z",
    paused_at: null,
    error_detail: null,
    description: null,
    init_status: "ok",
    init_duration_ms: 350,
    branch_provenance: "grove",
    placement: "worktree",
    // Issue-only: the state a workspace lives in from create until a PR
    // exists. Linkage must render even in this state.
    ticket_refs: [
      {
        provider: "gitea",
        id: "330",
        kind: "issue",
        title: "surface the status axes",
        url: "https://git.example/bearlike/Grove/issues/330",
        status: "open",
        assignee: null,
        ambiguous: false,
      },
    ],
  },
  {
    id: "w-grove-2",
    title: "fix tests",
    repo_root: "/repos/Grove",
    branch: "dev/fix-tests",
    base_branch: "main",
    worktree_path: "/repos/Grove/.worktrees/tests",
    tmux_session: "grove-tests",
    agent_name: "claude",
    status: "idle",
    created_at: "2026-05-09T08:00:00Z",
    updated_at: "2026-05-09T09:30:00Z",
    paused_at: null,
    error_detail: null,
    description: null,
    init_status: "ok",
    init_duration_ms: 220,
    branch_provenance: "grove",
    placement: "worktree",
    // The other half of the linkage: issue → PR, the row's one colored token.
    ticket_refs: [
      {
        provider: "gitea",
        id: "331",
        kind: "issue",
        title: "flaky test",
        url: "https://git.example/bearlike/Grove/issues/331",
        status: "open",
        assignee: null,
        ambiguous: false,
      },
      {
        provider: "gitea",
        id: "332",
        kind: "pull_request",
        title: "fix the flake",
        url: "https://git.example/bearlike/Grove/pulls/332",
        status: "open",
        assignee: null,
        ambiguous: false,
      },
    ],
  },
  {
    id: "w-other-1",
    title: "spike",
    repo_root: "/repos/website",
    branch: "spike/refactor",
    base_branch: "main",
    worktree_path: "/repos/website/.worktrees/spike",
    tmux_session: "site-spike",
    agent_name: "claude",
    status: "orphaned",
    created_at: "2026-04-09T08:00:00Z",
    updated_at: "2026-04-10T08:00:00Z",
    paused_at: null,
    error_detail: null,
    description: null,
    init_status: "skipped",
    init_duration_ms: 0,
    branch_provenance: "user",
    placement: "root",
  },
];

/**
 * Task phase per workspace — it rides `WorkspaceActivityView`, NOT the state,
 * so it lives beside the fixtures rather than inside them. Only `w-grove-1`
 * reports one, so every spec that touches another workspace also pins the
 * "reports no phase ⇒ renders nothing" half of the contract for free.
 */
export const FIXTURE_PHASES: Record<string, Record<string, unknown>> = {
  "w-grove-1": {
    phase: "implementing",
    note: "wiring the third axis into the header",
    updated_at: "2026-05-09T10:28:00Z",
    index: 2,
    total: 6,
  },
};

export const FIXTURE_PEEK_W_GROVE_1 = {
  state: FIXTURE_WORKSPACES[0],
  base_ahead: 3,
  base_behind: 0,
  diff_added: 124,
  diff_removed: 17,
  dirty_files: 2,
  recent_commits: [
    { sha: "abc1234567", subject: "feat: scaffold dashboard", committed_at: "2026-05-09T10:25:00Z" },
    { sha: "def4567890", subject: "feat: card component", committed_at: "2026-05-09T10:10:00Z" },
  ],
  // The 400-char line pins the horizontal-overflow regression: it must scroll
  // INSIDE the terminal pane, never widen the page (detail.spec asserts both).
  agent_snapshot: `$ npm run dev\n> next dev\n  ready in 1.2s\nLOG ${"wide-terminal-grid-".repeat(20)}end\n`,
  snapshot_taken_at: "2026-05-09T10:30:00Z",
};
