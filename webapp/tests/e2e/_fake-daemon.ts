import express from "express";
import type { Server } from "node:http";
import { createHash, randomUUID } from "node:crypto";

// The transcript fixture is shared with the unit suite — one captured
// transcript, so the two layers can never disagree about the wire shape.
import { TRANSCRIPT_TURNS } from "../fixtures/turns";
import {
  FIXTURE_ACTIVITY,
  FIXTURE_AGENTS,
  FIXTURE_BRANCHES,
  FIXTURE_CONTROLS,
  FIXTURE_PEEK,
  FIXTURE_PHASE,
  FIXTURE_PROVISION,
  FIXTURE_SESSIONS,
  FIXTURE_TODO,
  FIXTURE_USAGE,
  FIXTURE_WORKSPACES,
} from "./_fixtures";

/**
 * A hermetic stand-in for the Grove daemon, covering exactly the routes
 * webapp's five surfaces call (see `lib/grove/api/client.ts`).
 *
 * It BEARER-GATES every data route, so the suite pins the whole
 * cookie → BFF → bearer chain rather than just page rendering. Pairing
 * auto-approves: the first poll consumes and mints the deterministic token,
 * which is what lets `auth.setup.ts` run unattended.
 *
 * Deliberately a ROUTE SMOKE harness, not a full daemon. Anything testable as
 * a pure function belongs in `tests/unit`.
 */

/** Deterministic bearer minted on every pair consume. */
export const FAKE_DAEMON_TOKEN = "webapp-fake-daemon-token";

export function startFakeDaemon(port: number): Promise<Server> {
  return new Promise((resolve) => {
    const app = express();
    app.use(express.json());

    // Mirrors the daemon's auth contract: the pairing handshake and health are
    // the only unauthenticated entry points.
    const challenges = new Set<string>();
    const isOpen = (path: string): boolean =>
      path === "/healthz" ||
      path === "/openapi.json" ||
      path.startsWith("/auth/pair");

    app.use((req, res, next) => {
      if (
        isOpen(req.path) ||
        req.headers.authorization === `Bearer ${FAKE_DAEMON_TOKEN}`
      ) {
        next();
        return;
      }
      res
        .status(401)
        .json({
          detail: { error: "auth_invalid", message: "missing or bad bearer" },
        });
    });

    app.post("/auth/pair", (req, res) => {
      const id = randomUUID();
      challenges.add(id);
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
      // Auto-approve: consume on the first poll. Like the real daemon, a
      // resolved or unknown challenge polls as pair_not_found.
      if (!challenges.delete(req.params.id)) {
        res
          .status(404)
          .json({
            detail: {
              error: "pair_not_found",
              message: "unknown or resolved challenge",
            },
          });
        return;
      }
      res.json({
        challenge_id: req.params.id,
        state: "consumed",
        token: FAKE_DAEMON_TOKEN,
        expires_at: new Date(Date.now() + 30 * 24 * 3600_000).toISOString(),
      });
    });

    app.get("/auth/sessions/me", (_req, res) => {
      res.json({
        session_id: "fake-session",
        label: "playwright-e2e",
        created_at: new Date().toISOString(),
      });
    });

    app.get("/healthz", (_req, res) =>
      res.json({ status: "ok", version: "0.0.0-fake", uptime_seconds: 42 }),
    );
    app.get("/whoami", (_req, res) =>
      res.json({ user: "tester", host: "fake-host" }),
    );

    app.get("/workspaces", (_req, res) => res.json(FIXTURE_WORKSPACES));
    app.get("/workspaces/:id", (req, res) => {
      // The diagram fixture id has to resolve here too: `/w/[id]`'s
      // `generateMetadata` fetches the record before the page renders, so a
      // 404 would fail the route rather than the assertion under test.
      if (req.params.id === "diagram-workspace") {
        res.json({
          ...FIXTURE_WORKSPACES[0],
          id: "diagram-workspace",
          diagram: diagramDescriptor,
        });
        return;
      }
      const workspace = FIXTURE_WORKSPACES.find((w) => w.id === req.params.id);
      if (!workspace) {
        res
          .status(404)
          .json({
            detail: { error: "workspace_not_found", message: req.params.id },
          });
        return;
      }
      res.json(workspace);
    });

    /**
     * A diagram descriptor is served for ONE id only.
     *
     * The Diagram tab is the first conditional member of the work-panel census,
     * so serving it on the shared fixture would change every other spec's strip
     * and quietly make "the tab appears" untestable — a case that passes because
     * the tab is always there proves nothing about the condition.
     */
    const DIAGRAM_WORKSPACE = "diagram-workspace";
    const diagramDescriptor = {
      path: "docs/flow.drawio",
      session_id: "a".repeat(32),
      mode: "active" as "active" | "read_only",
    };
    let diagramXml =
      '<mxfile><diagram id="p1" name="Page 1"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>';
    const diagramHash = () =>
      createHash("sha256").update(diagramXml).digest("hex");
    let diagramRevision = diagramHash();

    app.get("/workspaces/:id/peek", (req, res) => {
      if (req.params.id !== DIAGRAM_WORKSPACE) {
        res.json(FIXTURE_PEEK);
        return;
      }
      res.json({
        ...FIXTURE_PEEK,
        state: {
          ...FIXTURE_PEEK.state,
          id: DIAGRAM_WORKSPACE,
          diagram: diagramDescriptor,
        },
      });
    });

    app.get("/workspaces/:id/diagram", (req, res) => {
      if (req.params.id !== DIAGRAM_WORKSPACE) {
        res
          .status(404)
          .json({
            detail: { error: "diagram_unavailable", message: "No diagram" },
          });
        return;
      }
      res.json({
        diagram: diagramDescriptor,
        revision: diagramRevision,
        xml: diagramXml,
      });
    });
    /**
     * Conditional by construction, exactly like the daemon: a stale
     * `expected_revision` writes nothing and answers 409. The suite's whole
     * interest in this route is that refusal.
     */
    app.put("/workspaces/:id/diagram", (req, res) => {
      if (
        req.params.id !== DIAGRAM_WORKSPACE ||
        diagramDescriptor.mode !== "active" ||
        req.body?.session_id !== diagramDescriptor.session_id
      ) {
        res
          .status(409)
          .json({
            detail: { error: "diagram_conflict", message: "stale session" },
          });
        return;
      }
      if (req.body?.expected_revision !== diagramRevision) {
        res
          .status(409)
          .json({
            detail: { error: "diagram_conflict", message: "stale revision" },
          });
        return;
      }
      diagramXml = String(req.body.xml);
      diagramRevision = diagramHash();
      res.json({
        diagram: diagramDescriptor,
        revision: diagramRevision,
        xml: diagramXml,
      });
    });
    app.post("/workspaces/:id/diagram/stop", (req, res) => {
      if (
        req.body?.session_id !== diagramDescriptor.session_id ||
        req.body?.expected_revision !== diagramRevision
      ) {
        res
          .status(409)
          .json({
            detail: { error: "diagram_conflict", message: "stale stop" },
          });
        return;
      }
      diagramDescriptor.mode = "read_only";
      res.json({
        diagram: diagramDescriptor,
        revision: diagramRevision,
        xml: diagramXml,
      });
    });
    // The fake holds exactly the revision-bound first-page result the browser
    // uploads. It proves the complete iframe → BFF → daemon path without
    // pretending an image says anything about the diagram's correctness.
    let diagramPreview: {
      session_id: string;
      revision: string;
      content_base64: string;
    } | null = null;
    app.post("/workspaces/:id/diagram/preview", (req, res) => {
      if (
        req.params.id !== DIAGRAM_WORKSPACE ||
        req.body?.session_id !== diagramDescriptor.session_id ||
        req.body?.expected_revision !== diagramRevision ||
        typeof req.body?.content_base64 !== "string"
      ) {
        res
          .status(409)
          .json({
            detail: { error: "diagram_conflict", message: "stale preview" },
          });
        return;
      }
      diagramPreview = {
        session_id: req.body.session_id,
        revision: req.body.expected_revision,
        content_base64: req.body.content_base64,
      };
      res.json({ ...diagramPreview, page_index: 0, mime_type: "image/png" });
    });
    app.get("/workspaces/:id/diagram/preview", (req, res) => {
      if (req.params.id !== DIAGRAM_WORKSPACE || diagramPreview === null) {
        res
          .status(404)
          .json({
            detail: {
              error: "diagram_unavailable",
              message: "Preview pending",
            },
          });
        return;
      }
      res.json({ ...diagramPreview, page_index: 0, mime_type: "image/png" });
    });
    app.get("/workspaces/:id/commits", (_req, res) =>
      res.json(FIXTURE_PEEK.recent_commits),
    );
    app.get("/workspaces/:id/todo", (_req, res) => res.json(FIXTURE_TODO));
    app.get("/workspaces/:id/phase", (_req, res) => res.json(FIXTURE_PHASE));
    app.get("/workspaces/:id/controls", (_req, res) =>
      res.json(FIXTURE_CONTROLS),
    );
    app.get("/workspaces/:id/provision", (_req, res) =>
      res.json(FIXTURE_PROVISION),
    );
    app.get("/workspaces/:id/pane", (_req, res) =>
      res.json({
        ansi: FIXTURE_PEEK.agent_snapshot,
        captured_at: new Date().toISOString(),
      }),
    );
    app.post("/workspaces/:id/keys", (req, res) => {
      const key = req.body?.key;
      if (
        ![
          "C-c",
          "Up",
          "Down",
          "Left",
          "Right",
          "Enter",
          "Tab",
          "Escape",
        ].includes(key)
      ) {
        res
          .status(422)
          .json({
            detail: { error: "validation_error", message: "key: invalid" },
          });
        return;
      }
      res.status(204).end();
    });

    app.get("/activity", (_req, res) => res.json(FIXTURE_ACTIVITY));
    app.get("/sessions", (_req, res) => res.json(FIXTURE_SESSIONS));

    /**
     * `/turns` answers a WINDOW, never a bare array.
     *
     * The turns fixture is the turn LIST; the route has to wrap it in the
     * `SessionDetailView` envelope the daemon actually sends. Serving the array
     * raw made `turns.data.turns` undefined, and the workspace surface reads
     * `turns.data?.turns.length` — an optional chain that guards `data` but not
     * `turns` — so the whole page died with "Cannot read properties of
     * undefined (reading 'length')" and rendered nothing.
     */
    const turnWindow = (sessionId: string) => ({
      session:
        FIXTURE_SESSIONS.find((s) => s.session_id === sessionId) ??
        FIXTURE_SESSIONS[0],
      turns: TRANSCRIPT_TURNS,
      total_turns: TRANSCRIPT_TURNS.length,
      first_turn_index: 0,
      incremental: false,
    });
    app.get("/sessions/:sessionId/turns", (req, res) =>
      res.json(turnWindow(req.params.sessionId)),
    );
    app.get("/workspaces/:id/sessions", (_req, res) =>
      res.json(FIXTURE_SESSIONS),
    );
    app.get("/workspaces/:id/sessions/:sessionId/turns", (req, res) =>
      res.json(turnWindow(req.params.sessionId)),
    );

    // Routes the workspace and composer surfaces call on mount. Absent, each
    // 404s into a console error, which the route smoke asserts against — so a
    // missing route here reads as a product defect.
    app.get("/workspaces/:id/queue", (_req, res) =>
      res.json({ messages: [], supported: true }),
    );
    app.get("/workspaces/:id/panels", (_req, res) => res.json([]));
    app.get("/tickets/providers", (_req, res) => res.json([]));
    app.get("/defaults", (_req, res) =>
      res.json({
        agent: null,
        runtime: "host",
        brief: false,
        model: null,
        branch_mode: "auto",
        base_ref: null,
        skip_init: false,
        agent_cwds: [],
        agent_cwd: null,
      }),
    );

    app.get("/usage/summary", (_req, res) => res.json(FIXTURE_USAGE.summary));
    app.get("/usage/activity", (_req, res) => res.json(FIXTURE_USAGE.activity));
    app.get("/usage/quotas", (_req, res) => res.json(FIXTURE_USAGE.quotas));
    app.get("/usage/sessions", (_req, res) => res.json(FIXTURE_USAGE.sessions));
    app.get("/usage/breakdowns", (_req, res) =>
      res.json(FIXTURE_USAGE.breakdowns),
    );
    app.get("/usage/findings", (_req, res) => res.json(FIXTURE_USAGE.findings));

    // Both are ARRAYS on the wire. An object here throws `.map is not a
    // function` inside the create dialog — which is how this was found.
    app.get("/agents", (_req, res) => res.json(FIXTURE_AGENTS));
    app.get("/branches", (_req, res) => res.json(FIXTURE_BRANCHES));

    // An SSE endpoint that stays OPEN and silent. Closing it would make every
    // page's stream hook flip to its error path and mask a real regression.
    app.get("/events", (_req, res) => {
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        connection: "keep-alive",
      });
      res.write(
        `event: snapshot\ndata: ${JSON.stringify(FIXTURE_ACTIVITY)}\n\n`,
      );
    });

    // Unmodelled routes answer with the daemon's real typed envelope rather
    // than a blanket 200 — a permissive catch-all turns server regressions
    // into green tests.
    app.use((req, res) => {
      res
        .status(404)
        .json({
          detail: {
            error: "not_found",
            message: `fake daemon has no route for ${req.path}`,
          },
        });
    });

    resolve(app.listen(port, "127.0.0.1", () => undefined));
  });
}
