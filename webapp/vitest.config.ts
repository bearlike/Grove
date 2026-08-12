import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  esbuild: { jsx: "automatic", jsxImportSource: "react" },
  test: {
    // `node`, not `jsdom`: the value in this suite is the PURE adapter layer,
    // and a DOM would quietly permit a React import to creep into it. A `.tsx`
    // case is still allowed, but only through `renderToStaticMarkup` — server
    // output, no DOM — which is enough to pin what a component renders from a
    // given wire shape without inviting interaction tests here.
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/unit/**/*.test.ts?(x)"],
    exclude: ["tests/e2e/**", "node_modules/**", ".next/**"],
  },
  resolve: {
    alias: [{ find: "@", replacement: path.resolve(import.meta.dirname, ".") }],
  },
});
