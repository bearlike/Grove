import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { code } from "./_source";

const layout = readFileSync("app/layout.tsx", "utf8");
const providers = readFileSync("components/grove/providers.tsx", "utf8");
const sonner = readFileSync("components/ui/sonner.tsx", "utf8");

/**
 * ONE HOST, pinned as a cross-FILE census — the defect lived in the
 * relationship, not in either file. `app/layout.tsx` and
 * `components/grove/providers.tsx` each mounted a `<Toaster>`, each correct on
 * its own, so every toast rendered and was announced twice. Sonner draws no
 * host at all until something is toasted, which is why an idle page looked
 * identical either way and why nothing caught this.
 *
 * The behavioural half — that one real event produces exactly one visible
 * toast — is `tests/e2e/workspace-composer.spec.ts`. This half is what stops a
 * second mount coming back.
 */
describe("the app hosts exactly one toast layer", () => {
  it("mounts the Toaster in Providers and nowhere else", () => {
    expect(providers).toContain("<Toaster");
    const layoutCode = code("app/layout.tsx");
    expect(layoutCode).not.toContain("<Toaster");
    expect(layoutCode).not.toContain('from "@/components/ui/sonner"');
  });

  it("keeps that one host theme-aware, bottom-right and closeable", () => {
    // Inside the theme provider, or it falls back to the system preference
    // instead of following an explicit choice.
    expect(providers.indexOf("<ThemeProvider")).toBeLessThan(providers.indexOf("<Toaster"));
    expect(providers).toContain('position="bottom-right"');
    expect(providers).toContain("closeButton");
    expect(sonner).toContain("useTheme");
  });
});

describe("toast typography comes from the app's ramp", () => {
  /**
   * Sonner injects its stylesheet UNLAYERED at runtime, and an unlayered rule
   * outranks every Tailwind utility regardless of specificity — so a class on
   * the title or description is silently inert, which is indistinguishable
   * from a class that works. Inline style on the toast root is the one
   * declaration that beats it, and the children inherit from there.
   */
  it("configures the shared size through toastOptions.style, not a class", () => {
    expect(providers).toContain("toastOptions");
    expect(providers).toMatch(/fontSize:\s*"var\(--text-sm\)"/);
    expect(code("components/grove/providers.tsx")).not.toContain("classNames");
  });

  it("uses a ramp token rather than a pixel literal, so zoom still scales it", () => {
    const options = providers.slice(providers.indexOf("toastOptions"));
    expect(options.slice(0, 200)).not.toMatch(/\d+px/);
  });

  it("leaves the vendored host untouched", () => {
    // Every icon is Lucide's, and the file takes caller config through props —
    // there is nothing here that needs editing to restyle a toast.
    expect(sonner).toContain('from "lucide-react"');
    expect(sonner).toContain("{...props}");
  });
});
