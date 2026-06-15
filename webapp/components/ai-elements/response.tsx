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
import { Streamdown } from "streamdown";
import "streamdown/styles.css";

export type ResponseProps = ComponentProps<typeof Streamdown>;

export const Response = memo(
  ({ className, ...props }: ResponseProps) => (
    <Streamdown
      className={cn(
        // Body is Geist Sans `text-sm`; ALL code (inline + fenced) rides Geist
        // Mono at the transcript's `text-[13px]` code scale, the same family +
        // size the Tool blocks use — one code voice across the surface.
        "size-full text-sm [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        "[&_code]:font-mono [&_code]:text-[13px] [&_pre]:font-mono [&_pre]:text-[13px]",
        className,
      )}
      {...props}
    />
  ),
  // Markdown re-renders are expensive (block parse per change); a digest entry's
  // text is immutable once written, so identity on `children` is the contract.
  (prevProps, nextProps) => prevProps.children === nextProps.children,
);
Response.displayName = "Response";
