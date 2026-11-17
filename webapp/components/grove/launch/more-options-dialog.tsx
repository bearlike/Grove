"use client";

import { Suspense, useEffect, useId, useRef, type ReactNode } from "react";
import { usePathname } from "next/navigation";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TICKET_PROVIDERS, type TicketProvider } from "@/components/grove/fleet/create-request";
import { useCreateWorkspaceUi } from "@/components/grove/fleet/create-store";
import { LaunchPillGroup } from "./control-pill";
import { LaunchComposer } from "./launch-composer";
import { useLaunchControls } from "./launch-state";

/** A labelled control. `aria-labelledby` because a field can hold two controls. */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}): ReactNode {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5" role="group" aria-labelledby={id}>
      <Label id={id} className="text-content-tertiary">
        {label}
      </Label>
      {children}
      {hint ? <span className="text-xs text-content-secondary">{hint}</span> : null}
    </div>
  );
}

/**
 * More options: the SAME draft as the landing composer, with the fields that
 * have no pill beside it.
 *
 * Not a second form. The composer is moved in here (the landing page renders
 * none while this is open) and every answer lives in `LaunchDraftProvider`, so
 * opening this keeps the brief, the files and every control, and closing it
 * hands them back. One builder (`buildCreateRequest`) serves both surfaces,
 * so an untouched field is omitted the same way wherever Send is pressed.
 */
export function MoreOptionsDialog(): ReactNode {
  const open = useCreateWorkspaceUi((state) => state.open);
  const setOpen = useCreateWorkspaceUi((state) => state.setOpen);
  // A successful create navigates to the new workspace; the dialog that
  // started it has nothing left to say.
  const pathname = usePathname();
  const route = useRef(pathname);
  useEffect(() => {
    if (route.current === pathname) return;
    route.current = pathname;
    setOpen(false);
  }, [pathname, setOpen]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        className="max-h-[90dvh] w-[calc(100vw-2rem)] overflow-y-auto text-sm sm:max-w-3xl"
        data-testid="create-workspace-dialog"
      >
        <DialogHeader>
          <DialogTitle className="text-lg">New workspace</DialogTitle>
          <DialogDescription>
            Every choice from the composer carries over. Untouched fields follow the project’s defaults.
          </DialogDescription>
        </DialogHeader>
        {/* A child so a closed dialog costs nothing: Radix renders no content
            while closed, so none of these queries run until it opens. */}
        <Suspense>
          <MoreOptionsBody />
        </Suspense>
      </DialogContent>
    </Dialog>
  );
}

function MoreOptionsBody(): ReactNode {
  const { values, set } = useLaunchControls();
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  return (
    <div className="flex flex-col gap-4" data-testid="create-workspace-form">
      <LaunchPillGroup>
        <LaunchComposer
          disabled={false}
          expanded={false}
          inputRef={inputRef}
          expandControl={null}
          noteTestIds={{ error: "create-error", refusal: "create-refusal" }}
        />
      </LaunchPillGroup>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Title" hint="Leave empty and the agent names it once it knows the task.">
          <Input
            value={values.titleOverride ?? ""}
            onChange={(event) => set({ titleOverride: event.target.value || null })}
            placeholder="What this workspace is for"
            data-testid="create-title"
          />
        </Field>

        <Field label="Brief">
          <Select
            // The seeded answer until touched; `null` (not yet resolved)
            // matches no item, so the control shows nothing rather than a guess.
            value={values.brief === null ? "" : values.brief ? "on" : "off"}
            onValueChange={(choice) => set({ brief: choice === "on" })}
          >
            <SelectTrigger className="w-full" data-testid="create-brief">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="on">Send Grove&apos;s brief</SelectItem>
              <SelectItem value="off">No brief</SelectItem>
            </SelectContent>
          </Select>
        </Field>

        <Field label="Ticket" hint="Links the workspace to an issue and publishes its progress there.">
          <TicketField />
        </Field>
      </div>
    </div>
  );
}

/** Provider plus issue id, held in the draft as one `LaunchTicket`. */
function TicketField(): ReactNode {
  const { values, set } = useLaunchControls();
  const provider = (values.ticket?.provider ?? TICKET_PROVIDERS[0]) as TicketProvider;
  const id = values.ticket?.id ?? "";
  const write = (next: { provider: TicketProvider; id: string }): void =>
    set({ ticket: next.id.trim() === "" ? null : { provider: next.provider, id: next.id.trim() } });

  return (
    <div className="flex gap-2">
      <Select value={provider} onValueChange={(choice) => write({ provider: choice as TicketProvider, id })}>
        <SelectTrigger data-testid="create-ticket-provider" aria-label="Ticket provider">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {TICKET_PROVIDERS.map((name) => (
            <SelectItem key={name} value={name}>
              {name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        value={id}
        onChange={(event) => write({ provider, id: event.target.value })}
        placeholder="Optional — issue number"
        aria-label="Ticket number"
        data-testid="create-ticket-id"
      />
    </div>
  );
}
