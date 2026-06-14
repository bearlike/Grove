"use client";

// Vendored from Vercel AI Elements (registry.ai-sdk.dev/prompt-input), now
// house code — slimmed hard. Upstream is a 40 kB compound (attachments, model
// selector, command menu, hover cards) typed against the `ai` package, which
// must never enter package.json. Grove's composer is a textarea + actions row,
// so only that survives; the status union is retyped locally (mirror of the AI
// SDK's `ChatStatus` *shape*, owned here). The textarea is styled inline
// rather than via a `components/ui/textarea.tsx` primitive: it exists only
// inside this vendored compound, and `shadcn add` needs network we don't
// assume — promote it to ui/ if a second consumer ever appears.

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Loader2Icon, SendIcon, SquareIcon, XIcon } from "lucide-react";
import type { ComponentProps, FormHTMLAttributes, KeyboardEventHandler } from "react";

/** Local composer status union — drives the submit button's icon only. */
export type PromptInputStatus = "ready" | "submitted" | "streaming" | "error";

export type PromptInputProps = FormHTMLAttributes<HTMLFormElement>;

export const PromptInput = ({ className, ...props }: PromptInputProps) => (
  <form
    className={cn(
      "w-full divide-y divide-border overflow-hidden rounded-md border border-border bg-background",
      className,
    )}
    {...props}
  />
);

export type PromptInputTextareaProps = ComponentProps<"textarea">;

export const PromptInputTextarea = ({
  className,
  onKeyDown,
  placeholder = "Send a message…",
  ...props
}: PromptInputTextareaProps) => {
  // Enter submits, Shift+Enter inserts a newline — the chat-composer contract.
  const handleKeyDown: KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    onKeyDown?.(e);
    if (e.defaultPrevented) return;
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      e.currentTarget.form?.requestSubmit();
    }
  };

  return (
    <textarea
      className={cn(
        "max-h-40 min-h-16 w-full resize-none bg-transparent p-3 text-sm text-foreground",
        "placeholder:text-muted-foreground focus-visible:outline-none",
        className,
      )}
      onKeyDown={handleKeyDown}
      placeholder={placeholder}
      rows={2}
      {...props}
    />
  );
};

export type PromptInputToolbarProps = ComponentProps<"div">;

export const PromptInputToolbar = ({ className, ...props }: PromptInputToolbarProps) => (
  <div className={cn("flex items-center justify-end gap-1 p-1", className)} {...props} />
);

export type PromptInputSubmitProps = ComponentProps<typeof Button> & {
  status?: PromptInputStatus;
};

export const PromptInputSubmit = ({
  className,
  status = "ready",
  children,
  ...props
}: PromptInputSubmitProps) => {
  let Icon = <SendIcon className="size-4" />;
  if (status === "submitted") Icon = <Loader2Icon className="size-4 animate-spin" />;
  else if (status === "streaming") Icon = <SquareIcon className="size-4" />;
  else if (status === "error") Icon = <XIcon className="size-4" />;

  return (
    <Button
      aria-label="Send message"
      size="icon-sm"
      type="submit"
      variant="ghost"
      className={className}
      {...props}
    >
      {children ?? Icon}
    </Button>
  );
};
