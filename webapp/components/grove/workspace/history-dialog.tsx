"use client";

import { useMemo, useState } from "react";
import { HistoryIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { historyTimeline, type HistoryEvent } from "@/lib/grove/adapters/history-timeline";
import { ticketKey, useTicketProviders, useTickets, useWorkspaceHistory } from "@/lib/grove/hooks";
import type { RecordedTicketView, TicketProviderView, TicketRef, WorkspaceHistoryView } from "@/lib/grove/api";
import { HistoryFeed } from "./history-feed";

/**
 * Everything this workspace has recorded, behind one control in the Task card.
 *
 * The trigger stays absent for forward-only records that do not contain an
 * event. A name alone is the current task-card title, rather than a history a
 * reader needs to open.
 */
export function WorkspaceHistoryDialog({
  workspaceId,
  repoRoot,
}: {
  workspaceId: string;
  repoRoot: string;
}): React.ReactNode {
  const history = useWorkspaceHistory(workspaceId);
  const [open, setOpen] = useState(false);
  const recorded = history.data;

  function handleOpenChange(next: boolean): void {
    setOpen(next);
    if (next) void history.refetch();
  }

  if (!recorded || isEmpty(recorded)) return null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <TooltipIconButton
          tooltip="Change history"
          aria-label="Change history"
          className="size-6 min-h-[24px] min-w-[24px]"
          data-testid="workspace-history-trigger"
        >
          <HistoryIcon />
        </TooltipIconButton>
      </DialogTrigger>
      <DialogContent
        className="flex max-h-[90dvh] w-[calc(100vw-2rem)] flex-col gap-4 sm:max-w-2xl"
        data-testid="workspace-history-dialog"
      >
        <DialogHeader>
          <DialogTitle>Change history</DialogTitle>
          <DialogDescription>
            Recorded progress, names and tickets in one chronology.
          </DialogDescription>
        </DialogHeader>
        {open && <HistoryContent recorded={recorded} repoRoot={repoRoot} />}
        {history.isError && (
          <div className="flex min-w-0 items-center justify-between gap-2 border-t border-border pt-3">
            <p className="min-w-0 text-xs text-content-secondary">
              Couldn’t refresh recorded history. Showing the last loaded events.
            </p>
            <Button
              variant="outline"
              size="sm"
              disabled={history.isFetching}
              onClick={() => void history.refetch()}
            >
              Retry
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function HistoryContent({
  recorded,
  repoRoot,
}: {
  recorded: WorkspaceHistoryView;
  repoRoot: string;
}): React.ReactNode {
  // Radix unmounts the content while closed, keeping conversion and ticket
  // enrichment off the Task card's ordinary render path.
  const events: readonly HistoryEvent[] = useMemo(() => historyTimeline(recorded), [recorded]);
  const refs = useMemo(
    () => (recorded.tickets ?? []).flatMap((ticket) => {
      const ref = ticketRef(ticket);
      return ref ? [{ ticketKey: ticket.ticket_key, ref }] : [];
    }),
    [recorded.tickets],
  );
  const providers = useTicketProviders(repoRoot);
  const resolved = useTickets(repoRoot, refs.map(({ ref }) => ref), configuredProviders(providers.data));
  const ticketUrls = new Map(
    refs.flatMap(({ ticketKey: recordedKey, ref }) => {
      const url = resolved.byKey.get(ticketKey(ref))?.url;
      return url ? ([[recordedKey, url]] as const) : [];
    }),
  );
  return (
    <>
      <HistoryFeed events={events} ticketUrls={ticketUrls} />
      {(providers.isError || resolved.failed > 0) && (
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-content-tertiary">Some ticket links could not be resolved.</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              if (providers.isError) void providers.refetch();
              else resolved.retry();
            }}
          >
            Retry links
          </Button>
        </div>
      )}
    </>
  );
}

function ticketRef(ticket: RecordedTicketView): TicketRef | null {
  if (!isTicketProvider(ticket.provider) || !isTicketKind(ticket.kind)) return null;
  return {
    provider: ticket.provider,
    id: ticket.ticket_id,
    kind: ticket.kind,
    draft: false,
    ambiguous: false,
  };
}

function isTicketProvider(provider: string): provider is TicketRef["provider"] {
  return provider === "gitea" || provider === "github" || provider === "linear";
}

function isTicketKind(kind: string): kind is TicketRef["kind"] {
  return kind === "issue" || kind === "pull_request";
}

function configuredProviders(
  providers: readonly TicketProviderView[] | undefined,
): TicketRef["provider"][] {
  return providers?.filter((provider) => provider.configured).map((provider) => provider.provider) ?? [];
}

function isEmpty(history: WorkspaceHistoryView): boolean {
  return (
    (history.names?.length ?? 0) === 0 &&
    (history.progress?.length ?? 0) === 0 &&
    (history.tickets?.length ?? 0) === 0
  );
}
