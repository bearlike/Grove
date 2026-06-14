"use client";

// Vendored from Vercel AI Elements (registry.ai-sdk.dev/message), now house
// code — slimmed to the pieces Grove's chat actually renders. The upstream
// file types `from` as the AI SDK's `UIMessage["role"]` and carries branch /
// attachment / toolbar machinery that needs the `ai` package; all of that is
// dropped and the role is retyped against our wire union instead (`ai` must
// never enter package.json — see webapp/CLAUDE.md).

import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

/** Conversational roles the chat renders as bubbles — a subset of the wire's
 * `DigestEntryView["role"]` (tool/summary/status get their own row shapes). */
export type ChatMessageRole = "user" | "assistant";

export type MessageProps = HTMLAttributes<HTMLDivElement> & {
  from: ChatMessageRole;
};

export const Message = ({ className, from, ...props }: MessageProps) => (
  <div
    className={cn(
      "group flex w-full max-w-[95%] flex-col gap-2",
      from === "user" ? "is-user ml-auto justify-end" : "is-assistant",
      className,
    )}
    {...props}
  />
);

export type MessageContentProps = HTMLAttributes<HTMLDivElement>;

export const MessageContent = ({ children, className, ...props }: MessageContentProps) => (
  <div
    className={cn(
      // break-words deviation from upstream: an unbroken token (URL, hash,
      // minified line) must wrap inside the bubble — overflow-hidden alone
      // would clip it silently, and without either it widens the page.
      "flex w-fit min-w-0 max-w-full flex-col gap-2 overflow-hidden break-words text-sm",
      "group-[.is-user]:ml-auto group-[.is-user]:rounded-lg group-[.is-user]:bg-secondary group-[.is-user]:px-3 group-[.is-user]:py-2 group-[.is-user]:text-foreground",
      "group-[.is-assistant]:text-foreground",
      className,
    )}
    {...props}
  >
    {children}
  </div>
);
