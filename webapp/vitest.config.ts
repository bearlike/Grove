import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/unit/**/*.test.ts", "tests/component/**/*.test.tsx"],
    exclude: ["tests/e2e/**", "node_modules/**", ".next/**"],
  },
  resolve: {
    alias: [
      // `@/app/fonts` pulls `next/font/local` (a build-time transform Vitest
      // can't resolve), so swap it for a class-string stub. Must precede the
      // generic `@` alias — Vite matches alias entries in order.
      { find: /^@\/app\/fonts$/, replacement: path.resolve(__dirname, "tests/_helpers/fonts-stub.ts") },
      { find: "@", replacement: path.resolve(__dirname, ".") },
    ],
  },
});
