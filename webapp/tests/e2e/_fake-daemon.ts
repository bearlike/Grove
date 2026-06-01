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

    const server = app.listen(port, "127.0.0.1", () => resolve(server));
  });
}
