import express from "express";
import type { Server } from "node:http";
import { FIXTURE_WORKSPACES, FIXTURE_PEEK_W_GROVE_1 } from "./_fixtures";

export function startFakeDaemon(port: number): Promise<Server> {
  return new Promise((resolve) => {
    const app = express();
    app.use(express.json());

    app.get("/healthz", (_req, res) => {
      res.json({ status: "ok", version: "0.0.0-fake" });
    });
    app.get("/whoami", (_req, res) => {
      res.json({
        version: "0.0.0-fake",
        started_at: new Date(Date.now() - 3600_000).toISOString(),
        uptime_seconds: 3600,
        host: "fake-host",
        user: "tester",
        platform: "linux",
        python_version: "3.12.0",
      });
    });
    app.get("/openapi.json", (_req, res) => {
      res.json({ openapi: "3.1.0", info: { title: "fake" }, paths: {} });
    });
    app.get("/workspaces", (_req, res) => {
      res.json(FIXTURE_WORKSPACES);
    });
    app.get("/workspaces/:id", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      res.json(ws);
    });
    app.get("/workspaces/:id/commits", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      // Synthetic commit log so the detail page has real rows to render.
      // Newest first; count derived from the id hash so each fixture
      // workspace gets a stable, distinct list length.
      const hash = ws.id.split("").reduce((a, c) => (a + c.charCodeAt(0)) % 11, 0);
      const count = Math.max(1, hash);
      const out = Array.from({ length: count }, (_, i) => ({
        sha: `${ws.id.slice(-3)}${i.toString(16).padStart(4, "0")}`,
        subject: `feat: synthetic commit ${count - i}`,
        committed_at: new Date(2026, 4, 9, 10, count - i).toISOString(),
      }));
      res.json(out);
    });

    app.get("/workspaces/:id/peek", (req, res) => {
      if (req.params.id === "w-grove-1") {
        res.json(FIXTURE_PEEK_W_GROVE_1);
        return;
      }
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      // Synthetic but non-zero peek so home-grid stat trios render with
      // real numbers in E2E. Determinism: derive counts from the id hash
      // so each fixture workspace gets a stable, distinct triplet.
      const hash = ws.id.split("").reduce((a, c) => (a + c.charCodeAt(0)) % 17, 0);
      res.json({
        state: ws,
        base_ahead: hash % 5,
        base_behind: (hash + 1) % 3,
        diff_added: hash * 7,
        diff_removed: hash * 2,
        dirty_files: hash % 4,
        recent_commits: [],
        agent_snapshot: null,
        snapshot_taken_at: null,
      });
    });

    // ─── Session drill-down (detail page Sessions panel) ────────────────
    app.get("/workspaces/:id/sessions", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      res.json(buildSessions(ws));
    });

    app.get("/workspaces/:id/sessions/:sessionId/turns", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      const session = ws && buildSessions(ws).find((s) => s.session_id === req.params.sessionId);
      if (!ws || !session) {
        res.status(404).json({ detail: { error: "session_not_found", message: "missing" } });
        return;
      }
      res.json({ session, turns: buildTurns(ws) });
    });

    // ─── Activity Dashboard (#17) ───────────────────────────────────────
    app.get("/activity", (_req, res) => {
      res.json(buildActivitySnapshot());
    });

    // Focused live pane (#19) — one-shot ANSI snapshot.
    app.get("/workspaces/:id/pane", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      res.json({
        workspace_id: ws.id,
        ansi: `agent pane for ${ws.title}\n$ running...\n`,
        taken_at: new Date().toISOString(),
      });
    });

    // SSE stream: a snapshot on connect, then keepalive comments so the
    // connection stays open (the dashboard also has a /activity poll fallback,
    // so the wall renders either way).
    app.get("/events", (req, res) => {
      res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      });
      const frame = { kind: "snapshot", seq: 1, snapshot: buildActivitySnapshot() };
      res.write(`id: 1\nevent: snapshot\ndata: ${JSON.stringify(frame)}\n\n`);
      const keepalive = setInterval(() => res.write(": keepalive\n\n"), 1000);
      req.on("close", () => clearInterval(keepalive));
    });

    const server = app.listen(port, "127.0.0.1", () => resolve(server));
  });
}

/**
 * Two deterministic SessionSummaryViews per workspace, newest-first: a live
 * grove-launched session plus an older hand-started (fs_discovered) one, so
 * the panel exercises both provenance labels.
 */
function buildSessions(ws: (typeof FIXTURE_WORKSPACES)[number]) {
  const act = (state: string, turns: number) => ({
    state,
    title: `ai: ${ws.title}`,
    current_task: null,
    human_turns: turns,
    assistant_replies: turns * 2,
    replies_per_turn: [2, 2],
    tool_calls: turns * 3,
    model: "claude-opus-4-8",
    tokens_in: 1200,
    tokens_out: 120,
    last_event_at: null,
    needs_attention: false,
    error_detail: null,
  });
  return [
    {
      session_id: `s-${ws.id}`,
      adapter_kind: "claude_code",
      provenance: "grove_launched",
      workspace_id: ws.id,
      git_branch: ws.branch,
      created_at: ws.created_at,
      modified_at: ws.updated_at,
      size_bytes: 4096,
      title: `ai: ${ws.title}`,
      first_prompt: `build ${ws.title}`,
      last_prompt: "run the tests",
      activity: act(ws.status === "active" ? "working" : "idle", 3),
    },
    {
      session_id: `s-${ws.id}-prior`,
      adapter_kind: "claude_code",
      provenance: "fs_discovered",
      workspace_id: ws.id,
      git_branch: ws.branch,
      created_at: ws.created_at,
      modified_at: ws.created_at,
      size_bytes: 1024,
      title: null,
      first_prompt: `explore ${ws.title}`,
      last_prompt: `explore ${ws.title}`,
      activity: act("idle", 1),
    },
  ];
}

/** Oldest-first turns; the head turn has an empty user_text (resumed session). */
function buildTurns(ws: (typeof FIXTURE_WORKSPACES)[number]) {
  return [
    {
      user_text: "",
      started_at: ws.created_at,
      entries: [{ role: "summary", text: "continued from a prior session" }],
    },
    {
      user_text: `build ${ws.title}`,
      started_at: ws.created_at,
      entries: [
        { role: "assistant", text: "Starting on it." },
        { role: "tool", text: "Edit app/page.tsx" },
      ],
    },
    {
      user_text: "run the tests",
      started_at: ws.updated_at,
      entries: [
        { role: "tool", text: "Bash npm test" },
        { role: "assistant", text: "All green." },
      ],
    },
  ];
}

/** Build a DashboardSnapshotView from the workspace fixtures, grouped by repo. */
function buildActivitySnapshot() {
  const byRepo = new Map<string, ReturnType<typeof workspaceActivity>[]>();
  for (const ws of FIXTURE_WORKSPACES) {
    const rows = byRepo.get(ws.repo_root) ?? [];
    rows.push(workspaceActivity(ws));
    byRepo.set(ws.repo_root, rows);
  }
  const projects = [...byRepo.entries()].map(([repo_root, workspaces]) => ({
    repo_root,
    repo_name: repo_root.split("/").pop() ?? repo_root,
    workspaces,
  }));
  const all = projects.flatMap((p) => p.workspaces);
  return {
    projects,
    generated_at: new Date().toISOString(),
    total_workspaces: all.length,
    needs_attention: all.filter((w) => w.needs_attention).length,
  };
}

function workspaceActivity(ws: (typeof FIXTURE_WORKSPACES)[number]) {
  // Map workspace status → a representative agent state for visual variety.
  const agentState =
    ws.status === "active" ? "working" : ws.status === "idle" ? "waiting" : "idle";
  const attention = agentState === "waiting";
  return {
    state: ws,
    sessions: [
      {
        session: {
          session_id: `s-${ws.id}`,
          adapter_kind: "claude_code",
          provenance: "grove_launched",
          tmux_window: "agent",
        },
        activity: {
          state: agentState,
          title: `ai: ${ws.title}`,
          current_task: null,
          human_turns: 2,
          assistant_replies: 5,
          replies_per_turn: [3, 2],
          tool_calls: 8,
          model: "claude-opus-4-8",
          tokens_in: 1200,
          tokens_out: 120,
          last_event_at: null,
          needs_attention: attention,
          error_detail: null,
        },
      },
    ],
    base_ahead: 1,
    base_behind: 0,
    diff_added: 30,
    diff_removed: 4,
    dirty_files: 2,
    pane_target: `${ws.tmux_session}:agent`,
    needs_attention: attention,
    // The durable latest-activity signal the card now reads (recent_commits[0]).
    recent_commits: [
      { sha: ws.id.slice(0, 8), subject: `feat: ${ws.title}`, committed_at: ws.updated_at },
    ],
    observed_at: new Date().toISOString(),
  };
}
