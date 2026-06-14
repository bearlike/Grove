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
 *
 * **The file is the source of truth; memory is just a cache.** Each
 * mutation is a single-entry delta applied to a freshly-read disk set,
 * NOT a dump of this process's in-memory map. This is the fix for the
 * repeated-re-pairing bug: ``next start`` runs multiple worker
 * processes, each with its own ``inMem``; a stale full-memory dump from
 * worker B would silently delete the cookie worker A just minted (and a
 * naive merge-on-flush would resurrect revoked/expired entries instead).
 * A targeted delta against fresh disk does neither. ``lookup`` re-reads
 * on a miss to self-heal a mint from a sibling worker. Residual: two
 * *simultaneous* ``issue()``s from different processes inside one
 * read→write window can still lose one — acceptable (pairing is rare and
 * user-driven), and far better than the unconditional stale-memory dump.
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

/** Whether the request reached us over HTTPS, honoring a TLS-terminating
 *  reverse proxy. Behind such a proxy the inbound request is plain HTTP, so
 *  `nextUrl.protocol` is `http:` even though the browser is on HTTPS — trust
 *  `x-forwarded-proto` (first value if comma-listed) when present. */
export function isSecureRequest(forwardedProto: string | null, urlProtocol: string): boolean {
  const proto = (forwardedProto?.split(",")[0] ?? urlProtocol).trim().replace(/:$/, "").toLowerCase();
  return proto === "https";
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

  /** Read + parse the file, dropping expired entries. The file is the source
   *  of truth, so every mutation reads through here for a fresh set rather
   *  than trusting cached memory that a sibling worker may have moved past.
   *  Missing file (ENOENT) → empty map; any other error → empty map + warn. */
  private async readDisk(): Promise<Map<string, CookieEntry>> {
    const entries = new Map<string, CookieEntry>();
    try {
      const raw = await readFile(this.path, "utf-8");
      const parsed = JSON.parse(raw) as FileShape;
      if (parsed?.version === FILE_VERSION && Array.isArray(parsed.entries)) {
        // Drop expired entries on load — bounds the file size + ensures a
        // stale cookie can't masquerade as fresh after a server restart.
        const now = new Date();
        for (const entry of parsed.entries) {
          if (new Date(entry.expiresAt) > now) {
            entries.set(entry.cookieId, entry);
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
    return entries;
  }

  /** Serialize + atomic-write (tmp + rename) so a crash mid-save can't
   *  corrupt the file. */
  private async write(entries: Map<string, CookieEntry>): Promise<void> {
    const payload: FileShape = {
      version: FILE_VERSION,
      entries: Array.from(entries.values()),
    };
    const text = JSON.stringify(payload, null, 2) + "\n";
    const tmp = `${this.path}.tmp`;
    await mkdir(dirname(this.path), { recursive: true });
    await writeFile(tmp, text, { encoding: "utf-8", mode: 0o600 });
    await rename(tmp, this.path);
  }

  /** Apply a single-entry delta to fresh disk, atomically. Chained on
   *  ``writeQueue`` so same-process mutations don't interleave their
   *  read/write windows; ``apply`` mutates the just-read set (NOT cached
   *  memory) so a sibling worker's freshly-minted cookies survive and
   *  revoked/expired entries are never resurrected. */
  private async mutate(apply: (entries: Map<string, CookieEntry>) => void): Promise<void> {
    this.writeQueue = this.writeQueue.then(async () => {
      const entries = await this.readDisk();
      apply(entries);
      // Cache now matches what we're about to write.
      this.inMem = entries;
      this.loaded = true;
      await this.write(entries);
    });
    return this.writeQueue;
  }

  /** Lazily load on first access. */
  private async ensureLoaded(): Promise<void> {
    if (this.loaded) return;
    this.inMem = await this.readDisk();
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
    const cookieId = randomBytes(COOKIE_ID_BYTES).toString("base64url");
    const entry: CookieEntry = {
      cookieId,
      daemonToken: args.daemonToken,
      sessionId: args.sessionId,
      label: args.label,
      expiresAt: args.expiresAt,
      createdAt: new Date().toISOString(),
    };
    await this.mutate((e) => e.set(cookieId, entry));
    return cookieId;
  }

  /** Look up the entry for a cookie id. Returns null for missing / expired.
   *  On a cache miss, re-reads disk to self-heal a mint from a sibling
   *  worker process before giving up. */
  async lookup(cookieId: string): Promise<CookieEntry | null> {
    await this.ensureLoaded();
    let entry = this.inMem.get(cookieId);
    if (!entry) {
      // Cache miss: a sibling worker may have just minted this cookie. Re-read
      // disk (the source of truth) and re-check before declaring it unknown.
      this.inMem = await this.readDisk();
      entry = this.inMem.get(cookieId);
      if (!entry) return null;
    }
    if (new Date(entry.expiresAt) <= new Date()) {
      await this.mutate((e) => e.delete(cookieId));
      return null;
    }
    return entry;
  }

  /** Drop the cookie locally. Caller is responsible for the daemon revoke. */
  async revoke(cookieId: string): Promise<void> {
    await this.mutate((e) => e.delete(cookieId));
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
