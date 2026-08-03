"use client";

// Vendored from Vercel AI Elements (registry.ai-sdk.dev), now house code — see
// webapp/CLAUDE.md "AI Elements" lesson. Upstream folded this into message.tsx
// as `MessageResponse`; we keep it a separate file because `streamdown` is the
// chat panel's entire bundle cost and this is the only module that imports it
// (the panel is next/dynamic-loaded so `/` and `/activity` pay nothing).
// `streamdown/styles.css` carries the sd-* keyframes; the utility classes
// inside streamdown's dist are scanned via `@source` in app/globals.css.

import { cn } from "@/lib/utils";
import { type ComponentProps, memo } from "react";
import { Streamdown, type ControlsConfig } from "streamdown";
import "streamdown/styles.css";

export type ResponseProps = ComponentProps<typeof Streamdown>;

// Fenced code renders through streamdown's native CodeBlock (shiki highlighting
// intact); markdown tables through its native table components. Its stock chrome
// affordances are anti-patterns for a calm transcript and can't be reached by
// CSS, so we kill them at the real API: the code DOWNLOAD button
// (`controls.code.download`), the per-line number gutters (`lineNumbers`), and
// the entire TABLE controls row — copy/download/fullscreen (`controls.table`),
// which the assistant-ui table grammar doesn't carry. Code copy
// stays. Module-level so the object identity is stable — Streamdown memoizes on
// its props. Mermaid controls are left at default.
const STREAMDOWN_CONTROLS: ControlsConfig = {
  code: { copy: true, download: false },
  table: false,
};

export const Response = memo(
  ({ className, ...props }: ResponseProps) => (
    <Streamdown
      className={cn(
        // Body prose reads at Grove's `--text-prose` scale (16px / 1.6) — the
        // surface read for minutes, deliberately larger than the 14px chrome
        // (the user's readability ruling; the upstream template's `text-base`
        // agrees). The markdown element map below mirrors assistant-ui's own
        // styled template (packages/ui markdown-text.tsx), so agent prose reads
        // modern-chat-native.
        "size-full text-prose [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        "[&_p]:my-3 [&_p]:leading-relaxed [&_li]:leading-relaxed",
        // Links ride terracotta-AS-TEXT (`--primary-fg`), never bare `--primary`
        // (a text-bearing `--primary` fails AA — design-system.md terracotta roles).
        "[&_a]:text-primary-fg [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-primary-fg/80",
        // Inline code — a soft muted pill (scoped to code NOT inside a <pre> so
        // fenced blocks keep their own well below).
        "[&_:not(pre)>code]:rounded-md [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1.5 [&_:not(pre)>code]:py-0.5 [&_:not(pre)>code]:font-mono [&_:not(pre)>code]:text-[0.85em]",
        // Fenced code — flatten streamdown's stock card-within-a-card (a
        // bg-sidebar chrome shell wrapping a nested bg-background body, double
        // borders) into the assistant-ui single well. Download + line
        // numbers are gone via CODE_CONTROLS/lineNumbers on <Streamdown>; these
        // attribute overrides re-tone the surfaces the props can't reach. The
        // (0,2,0) variant+attribute specificity beats streamdown's own (0,1,0)
        // utility classes on the element, so they win without !important.
        // Container: strip the chrome shell so the header + body ARE the block.
        "[&_[data-streamdown=code-block]]:my-4 [&_[data-streamdown=code-block]]:gap-0 [&_[data-streamdown=code-block]]:rounded-none [&_[data-streamdown=code-block]]:border-0 [&_[data-streamdown=code-block]]:bg-transparent [&_[data-streamdown=code-block]]:p-0",
        // Header: a slim muted cap, hairline sans its bottom edge so it fuses to
        // the body into one continuous well.
        "[&_[data-streamdown=code-block-header]]:rounded-t-xl [&_[data-streamdown=code-block-header]]:border [&_[data-streamdown=code-block-header]]:border-border/50 [&_[data-streamdown=code-block-header]]:border-b-0 [&_[data-streamdown=code-block-header]]:bg-muted/50 [&_[data-streamdown=code-block-header]]:px-3",
        // Copy control: dissolve the floating blurred pill and lift the (already
        // quiet, muted→foreground) button onto the header's right edge — no
        // sticky, no chrome. The wrapper is the div that parents the actions.
        "[&_div:has(>[data-streamdown=code-block-actions])]:static [&_div:has(>[data-streamdown=code-block-actions])]:-mt-8 [&_div:has(>[data-streamdown=code-block-actions])]:pr-1.5",
        "[&_[data-streamdown=code-block-actions]]:rounded-none [&_[data-streamdown=code-block-actions]]:border-0 [&_[data-streamdown=code-block-actions]]:bg-transparent [&_[data-streamdown=code-block-actions]]:p-0 [&_[data-streamdown=code-block-actions]]:backdrop-blur-none [&_[data-streamdown=code-block-actions]]:supports-[backdrop-filter]:bg-transparent",
        // Body: the single well — muted fill, one hairline continuing the
        // header, no rounding at the seam, at the 13px mono code scale. Keeps
        // streamdown's own overflow-x-auto for wide lines.
        "[&_[data-streamdown=code-block-body]]:rounded-t-none [&_[data-streamdown=code-block-body]]:rounded-b-xl [&_[data-streamdown=code-block-body]]:border [&_[data-streamdown=code-block-body]]:border-border/50 [&_[data-streamdown=code-block-body]]:bg-muted/30 [&_[data-streamdown=code-block-body]]:p-3.5 [&_[data-streamdown=code-block-body]]:font-mono [&_[data-streamdown=code-block-body]]:text-[13px]",
        // Markdown tables — flatten the SAME card-within-a-card streamdown wraps
        // them in (a bg-sidebar shell around a nested bg-background bordered
        // scroll card, double borders) into the assistant-ui single frame.
        // The copy/download/fullscreen controls row is gone via
        // STREAMDOWN_CONTROLS.table above; these overrides re-tone the surfaces
        // the prop can't reach (same (0,2,x) specificity win as the code block).
        // Wrapper: strip the chrome shell so the framed table IS the block.
        "[&_[data-streamdown=table-wrapper]]:my-4 [&_[data-streamdown=table-wrapper]]:gap-0 [&_[data-streamdown=table-wrapper]]:rounded-none [&_[data-streamdown=table-wrapper]]:border-0 [&_[data-streamdown=table-wrapper]]:bg-transparent [&_[data-streamdown=table-wrapper]]:p-0",
        // The scroll div that parents the <table> becomes the ONE rounded,
        // hairline frame — no bg tone of its own, keeping streamdown's
        // overflow-x-auto for wide tables (rounded corners clip the header cap).
        "[&_div:has(>[data-streamdown=table])]:rounded-lg [&_div:has(>[data-streamdown=table])]:border-border/50 [&_div:has(>[data-streamdown=table])]:bg-transparent",
        // Header cells read on a solid muted cap; the row + header/body dividers
        // drop to the border/50 hairline.
        "[&_[data-streamdown=table-header]]:bg-muted [&_[data-streamdown=table]]:divide-border/50 [&_[data-streamdown=table-body]]:divide-border/50",
        className,
      )}
      controls={STREAMDOWN_CONTROLS}
      lineNumbers={false}
      {...props}
    />
  ),
  // Markdown re-renders are expensive (block parse per change); a digest entry's
  // text is immutable once written, so identity on `children` is the contract.
  (prevProps, nextProps) => prevProps.children === nextProps.children,
);
Response.displayName = "Response";
