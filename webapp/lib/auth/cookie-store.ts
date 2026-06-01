/**
 * Server-side cookie ↔ daemon-token mapping.
 *
 * The browser holds an opaque cookie id (32 random bytes, base64url).
 * The Next.js server keeps a map from cookie id → daemon bearer token,
 * persisted to ``~/.config/grove/webapp-sessions.json`` so a process
 * restart doesn't log everyone out. The browser NEVER sees the daemon
 * token; it only ever sends its cookie id.
 *
 * Persisted entries also carry the daemon-side session id, so the
 * cookie store can issue a logout that revokes the daemon session
 * (defense in depth — rotating the cookie alone leaves the daemon
 * session valid).
 *
 * Single class, methods on the state. Atomic write pattern (write
 * tmp + rename) so a crash mid-save can't corrupt the file.
 */
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { randomBytes } from "node:crypto";

const COOKIE_ID_BYTES = 32;
const FILE_VERSION = 1;

/** Resolve the on-disk path. Mirrors `grove.core.paths.user_webapp_sessions_path`. */
function defaultPath(): string {
  // Use ``$XDG_CONFIG_HOME`` if set (linux), else ``~/.config`` (Linux fallback)
  // — macOS / Windows users running the webapp directly will fall back to
  // their HOME-based config dir; production webapp deployments live on Linux
  // hosts via systemd, so the XDG path is the load-bearing case.
  const xdg = process.env.XDG_CONFIG_HOME;
  const base = xdg && xdg.length > 0 ? xdg : join(homedir(), ".config");
  return join(base, "grove", "webapp-sessions.json");
}

export interface CookieEntry {
  cookieId: string;
  /** Daemon bearer token. Server-only — never serialized to the browser. */
  daemonToken: string;
  /** Daemon session id; used to revoke server-side on logout. */
  sessionId: string;
  label: string;
  expiresAt: string; // ISO timestamp
  createdAt: string;
}

interface FileShape {
  version: number;
  entries: CookieEntry[];
}

export class CookieStore {
  private readonly path: string;
  private inMem: Map<string, CookieEntry> = new Map();
  private loaded = false;
  private writeQueue: Promise<void> = Promise.resolve();

  constructor(path?: string) {
    this.path = path ?? defaultPath();
  }

  /** Lazily load on first access. */
  private async ensureLoaded(): Promise<void> {
    if (this.loaded) return;
    try {
      const raw = await readFile(this.path, "utf-8");
      const parsed = JSON.parse(raw) as FileShape;
      if (parsed?.version === FILE_VERSION && Array.isArray(parsed.entries)) {
        // Drop expired entries on load — bounds the file size + ensures a
        // stale cookie can't masquerade as fresh after a server restart.
        const now = new Date();
        for (const entry of parsed.entries) {
          if (new Date(entry.expiresAt) > now) {
            this.inMem.set(entry.cookieId, entry);
          }
        }
      }
    } catch (err) {
      // Missing file = empty store. Anything else (corrupt / permission
      // denied) → fail closed: empty store, surface a console warning.
      const e = err as NodeJS.ErrnoException;
      if (e.code !== "ENOENT") {
        // eslint-disable-next-line no-console
        console.warn(`[grove auth] cookie store at ${this.path} unreadable:`, e.message);
      }
    }
    this.loaded = true;
  }

  /** Mint a new cookie id; persist + return the cookie id only.
   * Daemon token + session id stored server-side only. */
  async issue(args: {
    daemonToken: string;
    sessionId: string;
    label: string;
    expiresAt: string;
  }): Promise<string> {
    await this.ensureLoaded();
    const cookieId = randomBytes(COOKIE_ID_BYTES).toString("base64url");
    const entry: CookieEntry = {
      cookieId,
      daemonToken: args.daemonToken,
      sessionId: args.sessionId,
      label: args.label,
      expiresAt: args.expiresAt,
      createdAt: new Date().toISOString(),
    };
    this.inMem.set(cookieId, entry);
    await this.flush();
    return cookieId;
  }

  /** Look up the entry for a cookie id. Returns null for missing / expired. */
  async lookup(cookieId: string): Promise<CookieEntry | null> {
    await this.ensureLoaded();
    const entry = this.inMem.get(cookieId);
    if (!entry) return null;
    if (new Date(entry.expiresAt) <= new Date()) {
      this.inMem.delete(cookieId);
      await this.flush();
      return null;
    }
    return entry;
  }

  /** Drop the cookie locally. Caller is responsible for the daemon revoke. */
  async revoke(cookieId: string): Promise<void> {
    await this.ensureLoaded();
    if (this.inMem.delete(cookieId)) {
      await this.flush();
    }
  }

  /** Serialize + atomic-write. Serialized through ``writeQueue`` so concurrent
   *  ``flush()`` calls don't race the rename. */
  private async flush(): Promise<void> {
    const payload: FileShape = {
      version: FILE_VERSION,
      entries: Array.from(this.inMem.values()),
    };
    this.writeQueue = this.writeQueue.then(async () => {
      const text = JSON.stringify(payload, null, 2) + "\n";
      const tmp = `${this.path}.tmp`;
      await mkdir(dirname(this.path), { recursive: true });
      await writeFile(tmp, text, { encoding: "utf-8", mode: 0o600 });
      await rename(tmp, this.path);
    });
    return this.writeQueue;
  }
}

let _shared: CookieStore | null = null;

/** Process-wide singleton — mounted lazily so ``import`` doesn't touch disk. */
export function sharedCookieStore(): CookieStore {
  if (_shared === null) {
    _shared = new CookieStore();
  }
  return _shared;
}

/** Public for tests only. Resets the singleton between cases. */
export function _resetSharedCookieStore(): void {
  _shared = null;
}

export const COOKIE_NAME = "grove_session";
