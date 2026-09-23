"use client";

import { GroveClient } from "@/lib/grove/api";
import {
  DEMO_CONTROLS,
  DEMO_DIAGRAM_XML_URL,
  DEMO_DIFF,
  DEMO_HISTORY,
  DEMO_PEEK,
  DEMO_PHASE,
  DEMO_PROVIDERS,
  DEMO_QUEUE,
  DEMO_REPO_ROOT,
  DEMO_STATE,
  DEMO_TICKETS,
  DEMO_TODO,
  DEMO_TURNS,
  DEMO_WATCHES,
  DEMO_WORKSPACE_ID,
  demoDiagram,
  withDemoWorkspace,
} from "./demo-workspace";

/**
 * Serve the demo workspace from the browser while the tour is open.
 *
 * WHY AN INTERCEPTOR AND NOT A DAEMON ROUTE. The demo is a presentation
 * concern of one client: the daemon must not learn a fictional workspace, and
 * the react-query cache must not be hand-seeded per hook (thirteen keys, each
 * with its own staleness and invalidation, and the SSE reducer would drop the
 * seed on the next frame). The seam every read in this app already shares is
 * `GroveClient.basePath` — one `fetch` prefix — so answering there is one
 * decision that reaches every hook, every future hook, and the stream.
 *
 * WHAT IT ANSWERS. Only requests naming `DEMO_WORKSPACE_ID` or `DEMO_REPO_ROOT`
 * (and the two catalog reads that must include it: `/activity` and `/events`).
 * Every other request — the real fleet, the real usage page, a real workspace
 * the reader opens mid-tour — passes through untouched, so nothing a reader
 * sees outside the demo is invented. Writes to the demo answer 204 and change
 * nothing, so "send" in the demo composer is a harmless no-op rather than a
 * message to a daemon that has never heard of the workspace.
 *
 * WHAT IT NEVER DOES. It does not patch `window.fetch` for the page's life:
 * `install()` returns the uninstaller and the tour calls it on close, and the
 * originals are captured once so two installs cannot stack (the doubling trap
 * `addInitScript` is documented for in CLAUDE.md applies to any wrapper).
 */
export function installDemoInterceptor(): () => void {
  if (typeof window === "undefined") return () => undefined;
  const realFetch = window.fetch;
  const RealEventSource = window.EventSource;
  const prefix = GroveClient.basePath;

  window.fetch = async (input, init) => {
    const url = requestUrl(input);
    const demo = url ? answerFor(url, init?.method ?? "GET", realFetch) : null;
    return demo ?? realFetch(input, init);
  };

  // The workspace's pane stream: a fake `EventSource` that emits one frame and
  // stays open. Everything else (`/events`) is the real stream, wrapped so its
  // fleet snapshots carry the demo project too.
  const FakeEventSource = function (this: EventSource, url: string | URL, eventSourceInit?: EventSourceInit) {
    const href = String(url);
    if (href.startsWith(`${prefix}/workspaces/${DEMO_WORKSPACE_ID}/pane/stream`)) {
      return paneStream(href);
    }
    const real = new RealEventSource(url, eventSourceInit);
    if (href.startsWith(`${prefix}/events`)) return spliceSnapshots(real);
    return real;
  } as unknown as typeof EventSource;
  FakeEventSource.prototype = RealEventSource.prototype;
  Object.assign(FakeEventSource, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });
  window.EventSource = FakeEventSource;

  return () => {
    window.fetch = realFetch;
    window.EventSource = RealEventSource;
  };
}

function requestUrl(input: RequestInfo | URL): string | null {
  const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const prefix = GroveClient.basePath;
  const path = href.startsWith("http") ? new URL(href).pathname + new URL(href).search : href;
  return path.startsWith(prefix) ? path.slice(prefix.length) : null;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** The demo's answer for a BFF path, or null to pass the request through. */
function answerFor(path: string, method: string, realFetch: typeof fetch): Promise<Response> | null {
  const [pathname, search = ""] = path.split("?");
  const params = new URLSearchParams(search);
  const ws = `/workspaces/${DEMO_WORKSPACE_ID}`;

  // Catalog reads that must INCLUDE the demo: fetch the real thing and splice.
  if (pathname === "/activity" && method === "GET") {
    return realFetch(`${GroveClient.basePath}/activity`).then(async (response) => {
      if (!response.ok) return response;
      return json(withDemoWorkspace(await response.json()));
    });
  }

  // Repo-scoped reads the demo answers for ITS repo only: the ticket routes
  // (resolution and the provider list) and the share policy. Anything else
  // that carries `?repo=` (`/agents`, `/branches`, `/defaults`) is the landing
  // page's business and passes through — the demo repo is only ever named
  // by the workspace page.
  if (params.get("repo") === DEMO_REPO_ROOT && method === "GET") {
    if (pathname === "/tickets/providers") return Promise.resolve(json(DEMO_PROVIDERS));
    if (pathname.startsWith("/tickets/")) {
      const id = decodeURIComponent(pathname.split("/").at(-1) ?? "");
      const ticket = DEMO_TICKETS.find((ref) => ref.id === id);
      return Promise.resolve(ticket ? json(ticket) : json({ detail: { error: "not_found", message: id } }, 404));
    }
    if (pathname === "/share-policy") return Promise.resolve(json({ ttl_seconds: null, passcode_set: false }));
  }

  // Watches are listed host-wide and FILTERED by workspace, so the demo's are
  // named by a query parameter rather than by a path under `ws`.
  if (pathname === "/watches" && params.get("workspace") === DEMO_WORKSPACE_ID && method === "GET") {
    return Promise.resolve(json(DEMO_WATCHES));
  }

  if (!pathname.startsWith(ws)) return null;

  if (method !== "GET") return Promise.resolve(new Response(null, { status: 204 }));

  const sub = pathname.slice(ws.length);
  switch (sub) {
    case "":
      return Promise.resolve(json(DEMO_STATE));
    case "/peek":
      return Promise.resolve(json(DEMO_PEEK));
    case "/commits":
      return Promise.resolve(json(DEMO_PEEK.recent_commits));
    case "/diff":
      return Promise.resolve(json(DEMO_DIFF));
    case "/todo":
      return Promise.resolve(json(DEMO_TODO));
    case "/phase":
      return Promise.resolve(json(DEMO_PHASE));
    case "/controls":
      return Promise.resolve(json(DEMO_CONTROLS));
    case "/queue":
      return Promise.resolve(json(DEMO_QUEUE));
    case "/history":
      return Promise.resolve(json(DEMO_HISTORY));
    case "/panels":
      return Promise.resolve(json([]));
    case "/provision":
      return Promise.resolve(json({ status: "skipped", steps: [] }));
    case "/pane":
      return Promise.resolve(json({ workspace_id: DEMO_WORKSPACE_ID, ansi: DEMO_PEEK.agent_snapshot, taken_at: DEMO_PEEK.snapshot_taken_at }));
    case "/sessions":
      return Promise.resolve(json([DEMO_TURNS.session]));
    case "/diagram":
      // The fixture's XML is a static asset, loaded through the REAL fetch so
      // this shim never answers its own request.
      return realFetch(DEMO_DIAGRAM_XML_URL).then(async (response) => json(demoDiagram(await response.text())));
    case "/diagram/preview":
      return Promise.resolve(json({ detail: { error: "diagram_unavailable", message: "Preview pending" } }, 404));
    default:
      break;
  }
  if (sub.startsWith("/sessions/") && sub.endsWith("/turns")) return Promise.resolve(json(DEMO_TURNS));
  return Promise.resolve(json({ detail: { error: "not_found", message: pathname } }, 404));
}

/** An `EventSource` that delivers one pane frame and then holds. */
function paneStream(href: string): EventSource {
  const target = new EventTarget() as EventSource & { url: string; readyState: number; withCredentials: boolean; close: () => void };
  Object.assign(target, {
    url: href,
    readyState: 1,
    withCredentials: false,
    onopen: null,
    onmessage: null,
    onerror: null,
    close: () => {
      target.readyState = 2;
    },
    CONNECTING: 0,
    OPEN: 1,
    CLOSED: 2,
  });
  const frame = { kind: "pane_snapshot", pane: { workspace_id: DEMO_WORKSPACE_ID, ansi: DEMO_PEEK.agent_snapshot, taken_at: DEMO_PEEK.snapshot_taken_at } };
  window.setTimeout(() => {
    target.dispatchEvent(new Event("open"));
    target.dispatchEvent(new MessageEvent("pane_snapshot", { data: JSON.stringify(frame) }));
  }, 0);
  return target;
}

/**
 * Wrap the real fleet stream so every `snapshot` frame carries the demo
 * project. Listeners are attached to the real source; only the data of the
 * one event type that carries a whole snapshot is rewritten.
 */
function spliceSnapshots(real: EventSource): EventSource {
  const originalAdd = real.addEventListener.bind(real) as (
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions,
  ) => void;
  const add = (
    type: string,
    listener: EventListenerOrEventListenerObject | null,
    options?: boolean | AddEventListenerOptions,
  ): void => {
    if (!listener) return;
    if (type !== "snapshot") return originalAdd(type, listener, options);
    const wrapped = (event: Event): void => {
      const message = event as MessageEvent<string>;
      let data = message.data;
      try {
        const envelope = JSON.parse(message.data) as { snapshot?: Parameters<typeof withDemoWorkspace>[0] };
        if (envelope.snapshot) data = JSON.stringify({ ...envelope, snapshot: withDemoWorkspace(envelope.snapshot) });
      } catch {
        // A frame that is not a snapshot envelope passes through untouched.
      }
      const forwarded = new MessageEvent("snapshot", { data, lastEventId: message.lastEventId });
      if (typeof listener === "function") listener(forwarded);
      else listener.handleEvent(forwarded);
    };
    originalAdd(type, wrapped, options);
  };
  (real as { addEventListener: unknown }).addEventListener = add;
  return real;
}
