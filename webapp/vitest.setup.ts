import { afterEach, beforeEach, vi } from "vitest";

/**
 * The adapter suite is pure by contract, so the setup file's job is to make a
 * breach of that contract fail loudly rather than to prop a DOM up. A `fetch`
 * from an adapter is a design regression, not a flake.
 *
 * Imported explicitly rather than leaning on `globals: true`, so `tsc` sees
 * them without a `types` entry in the shared tsconfig.
 */
beforeEach(() => {
  vi.stubGlobal("fetch", () => {
    throw new Error("lib/grove/adapters must be pure — no network. Move the I/O into lib/grove/api.");
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
