import { randomBytes } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const COOKIE_ID_BYTES = 32;
const FILE_VERSION = 1;
export const COOKIE_NAME = "grove_session";

export interface CookieEntry {
  cookieId: string;
  daemonToken: string;
  sessionId: string;
  label: string;
  expiresAt: string;
  createdAt: string;
}

type FileShape = { version: number; entries: CookieEntry[] };

function defaultPath(): string {
  const configRoot = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
  return join(configRoot, "grove", "webapp-sessions.json");
}

export function isSecureRequest(forwardedProto: string | null, urlProtocol: string): boolean {
  return (forwardedProto?.split(",")[0] ?? urlProtocol).trim().replace(/:$/, "").toLowerCase() === "https";
}

/** Server-side mapping of opaque browser cookies to daemon tokens. */
export class CookieStore {
  private cache = new Map<string, CookieEntry>();
  private loaded = false;
  private writeQueue: Promise<void> = Promise.resolve();

  constructor(private readonly path = defaultPath()) {}

  async issue(args: Omit<CookieEntry, "cookieId" | "createdAt">): Promise<string> {
    const cookieId = randomBytes(COOKIE_ID_BYTES).toString("base64url");
    const entry: CookieEntry = { ...args, cookieId, createdAt: new Date().toISOString() };
    await this.mutate((entries) => entries.set(cookieId, entry));
    return cookieId;
  }

  async lookup(cookieId: string): Promise<CookieEntry | null> {
    await this.ensureLoaded();
    let entry = this.cache.get(cookieId);
    if (!entry) {
      this.cache = await this.readDisk();
      entry = this.cache.get(cookieId);
    }
    if (!entry) return null;
    if (new Date(entry.expiresAt) <= new Date()) {
      await this.mutate((entries) => entries.delete(cookieId));
      return null;
    }
    return entry;
  }

  async revoke(cookieId: string): Promise<void> {
    await this.mutate((entries) => entries.delete(cookieId));
  }

  private async ensureLoaded(): Promise<void> {
    if (this.loaded) return;
    this.cache = await this.readDisk();
    this.loaded = true;
  }

  private async mutate(apply: (entries: Map<string, CookieEntry>) => void): Promise<void> {
    this.writeQueue = this.writeQueue.then(async () => {
      const entries = await this.readDisk();
      apply(entries);
      this.cache = entries;
      this.loaded = true;
      await this.writeDisk(entries);
    });
    return this.writeQueue;
  }

  private async readDisk(): Promise<Map<string, CookieEntry>> {
    try {
      const parsed = JSON.parse(await readFile(this.path, "utf-8")) as FileShape;
      if (parsed.version !== FILE_VERSION || !Array.isArray(parsed.entries)) return new Map();
      const now = new Date();
      return new Map(parsed.entries.filter((entry) => new Date(entry.expiresAt) > now).map((entry) => [entry.cookieId, entry]));
    } catch (error: unknown) {
      if (this.errorCode(error) !== "ENOENT") console.warn(`[grove auth] cookie store at ${this.path} unreadable`);
      return new Map();
    }
  }

  private async writeDisk(entries: Map<string, CookieEntry>): Promise<void> {
    const temporaryPath = `${this.path}.tmp`;
    await mkdir(dirname(this.path), { recursive: true });
    await writeFile(temporaryPath, `${JSON.stringify({ version: FILE_VERSION, entries: [...entries.values()] } satisfies FileShape, null, 2)}\n`, { encoding: "utf-8", mode: 0o600 });
    await rename(temporaryPath, this.path);
  }

  private errorCode(error: unknown): string | undefined {
    return typeof error === "object" && error !== null && "code" in error && typeof error.code === "string" ? error.code : undefined;
  }
}

let shared: CookieStore | null = null;

export function sharedCookieStore(): CookieStore {
  shared ??= new CookieStore();
  return shared;
}

export function resetSharedCookieStore(): void {
  shared = null;
}
