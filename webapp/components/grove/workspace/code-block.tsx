"use client";

import { Suspense, lazy, type ComponentPropsWithoutRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * `react-syntax-highlighter` + refractor + Prism is ~785 KiB, and every call
 * site (`tool-call-part.tsx`, `compaction-boundary.tsx`, `data-parts.tsx`)
 * mounts `CodeBlock` only inside a Radix `CollapsibleContent`, which
 * unmounts its children while closed. Importing the highlighter eagerly put
 * that whole chunk on the workspace route's critical path to render, in the
 * ordinary case, nothing — on a 1,655-message transcript, 1,050 expanders
 * sit closed and only 9 are open. `lazy()` moves WHEN the chunk downloads
 * (to the first actual expand) — it does NOT shrink it; the 785 KiB is
 * unchanged, it just leaves the initial bundle.
 *
 * `React.lazy`/`Suspense` directly, not `next/dynamic`'s `ssr`/`loading`
 * options: `next/dynamic`'s `loading` slot renders with no access to the
 * wrapped component's own props (only `{ isLoading, pastDelay, error }`), so
 * it cannot render this block's actual `code` while the chunk is in flight —
 * only a spinner or a fixed placeholder, and neither is acceptable here (see
 * `RawCode` below). `next/dynamic`'s `ssr: false` would also be a no-op:
 * `CodeBlock` only ever renders behind a `useState(false)`-initialised
 * disclosure, so on every server render the enclosing `CollapsibleContent`
 * is already unmounted and this file never reaches the server at all.
 */
const HighlightedBody = lazy(() =>
  Promise.all([
    import("react-syntax-highlighter"),
    import("react-syntax-highlighter/dist/esm/languages/prism/bash"),
    import("react-syntax-highlighter/dist/esm/languages/prism/javascript"),
    import("react-syntax-highlighter/dist/esm/languages/prism/markdown"),
    import("@/components/assistant-ui/syntax-highlighter"),
  ]).then(([{ PrismAsyncLight }, bash, javascript, markdown, { SyntaxHighlighter }]) => {
    /**
     * `registerLanguage` mutates a module-singleton registry on the shared
     * `react-syntax-highlighter` package; doing it here, inside the lazy
     * loader, means it only runs once this chunk has actually arrived, not
     * at `code-block.tsx`'s own (eager) module-eval time. Still side-effect
     * only and idempotent — safe under Strict Mode's double module
     * evaluation and Fast Refresh alike. `components/assistant-ui/
     * syntax-highlighter.tsx` is vendored verbatim (`registry:check` diffs
     * it byte for byte against upstream) and registers only js/jsx/ts/tsx/
     * python — all its one caller, a fenced markdown code block, ever
     * needed. Importing the SAME singleton here and registering three more
     * names reaches the vendored `SyntaxHighlighter` for free, with no edit
     * to that file.
     */
    PrismAsyncLight.registerLanguage("bash", bash.default);
    PrismAsyncLight.registerLanguage("javascript", javascript.default);
    PrismAsyncLight.registerLanguage("markdown", markdown.default);

    function Highlighted({ code, language }: { code: string; language: string }): ReactNode {
      return (
        <SyntaxHighlighter
          language={language}
          code={code}
          components={{ Pre: WrappedPre, Code: WrappedCode }}
        />
      );
    }
    return { default: Highlighted };
  }),
);

/**
 * A syntax-highlighted body, composing the vendored `SyntaxHighlighter` —
 * the same component `components/assistant-ui/markdown-text.tsx` wires into
 * fenced code blocks — rather than a second highlighting library. That is
 * the failure mode `webapp/CLAUDE.md`'s vendored-verbatim rule exists to
 * prevent: a highlighter is already vendored, so composing it is the only
 * sanctioned path.
 *
 * Until the highlighter chunk arrives, `RawCode` shows the same text in the
 * same monospace box immediately — never a spinner, never an empty box —
 * and Prism's colour swaps in on top with no layout shift once it loads.
 */
export function CodeBlock({ code, language }: { code: string; language: string }): ReactNode {
  return (
    <Suspense fallback={<RawCode code={code} />}>
      <HighlightedBody code={code} language={language} />
    </Suspense>
  );
}

/**
 * Mirrors the highlighted `<pre>`'s box exactly — same padding/width/margin
 * as the vendored `syntax-highlighter.tsx`'s `customStyle`
 * (`{ margin: 0, width: "100%", padding: "1.5rem 1rem" }`, i.e. `m-0 w-full
 * py-6 px-4`) plus `WrappedPre`'s own whitespace classes — so swapping in
 * the highlighted version changes colour only, never geometry. These values
 * are copied rather than imported (that object isn't exported, and
 * shouldn't be — the vendored file stays untouched); if it ever changes,
 * this needs a matching edit, and nothing else will catch the drift.
 */
function RawCode({ code }: { code: string }): ReactNode {
  return (
    <pre className="m-0 w-full py-6 px-4 text-xs wrap-break-word whitespace-pre-wrap">
      <code>{code}</code>
    </pre>
  );
}

/**
 * `Pre`/`Code` below are near-bare pass-throughs: every pixel of colour (the
 * Prism theme's background and per-token hues) arrives as the vendored
 * component's own inline `style`, never a Tailwind class, so this file adds
 * none — `lint:styling` forbids color/radius/shadow utilities under
 * `components/grove/`, and there is nothing here to forbid. The one class
 * added is whitespace, which is layout, not a look.
 *
 * WRAPS rather than scrolls. Both call sites — a tool call's command, a
 * sub-agent's markdown report — are read top to bottom; a command whose
 * expander scrolled while the plain-text argument fields beside it (still
 * `ToolFallbackArgs`, `wrap-break-word`) wrapped would read as an
 * inconsistency in the row rather than a decision about it. Long lines wrap
 * at the same word/character boundaries the rest of the transcript uses, so
 * nothing is silently clipped.
 */
function WrappedPre({
  node: _node,
  className,
  ...props
}: ComponentPropsWithoutRef<"pre"> & { node?: unknown }): ReactNode {
  return (
    <pre className={cn(className, "text-xs wrap-break-word whitespace-pre-wrap")} {...props} />
  );
}

function WrappedCode({ node: _node, ...props }: ComponentPropsWithoutRef<"code"> & { node?: unknown }): ReactNode {
  return <code {...props} />;
}
