import express from "express";
import type { Server } from "node:http";
import { randomUUID } from "node:crypto";
import { FIXTURE_WORKSPACES, FIXTURE_PEEK_W_GROVE_1, FIXTURE_PHASES } from "./_fixtures";

/** Deterministic bearer minted on every pair consume. */
export const FAKE_DAEMON_TOKEN = "grove-fake-daemon-token";

export function startFakeDaemon(port: number): Promise<Server> {
  return new Promise((resolve) => {
    const app = express();
    app.use(express.json());

    // ─── Live question state ──────────────────────────────────────────────
    // The one pending GROUP tracked at a time (a `tool_use_id` shared by every
    // question in the batch) — enough for the spec's happy path (a single
    // workspace/session ever has a live question in these tests);
    // `/_test/push-event` sets it, `/question-answer` validates against it,
    // `/events` connections (tracked here) receive the delta.
    let pendingQuestionToolUseId: string | null = null;
    let eventSeq = 1;
    const eventClients = new Set<express.Response>();

    // ─── Auth: pairing bootstrap + bearer gate ───────────────────────────
    // Mirrors the daemon contract (src/grove/daemon/auth.py): POST /auth/pair
    // and GET /auth/pair/{id} are the only unauthenticated entry points; the
    // fake auto-approves, so the FIRST poll consumes and returns the token.
    // Every data endpoint requires the bearer, so the suite actually pins
    // the cookie → BFF → bearer chain, not just page rendering.
    const challenges = new Set<string>();
    const isOpen = (path: string) =>
      path === "/healthz" ||
      path === "/openapi.json" ||
      path.startsWith("/auth/pair") ||
      // Test-harness control plane only (the live-question spec) —
      // not a real daemon route, so it never needs the bearer.
      path.startsWith("/_test/");
    app.use((req, res, next) => {
      if (isOpen(req.path) || req.headers.authorization === `Bearer ${FAKE_DAEMON_TOKEN}`) {
        next();
        return;
      }
      res
        .status(401)
        .json({ detail: { error: "auth_invalid", message: "missing or bad bearer" } });
    });

    app.post("/auth/pair", (req, res) => {
      const id = randomUUID();
      challenges.add(id);
      // PairingChallengeView shape.
      res.json({
        challenge_id: id,
        label: String(req.body?.label ?? "browser"),
        code: "FAKE-42",
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        state: "pending",
      });
    });

    app.get("/auth/pair/:id", (req, res) => {
      // Auto-approve: consume on first poll; like the real daemon, a resolved
      // (or unknown) challenge polls as pair_not_found.
      if (!challenges.delete(req.params.id)) {
        res
          .status(404)
          .json({ detail: { error: "pair_not_found", message: "unknown or resolved challenge" } });
        return;
      }
      // PairResultView consume shape — the one response that carries a token.
      res.json({
        challenge_id: req.params.id,
        state: "consumed",
        token: FAKE_DAEMON_TOKEN,
        expires_at: new Date(Date.now() + 30 * 24 * 3600_000).toISOString(),
      });
    });

    // SessionView — the BFF calls this right after consume to learn the
    // session id it stores for logout-time revocation.
    app.get("/auth/sessions/me", (_req, res) => {
      const now = new Date();
      res.json({
        session_id: "00000000-0000-0000-0000-00000000fa4e",
        label: "playwright-e2e",
        created_at: now.toISOString(),
        expires_at: new Date(now.getTime() + 30 * 24 * 3600_000).toISOString(),
        last_seen_at: now.toISOString(),
        revoked: false,
      });
    });

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
        latest_version: null,
        update_available: false,
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

    // ─── Create (workspace lifecycle parity) ─────────────────────────────
    // Mirrors the daemon contract: 422 when repo_root is missing, else echo a
    // freshly-minted WorkspaceStateView built from the request's branch plan.
    // Non-destructive to the read fixtures (one shared daemon serves every
    // worker), so it can't contaminate other specs; the stateful pause/resume/
    // kill transitions are driven per-test via page.route in lifecycle.spec.
    app.post("/workspaces", (req, res) => {
      const body = req.body ?? {};
      if (!body.repo_root) {
        res.status(422).json({
          detail: { error: "repo_root_required", message: "repo_root is required" },
        });
        return;
      }
      const plan = body.branch_plan ?? { kind: "auto" };
      const now = new Date().toISOString();
      res.json({
        id: `w-new-${Math.random().toString(36).slice(2, 8)}`,
        title: body.title,
        repo_root: body.repo_root,
        branch: plan.kind === "root" ? "main" : (plan.name ?? `grove/${body.title}`),
        base_branch: plan.base_ref ?? "main",
        worktree_path: `${body.repo_root}/.worktrees/new`,
        tmux_session: `grove-${body.title}`,
        agent_name: body.agent_name,
        status: "running",
        created_at: now,
        updated_at: now,
        paused_at: null,
        error_detail: null,
        description: body.description ?? null,
        init_status: body.skip_init ? "skipped" : "ok",
        init_duration_ms: body.skip_init ? 0 : 200,
        branch_provenance:
          plan.kind === "existing_local" || plan.kind === "root" ? "attached" : "grove",
        placement: plan.kind === "root" ? "root" : "worktree",
      });
    });

    // ─── Create-form pickers (workspace lifecycle parity) ────────────────
    app.get("/agents", (req, res) => {
      if (!req.query.repo) {
        res.status(422).json({ detail: [{ msg: "repo required" }] });
        return;
      }
      res.json([
        {
          name: "claude",
          kind: "claude_code",
          description: "Anthropic Claude Code",
          models: ["claude-sonnet-4-6", "opus", "haiku"],
        },
        { name: "shell", kind: "generic", description: "Plain shell", models: [] },
      ]);
    });
    app.get("/branches", (req, res) => {
      if (!req.query.repo || !req.query.scope) {
        res.status(422).json({ detail: [{ msg: "repo + scope required" }] });
        return;
      }
      if (req.query.scope === "remote") {
        res.json([{ name: "origin/main", kind: "remote" }]);
        return;
      }
      res.json([
        { name: "main", kind: "local", is_current: true, upstream: null, checked_out_in: null },
        { name: "develop", kind: "local", is_current: false, upstream: null, checked_out_in: null },
      ]);
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

    // ─── Project-wide sessions (home page Sessions section) ─────────────
    // Mirrors the daemon contract: `repo` required (404 unknown_repo_root
    // when no fixture workspace lives there), newest-first across ALL the
    // project's worktrees — grove-launched rows workspace-attributed, plus
    // one hand-staged (fs_discovered, null workspace trio) session.
    app.get("/sessions", (req, res) => {
      const limit = Math.min(Math.max(Number(req.query.limit ?? 50) || 50, 1), 200);
      // Scope is a VALUE of `repo`, never a second route: omitted widens
      // to the host-wide Session Catalog, metadata-only.
      if (req.query.repo === undefined) {
        res.json(buildCatalog().slice(0, limit));
        return;
      }
      const repo = String(req.query.repo ?? "");
      const members = FIXTURE_WORKSPACES.filter((w) => w.repo_root === repo);
      if (members.length === 0) {
        res
          .status(404)
          .json({ detail: { error: "unknown_repo_root", message: `no project at ${repo}` } });
        return;
      }
      res.json(buildProjectSessions(repo, members).slice(0, limit));
    });

    // ─── Catalog drill-in: the workspace-LESS turns route ────────────────
    // Resolves by the `(kind, cwd, session_id)` triple a catalog row carries,
    // because most sessions on a host were never launched by Grove. Any
    // coordinate mismatch is ONE typed 404 — never another session's turns.
    app.get("/sessions/:sessionId/turns", (req, res) => {
      const row = buildCatalog().find(
        (r) =>
          r.session_id === req.params.sessionId &&
          r.adapter_kind === String(req.query.kind ?? "") &&
          r.cwd === String(req.query.cwd ?? ""),
      );
      if (!row) {
        res.status(404).json({
          detail: { error: "agent_session_not_found", message: "no such session recorded" },
        });
        return;
      }
      res.json({ session: row, turns: buildCatalogTurns(row) });
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

    // ─── Workspace steering ───────────────────────────────────────────────
    // Mirrors the daemon contract: 204 on success, 422 on an empty text, and
    // the typed 409 refusal envelope when the agent isn't steerable (here:
    // any non-active fixture workspace, matching the "working" gating the
    // fixtures derive from ws.status).
    app.post("/workspaces/:id/message", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      const text = req.body?.text;
      if (typeof text !== "string" || text.length === 0) {
        res.status(422).json({ detail: [{ msg: "text must be non-empty" }] });
        return;
      }
      if (ws.status !== "active") {
        res.status(409).json({
          detail: { error: "agent_not_running", message: "no running agent to steer" },
        });
        return;
      }
      res.status(204).end();
    });

    app.post("/workspaces/:id/interrupt", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      if (ws.status !== "active") {
        res.status(409).json({
          detail: { error: "agent_not_running", message: "no running agent to interrupt" },
        });
        return;
      }
      res.status(204).end();
    });

    // ─── Session remap ───────────────────────────────────────────────────
    // Mirrors the daemon contract: 404 workspace_not_found, 404
    // agent_session_not_found (the ref matches zero or more-than-one
    // session), else 200 with the WorkspaceStateView. `agent_session_id`
    // isn't on that wire view (see webapp/CLAUDE.md), so there's
    // nothing to mutate in the fixture; this only exercises the
    // request/response shape + refusal envelope, read-only against
    // FIXTURE_WORKSPACES.
    app.post("/workspaces/:id/session", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      const ref = String(req.body?.session_ref ?? "");
      const candidates = buildSessions(ws);
      // Exact id wins outright — some fixture ids are literal prefixes of a
      // sibling's (`s-w-grove-1` / `s-w-grove-1-prior`), so a naive prefix
      // filter alone would call an exact match "ambiguous".
      const exact = candidates.find((s) => s.session_id === ref);
      const matches = exact ? [exact] : candidates.filter((s) => s.session_id.startsWith(ref));
      if (matches.length !== 1) {
        res.status(404).json({
          detail: {
            error: "agent_session_not_found",
            message: `no unique session for "${ref}"`,
          },
        });
        return;
      }
      res.json(ws);
    });

    // ─── Live question answer-back ───────────────────────────────────────
    // Mirrors the daemon contract: 204 dispatched (resolution rides back over
    // /events separately, driven by the test's /_test/push-event calls, never
    // by this response), 404 unknown workspace, 409 a tool_use_id that isn't
    // the currently-tracked pending one (stale), 422 an empty answers array.
    app.post("/workspaces/:id/question-answer", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      const { tool_use_id: toolUseId, answers } = req.body ?? {};
      if (!Array.isArray(answers) || answers.length === 0) {
        res.status(422).json({ detail: [{ msg: "answers must be non-empty" }] });
        return;
      }
      if (toolUseId !== pendingQuestionToolUseId) {
        res.status(409).json({
          detail: { error: "question_not_pending", message: "no longer the pending question" },
        });
        return;
      }
      res.status(204).end();
    });

    // ─── Test-harness control plane (question.spec.ts ONLY) ─────────────
    // Not a daemon route: lets a Playwright spec push a live `session_activity`
    // delta (a pending question GROUP appearing/resolving) over the SAME
    // /events connection the page already holds, instead of teaching the
    // fixture fetch-and-poll layer to simulate push semantics. `questions` is
    // a LIST (a real AskUserQuestion batch can carry more than one, answered
    // atomically) — every question in it shares one `group_id`.
    app.post("/_test/push-event", (req, res) => {
      const { workspaceId, questions } = req.body ?? {};
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === workspaceId);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      const pending: Array<{ group_id: string }> = Array.isArray(questions) ? questions : [];
      pendingQuestionToolUseId = pending[0]?.group_id ?? null;
      const delta = {
        kind: "session_activity",
        seq: ++eventSeq,
        workspace: workspaceActivity(ws, pending as Record<string, unknown>[]),
      };
      const payload = `id: ${delta.seq}\nevent: session_activity\ndata: ${JSON.stringify(delta)}\n\n`;
      for (const client of eventClients) client.write(payload);
      res.status(204).end();
    });

    // Reassigns the server's notion of "the pending tool_use_id" WITHOUT
    // broadcasting an SSE delta — models the race the 409 response exists
    // for: the question resolved (or moved on) faster than this page's
    // stream delivered the update, so its still-rendered card answers a
    // tool_use_id the daemon no longer considers pending.
    app.post("/_test/set-pending-tool-use-id", (req, res) => {
      pendingQuestionToolUseId = req.body?.toolUseId ?? null;
      res.status(204).end();
    });

    // ─── Activity Dashboard ───────────────────────────────────────────────
    app.get("/activity", (_req, res) => {
      res.json(buildActivitySnapshot());
    });

    // In-flight provisioning progress. Fetched only while a workspace reads
    // `provisioning`, so no fixture workspace hits it by default — the
    // provisioning spec rewrites a peek to reach this. Returns the engine's
    // three facts verbatim: elapsed, the last line written, the recent tail.
    app.get("/workspaces/:id/provision", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      const lines = [
        "#5 [internal] load build context",
        "#8 [4/9] RUN apt-get install -y build-essential",
        "#8 sha256:deadbeef extracting layers",
      ];
      res.json({ elapsed_ms: 131_000, headline: lines[lines.length - 1], lines });
    });

    // Focused live pane — one-shot ANSI snapshot.
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

    // Focused live pane — SSE stream of `pane_snapshot` frames. Mirrors
    // the daemon: ONE frame with the same per-workspace ANSI the one-shot
    // `/pane` route returns, then keepalive comments (the real daemon is
    // diff-guarded, so a steady pane emits only keepalives after the first).
    app.get("/workspaces/:id/pane/stream", (req, res) => {
      const ws = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!ws) {
        res.status(404).json({ detail: { error: "workspace_not_found", message: "missing" } });
        return;
      }
      res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      });
      const frame = {
        kind: "pane_snapshot",
        seq: 1,
        workspace_id: ws.id,
        pane: {
          workspace_id: ws.id,
          ansi: `agent pane for ${ws.title}\n$ running...\n`,
          taken_at: new Date().toISOString(),
        },
      };
      res.write(`id: 1\nevent: pane_snapshot\ndata: ${JSON.stringify(frame)}\n\n`);
      const keepalive = setInterval(() => res.write(": keepalive\n\n"), 1000);
      req.on("close", () => clearInterval(keepalive));
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
      const frame = { kind: "snapshot", seq: eventSeq, snapshot: buildActivitySnapshot() };
      res.write(`id: ${eventSeq}\nevent: snapshot\ndata: ${JSON.stringify(frame)}\n\n`);
      // Tracked so `/_test/push-event` (question.spec.ts) can push a live
      // `session_activity` delta to every connected page, mirroring the real
      // daemon's SSE fan-out.
      eventClients.add(res);
      const keepalive = setInterval(() => res.write(": keepalive\n\n"), 1000);
      req.on("close", () => {
        clearInterval(keepalive);
        eventClients.delete(res);
      });
    });

    const server = app.listen(port, "127.0.0.1", () => resolve(server));
  });
}

/**
 * Two deterministic SessionSummaryViews per workspace, newest-first: a live
 * grove-launched session plus an older hand-started (fs_discovered) one, so
 * the panel exercises both provenance labels. The orphaned w-other-1 has NO
 * recorded sessions — the fixture for the no-transcript case (the detail
 * page's agent tabs default to Terminal there).
 */
function buildSessions(ws: (typeof FIXTURE_WORKSPACES)[number]) {
  if (ws.id === "w-other-1") return [];
  const act = (state: string, turns: number) => ({
    state,
    title: `ai: ${ws.title}`,
    current_task: null,
    human_turns: turns,
    assistant_replies: turns * 2,
    replies_per_turn: [2, 2],
    tool_calls: turns * 3,
    // In-flight background subagents — only the working session carries any
    // (the detail page's subagent badge fixture; hidden at 0).
    active_subagents: state === "working" ? 2 : 0,
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
      workspace_title: ws.title,
      workspace_branch: ws.branch,
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
      workspace_title: ws.title,
      workspace_branch: ws.branch,
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

/**
 * Project-wide sessions for `GET /sessions?repo=` — every workspace's
 * grove-launched session (workspace-attributed) plus one hand-staged
 * session with the null workspace trio, newest-first (the hand-staged
 * session's timestamp is deliberately earlier than the grove fixtures').
 */
function buildProjectSessions(repo: string, members: typeof FIXTURE_WORKSPACES) {
  const grove = members.flatMap((ws) =>
    buildSessions(ws).filter((s) => s.provenance === "grove_launched"),
  );
  const handStaged = {
    session_id: `s-hand-${repo.split("/").pop()}`,
    adapter_kind: "claude_code",
    provenance: "fs_discovered",
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    git_branch: "main",
    created_at: "2026-05-08T09:00:00Z",
    modified_at: "2026-05-08T10:00:00Z",
    size_bytes: 2048,
    title: null,
    first_prompt: `poke around ${repo}`,
    last_prompt: "wrap up",
    activity: {
      state: "idle",
      title: null,
      current_task: null,
      human_turns: 1,
      assistant_replies: 2,
      replies_per_turn: [2],
      tool_calls: 3,
      model: "claude-opus-4-8",
      tokens_in: 400,
      tokens_out: 40,
      last_event_at: null,
      needs_attention: false,
      error_detail: null,
    },
  };
  return [...grove, handStaged];
}

/**
 * The host-wide Session Catalog — the shape `GET /sessions` returns with
 * `repo` OMITTED, newest-first. Metadata-only by contract: `activity`,
 * `size_bytes`, `title`, and both prompts are NULL because the host scan pays
 * one bounded head read per session and never parses a transcript. A fixture
 * that filled them would let a bug that reads null-as-zero pass here.
 *
 * Deliberately covers the four cases the screen must handle honestly:
 *   · a Grove-managed row (`workspace_id` set, project `is_grove_managed`)
 *   · a repo Grove has NEVER managed — the whole point of the catalog, and a row
 *     the rail's per-known-project fan-out structurally cannot produce
 *   · a session with no enclosing git repo (`project: null`)
 *   · a session whose head read recovered no cwd (`cwd: null`) — undrillable,
 *     listed anyway
 */
function buildCatalog() {
  const project = (repoRoot: string, repoName: string, groveManaged: boolean) => ({
    repo_root: repoRoot,
    repo_name: repoName,
    is_worktree: groveManaged,
    is_grove_managed: groveManaged,
  });
  const base = {
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    size_bytes: null,
    title: null,
    first_prompt: null,
    last_prompt: null,
    activity: null,
    live: false,
    primary: false,
  };
  return [
    {
      ...base,
      session_id: "cat-foreign-1",
      adapter_kind: "codex",
      provenance: "fs_discovered",
      git_branch: "main",
      created_at: "2026-07-20T08:00:00Z",
      modified_at: "2026-07-20T12:00:00Z",
      cwd: "/repos/untracked-lab",
      project: project("/repos/untracked-lab", "untracked-lab", false),
      live: true,
    },
    {
      ...base,
      session_id: "cat-grove-1",
      adapter_kind: "claude_code",
      provenance: "grove_launched",
      primary: true,
      workspace_id: "w-grove-1",
      workspace_title: "feat dashboard",
      git_branch: "dev/feat-dashboard",
      created_at: "2026-07-19T08:00:00Z",
      modified_at: "2026-07-19T10:00:00Z",
      cwd: "/repos/Grove/.worktrees/dash",
      project: project("/repos/Grove/.worktrees/dash", "Grove", true),
    },
    {
      ...base,
      session_id: "cat-norepo-1",
      adapter_kind: "claude_code",
      provenance: "fs_discovered",
      git_branch: null,
      created_at: "2026-07-18T08:00:00Z",
      modified_at: "2026-07-18T09:00:00Z",
      cwd: "/tmp/scratchpad",
      project: null,
    },
    {
      ...base,
      session_id: "cat-nocwd-1",
      adapter_kind: "claude_code",
      provenance: "fs_discovered",
      git_branch: null,
      created_at: "2026-07-17T08:00:00Z",
      modified_at: "2026-07-17T09:00:00Z",
      cwd: null,
      project: null,
    },
  ];
}

/** A short recorded conversation for a catalog row's read-only drill-in. */
function buildCatalogTurns(row: ReturnType<typeof buildCatalog>[number]) {
  return [
    {
      user_text: `what happened in ${row.cwd}?`,
      started_at: row.created_at,
      entries: [{ role: "assistant", text: "This is an archived conversation." }],
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
    // The new wire shapes: a subagent spawn (tool text carries the full
    // "Agent(kind): description" line), the background-task notification it
    // produced (summary line + full result), and an unbroken token that must
    // wrap inside the bubble rather than widen the page.
    {
      user_text: "map the webapp in the background",
      started_at: ws.updated_at,
      entries: [
        { role: "tool", text: "Agent(Explore): map the webapp" },
        {
          role: "notification",
          text: "Background task completed: Explore\nThe webapp has a BFF proxy, a home grid, an activity wall, and a detail page.",
        },
        { role: "assistant", text: `Survey landed. ${"unbroken-token-".repeat(25)}end` },
      ],
    },
    // A structured agent question: the agent paused to ask, and the
    // transcript renders it as a read-only choice card. `text` is the digest
    // fallback; the structured payload rides `question`.
    {
      user_text: "which path should we take?",
      started_at: ws.updated_at,
      entries: [
        {
          role: "question",
          text: "Which migration strategy?",
          question: {
            id: "q-1",
            group_id: "g-1",
            kind: "single_select",
            prompt: "Which migration strategy?",
            header: "Decision needed",
            options: [
              { label: "Big-bang cutover", description: "Faster, riskier" },
              { label: "Incremental", description: "Slower, safer" },
            ],
            multiselect: false,
            answered: false,
            answer: null,
            source_tool: "AskUserQuestion",
          },
        },
      ],
    },
    // A long user paste: renders past the 6-line clamp in every
    // viewport so the SmartCollapse fade + Show more toggle appear. NO tool
    // entries here — chat.spec pins the tool-group count at 3.
    {
      user_text: `requirements dump: ${"the workspace page must show as much transcript as possible on every device we support, without wasting a single row of chrome on decoration. ".repeat(8)}`,
      started_at: ws.updated_at,
      entries: [{ role: "assistant", text: "Understood — density first." }],
    },
    // Filler so the transcript overflows its viewport in e2e — the
    // opens-at-the-tail assertion needs a genuinely scrollable conversation.
    ...Array.from({ length: 6 }, (_, i) => ({
      user_text: `iterate ${i + 1}`,
      started_at: ws.updated_at,
      entries: [
        {
          role: "assistant",
          text: `Iteration ${i + 1} complete; transcript filler so the conversation overflows.`,
        },
      ],
    })),
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
    cwd: repo_root,
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

/**
 * `questions` is the live-pending-question payload (`AgentQuestionView[]`,
 * ordered as asked — a real AskUserQuestion batch can carry more than one,
 * answered atomically) — passed only by `/_test/push-event`
 * (question.spec.ts); every other caller keeps it `[]`.
 */
function workspaceActivity(
  ws: (typeof FIXTURE_WORKSPACES)[number],
  questions: Record<string, unknown>[] = [],
) {
  // Map workspace status → a representative agent state for visual variety.
  // A pending question always reads as BLOCKED — the hook-sourced signal
  // that overrides the transcript blend (core/activity.py).
  const agentState =
    questions.length > 0
      ? "blocked"
      : ws.status === "active"
        ? "working"
        : ws.status === "idle"
          ? "waiting"
          : "idle";
  const attention = agentState === "waiting" || agentState === "blocked";
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
          questions,
        },
      },
    ],
    // The third status axis — reported by only one fixture workspace, so
    // the "no phase ⇒ no chrome" half of the contract is covered by the rest.
    phase: FIXTURE_PHASES[ws.id] ?? null,
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
