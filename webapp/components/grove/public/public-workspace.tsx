"use client";

import { useState } from "react";

import { AssistantRuntimeProvider } from "@assistant-ui/react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { InfoIcon, LockIcon, PrinterIcon } from "lucide-react";

import { ErrorState } from "@/components/elements/error-state";
import { BrandMark } from "@/components/grove/brand-mark";
import { BranchLabel, ProjectLabel } from "@/components/grove/entity";
import { StatusBadge } from "@/components/grove/fleet/badges";
import { GitHubIcon } from "@/components/icons/github";
import { RelativeTime } from "@/components/grove/relative-time";
import { RAIL_WIDTH } from "@/components/grove/shell/rail-width";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { GroveDataParts } from "@/components/grove/workspace/data-parts";
import { TranscriptSkeleton } from "@/components/grove/workspace/transcript-skeleton";
import {
  Thread,
  type ThreadComponents,
} from "@/components/grove/workspace/thread";
import { GROVE_THREAD_COMPONENTS } from "@/components/grove/workspace/tool-call-part";
import { WorkPanel } from "@/components/grove/workspace/work-panel";
import type { PanelTab } from "@/components/grove/workspace/selectors";
import { useMinWidth } from "@/components/grove/workspace/use-min-width";
import { SectionCard } from "@/components/grove/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import type { PublicWorkspaceView } from "@/lib/grove/api";
import { usePublicTurns, usePublicWorkspace } from "@/lib/grove/hooks";
import { GroveProtocolError } from "@/lib/grove/api";
import {
  clearSharePasscode,
  readSharePasscode,
  writeSharePasscode,
} from "@/lib/grove/api/share-passcode";
import { cn } from "@/lib/utils";
import { useReadOnlyTranscript } from "@/lib/grove/runtime";

const SPLIT_MIN_WIDTH = 1024;

/**
 * The one transcript renderer renders both a live workspace and a historical
 * session. The public share is historical in the capability sense: it receives
 * a periodically-read transcript, but it has no right to steer its agent.
 * Suppressing the new-chat welcome follows the catalog session page exactly;
 * `isEmpty` is derived from the query's actual turns and therefore wins the
 * race against assistant-ui's asynchronously-populated external store.
 */
const SUPPRESSED_WELCOME_COMPONENTS: ThreadComponents = {
  ...GROVE_THREAD_COMPONENTS,
  Welcome: () => null,
};

/**
 * A public workspace: a readable transcript and the same bounded information
 * panel its owner sees, with every control path deliberately withheld.
 *
 * It owns no authenticated shell and no event stream. The public hooks poll the
 * capability endpoints independently, which is the only freshness mechanism a
 * link that never receives host-wide `/events` can honestly have.
 */
export function PublicWorkspace(): React.ReactNode {
  const params = useParams<{ token: string }>();
  const token = params.token ?? "";
  const workspace = usePublicWorkspace(token);
  const turns = usePublicTurns(token);
  const wideEnoughToSplit = useMinWidth(SPLIT_MIN_WIDTH);
  const { runtime, isEmpty } = useReadOnlyTranscript(turns.data?.turns);
  // Local, not persisted. `Workspace` holds this above `WorkPanel` because
  // crossing the split breakpoint remounts the panel from a different depth in
  // the tree; the same is true here, so the choice lives at this level for the
  // same reason. Deliberately NOT written to `localStorage`: a reader following
  // one link has no relationship with this workspace worth remembering, and the
  // two panes are one click apart.
  const [workTab, setWorkTab] = useState<PanelTab>("info");

  // A LOCKED share is not an error, and rendering it as one is the difference
  // between "ask me for the passcode" and "this link is broken". The daemon
  // answers 401 `share_passcode_required` for both a missing and a wrong
  // passcode — deliberately one code, so a guess learns nothing — so this
  // branch covers first arrival and a rejected attempt alike.
  if (isPasscodeRequired(workspace.error)) {
    return (
      <PublicUnlock
        token={token}
        // A stored passcode that is now refused is stale (the project changed
        // it), so it is cleared before asking again — otherwise every refetch
        // silently retries a secret that will never be accepted.
        stale={clearStaleSharePasscode(token)}
        onUnlock={() => void workspace.refetch()}
      />
    );
  }

  if (workspace.isError) {
    return (
      <PublicSurface>
        <PublicHeader title="Shared workspace" />
        <ErrorState
          className="m-4"
          title="Couldn’t load this shared workspace"
          detail={workspace.error.message}
          retrying={workspace.isFetching}
          onRetry={() => void workspace.refetch()}
        />
      </PublicSurface>
    );
  }

  if (!workspace.data) return <PublicWorkspaceSkeleton />;

  const { peek, activity, shared, grove } = workspace.data;
  const title = peek.state.title;
  const commits = peek.recent_commits;

  return (
    // `relative` is LOAD-BEARING, not decoration, and its absence made the whole
    // document scroll ~218px past the viewport.
    //
    // `overflow-hidden` clips an absolutely-positioned descendant only when the
    // clipping element is ALSO that descendant's containing block — that is,
    // only when it is itself positioned. This root was `static`, so the
    // transcript's own absolute overlays (assistant-ui's tool shimmers, Grove's
    // `scroll-edge-*` fades) resolved against the initial containing block,
    // escaped the clip at y~27,000, and extended the document instead.
    //
    // It read as INTERMITTENT for the same reason: those overlays exist only
    // while a tool is running or a pane is actually scrollable, so an idle
    // workspace looked fine. `AppShell` has carried `relative` from the start;
    // this page rolled its own shell and dropped it. Measured 1118px -> 900px.
    <div className="relative flex h-dvh w-full overflow-hidden bg-surface-sunken">
      {/* Same measure as the app shell's rail, from the same export — see
          `rail-width.ts` for why it is not a literal here. This rail never
          collapses: there is no session to hold a preference for, and a
          reader of one shared link has no fleet to make room for. */}
      <aside
        className={cn(
          "print-hide hidden h-full shrink-0 flex-col overflow-hidden md:flex",
          RAIL_WIDTH,
        )}
      >
        <PublicRail
          currentToken={token}
          project={peek.state.project}
          shared={shared}
          grove={grove}
        />
      </aside>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-surface-base">
        <PublicHeader title={title} sessionPinned={workspace.data.session_pinned} />
        {/* Paper needs its own chrome. On screen the rail carries the brand and
            the header carries the title, and print hides the rail entirely — so
            without these a printout arrives as an anonymous wall of transcript
            with nothing saying what it is or where it came from. They render
            ONLY on paper, so the screen is untouched. */}
        <PrintMasthead
          title={title}
          project={peek.state.project}
          grove={grove}
        />
        <div
          className="print-scroll flex min-h-0 min-w-0 flex-1 flex-col"
          data-testid="public-workspace-page"
        >
          {wideEnoughToSplit ? (
            <ResizablePanelGroup
              orientation="horizontal"
              className="print-stack print-reverse min-h-0 flex-1"
              data-split-min-width={SPLIT_MIN_WIDTH}
            >
              {/* The transcript and work panel each own their scroll area. The
                height comes from `PublicSurface`'s flex chain, never viewport
                arithmetic: that prevents a long transcript from making the
                public page itself the scroll owner. */}
              <ResizablePanel defaultSize="55" minSize="30">
                <PrintSection>Transcript</PrintSection>
                <PublicTranscript
                  pending={turns.isPending}
                  error={turns.error}
                  retry={() => void turns.refetch()}
                  runtime={runtime}
                  isEmpty={isEmpty}
                />
              </ResizablePanel>
              <ResizableHandle className="transition-colors after:w-3 hover:bg-ring focus-visible:bg-ring active:bg-ring" />
              <ResizablePanel defaultSize="45" minSize="25">
                <div className="flex h-full min-h-0 min-w-0 flex-col bg-background">
                  <PrintSection>Workspace summary</PrintSection>
                  <WorkPanel
                    peek={peek}
                    activity={activity}
                    commits={commits}
                    repoRoot={null}
                    tab={workTab}
                    onTabChange={setWorkTab}
                  />
                </div>
              </ResizablePanel>
            </ResizablePanelGroup>
          ) : (
            /* A narrow screen cannot afford side-by-side reading, but dropping a
                pane would hide the work context from the share. Stack complete,
                independently-bounded panes instead; the transcript comes first
                because it is the page's subject and the work panel follows in the
                same reading order as the wide split. */
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <section className="flex min-h-0 min-w-0 flex-1 flex-col">
                <PrintSection>Transcript</PrintSection>
                <PublicTranscript
                  pending={turns.isPending}
                  error={turns.error}
                  retry={() => void turns.refetch()}
                  runtime={runtime}
                  isEmpty={isEmpty}
                />
              </section>
              <section className="flex min-h-0 min-w-0 flex-1 flex-col">
                <PrintSection>Workspace summary</PrintSection>
                <WorkPanel
                  peek={peek}
                  activity={activity}
                  commits={commits}
                  repoRoot={null}
                  tab={workTab}
                  onTabChange={setWorkTab}
                />
              </section>
            </div>
          )}
        </div>
        <PrintFooter grove={grove} />
      </main>
    </div>
  );
}

/** The shared page's public shell header: identification only, never navigation. */
function PublicHeader({
  title,
  sessionPinned,
}: {
  title: string;
  sessionPinned?: boolean;
}): React.ReactNode {
  return (
    // `ShellHeader`'s two direct button children operate the authenticated,
    // collapsible rail. A public rail is permanently visible on desktop and has
    // no mobile sheet, so that chrome is intentionally hidden rather than left
    // as a control that cannot affect this shell.
    <div className="[&_header>button]:hidden" data-testid="public-shell-header">
      <ShellHeader
        title={title}
        actions={
          <div className="print-hide flex shrink-0 items-center gap-2">
            {sessionPinned !== undefined && (
              <span className="text-xs text-content-tertiary">
                {sessionPinned
                  ? "The session this link was shared from"
                  : "This workspace’s current session"}
              </span>
            )}
            <Badge variant="secondary">Public read-only view</Badge>
            <Button
              variant="ghost"
              size="sm"
              className="print-hide"
              onClick={() => window.print()}
            >
              <PrinterIcon aria-hidden />
              Print
            </Button>
          </div>
        }
      />
    </div>
  );
}

/**
 * Whether a failed read means "this share is locked" rather than "this went
 * wrong".
 *
 * Keyed on the daemon's error CODE, never on the 401 status alone: the code is
 * the contract, and a bare status would also swallow any future unauthorized
 * case into a passcode prompt that could never satisfy it.
 */
function clearStaleSharePasscode(token: string): boolean {
  const had = readSharePasscode(token) !== null;
  if (had) clearSharePasscode(token);
  return had;
}

function isPasscodeRequired(error: unknown): boolean {
  return (
    error instanceof GroveProtocolError &&
    error.code === "share_passcode_required"
  );
}

/**
 * The gate in front of a passcode-protected share.
 *
 * It is deliberately the ONLY thing on screen. Rendering the rail, the header
 * or a skeleton behind it would leak the shape of what is behind the lock —
 * how many workspaces the project shares, what this one is called — to somebody
 * who has not yet proved they may see any of it. A locked page says only that
 * it is locked.
 *
 * There is no "wrong passcode" message distinct from "enter the passcode",
 * because the daemon answers one code for both and this surface must not invent
 * a distinction the server refused to make: telling a guesser that a passcode
 * was wrong rather than missing is exactly the oracle the flat refusal avoids.
 * The `stale` copy is the one concession, and it is about a passcode THIS
 * reader already supplied, not about one they are guessing.
 */
function PublicUnlock({
  token,
  stale,
  onUnlock,
}: {
  token: string;
  stale: boolean;
  onUnlock: () => void;
}): React.ReactNode {
  const [value, setValue] = useState("");

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const passcode = value.trim();
    if (!passcode) return;
    writeSharePasscode(token, passcode);
    setValue("");
    onUnlock();
  };

  return (
    <div className="flex h-dvh w-full items-center justify-center bg-surface-sunken p-4">
      <SectionCard
        icon={<LockIcon />}
        title="This share is protected"
        description={
          stale
            ? "That passcode was not accepted. It may have changed since you last opened this link."
            : "Enter the passcode you were given to read this workspace."
        }
        className="w-full max-w-sm"
      >
        <form className="flex flex-col gap-3" onSubmit={submit}>
          <Input
            type="password"
            autoFocus
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Passcode"
            aria-label="Share passcode"
            data-testid="share-passcode-input"
          />
          <Button type="submit" disabled={value.trim() === ""}>
            Unlock
          </Button>
        </form>
      </SectionCard>
    </div>
  );
}

/**
 * Grove's own repository, as `owner/name`.
 *
 * DERIVED from the url the daemon sends rather than written out, so the two can
 * never disagree and a fork does not have to remember to edit a second constant.
 * Falls back to the bare url if it is ever not a forge-shaped path, because a
 * printed page showing a full url is mildly ugly where showing nothing is a
 * missing attribution.
 */
function repoIdentifier(repoUrl: string): string {
  try {
    const parts = new URL(repoUrl).pathname.split("/").filter(Boolean);
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : repoUrl;
  } catch {
    return repoUrl;
  }
}

/**
 * The masthead a printed share carries, and the only place the brand appears on
 * paper.
 *
 * Print hides the rail, which on screen is what says this is Grove and which
 * workspace you are reading. Without a masthead the printout is an anonymous
 * wall of transcript: no title, no project, no attribution, no way for somebody
 * handed the paper to find the tool that produced it. So it restates the four
 * facts a physical page cannot recover from context, and it does it once, at
 * the top, where a reader looks first.
 *
 * The repository is spelled `owner/name` rather than shown as a url. On paper a
 * link is not clickable, so the useful form is the one a person can TYPE into a
 * browser or a `git clone`, and a full https url is neither shorter nor clearer.
 * It stays a real anchor so a PDF keeps it live.
 */
function PrintMasthead({
  title,
  project,
  grove,
}: {
  title: string;
  project: string;
  grove: PublicWorkspaceView["grove"];
}): React.ReactNode {
  return (
    <header className="print-only mb-6 border-b pb-4">
      <div className="flex items-center gap-2">
        <BrandMark className="size-6 shrink-0" />
        <span className="text-sm font-semibold">Grove</span>
        <a
          href={grove.repo_url}
          className="font-mono text-xs text-content-tertiary"
        >
          {repoIdentifier(grove.repo_url)}
        </a>
      </div>
      <h1 className="mt-3 text-lg font-semibold">{title}</h1>
      <p className="mt-1 text-xs text-content-tertiary">
        <ProjectLabel name={project} /> · shared workspace transcript · Grove{" "}
        {grove.version}
      </p>
    </header>
  );
}

/**
 * A heading that exists only on paper.
 *
 * On screen these sections are a tab strip and a pane: their identity is
 * carried by chrome that print removes. Printed, the two run together and a
 * reader cannot tell where the summary stops and the conversation starts, which
 * is exactly the structure a printout is FOR. `break-before` keeps each on its
 * own page so the seam is unmissable rather than a heading stranded at the foot
 * of a page.
 */
function PrintSection({
  children,
}: {
  children: React.ReactNode;
}): React.ReactNode {
  return (
    <h2 className="print-only print-section mb-3 text-base font-semibold">
      {children}
    </h2>
  );
}

/**
 * The end of the document, said explicitly.
 *
 * A transcript can run to dozens of pages, so "is this all of it" is a real
 * question for somebody holding the stack. A closing rule that names the source
 * answers it, and repeats the attribution for anyone who only ever sees the
 * last page.
 */
function PrintFooter({
  grove,
}: {
  grove: PublicWorkspaceView["grove"];
}): React.ReactNode {
  return (
    <footer className="print-only mt-6 border-t pt-3">
      <p className="text-xs text-content-tertiary">
        End of transcript · Printed from a Grove shared workspace ·{" "}
        <a href={grove.repo_url} className="font-mono">
          {repoIdentifier(grove.repo_url)}
        </a>
      </p>
    </footer>
  );
}

/**
 * The public rail is deliberately an expanded rail, rather than an adaptation
 * of `AppSidebar`: it is a map of the capabilities this link grants, not an app
 * navigator. Its `w-98` reuses the authenticated rail's only explicit width
 * class instead of inventing a second measurement.
 */
function PublicRail({
  currentToken,
  project,
  shared,
  grove,
}: {
  currentToken: string;
  project: string;
  shared: PublicWorkspaceView["shared"];
  grove: PublicWorkspaceView["grove"];
}): React.ReactNode {
  return (
    <>
      <div className="mt-2 flex h-12 shrink-0 items-center gap-2 px-3">
        <a
          href={grove.repo_url}
          target="_blank"
          rel="noreferrer"
          className="flex min-w-0 items-center gap-2"
          aria-label="Grove"
        >
          <BrandMark className="size-8 shrink-0" />
          <span className="truncate text-sm font-medium">Grove</span>
        </a>
      </div>

      <nav
        className="print-scroll flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-3"
        aria-label="Shared workspaces"
      >
        {shared.map((entry) => (
          <SharedWorkspaceRow
            key={entry.token}
            entry={entry}
            project={project}
            current={entry.token === currentToken}
          />
        ))}
      </nav>

      <div className="flex shrink-0 flex-col gap-1.5 border-t p-3">
        <AboutGrove grove={grove} />
        <a
          href={grove.repo_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-8 items-center gap-2 px-2.5 text-xs text-content-tertiary underline-offset-2 hover:underline"
        >
          <GitHubIcon className="size-[1em]" />
          View on GitHub
        </a>
        <span className="px-2.5 text-xs text-content-tertiary tabular-nums">
          Grove {grove.version}
        </span>
      </div>
    </>
  );
}

/**
 * One row in the rail — INCLUDING the workspace being viewed, which is the
 * whole point of `current`. The payload deliberately contains self (see
 * `PublicSharedWorkspaceView` on the daemon side); without it the reader has no
 * "you are here", and a project with one shared workspace showed an empty rail.
 */
function SharedWorkspaceRow({
  entry,
  project,
  current,
}: {
  entry: PublicWorkspaceView["shared"][number];
  project: string;
  current: boolean;
}): React.ReactNode {
  return (
    <Button
      asChild
      variant={current ? "secondary" : "ghost"}
      size="sm"
      className="h-auto w-full justify-start py-2 font-normal"
    >
      <Link
        href={`/public/${encodeURIComponent(entry.token)}`}
        aria-current={current ? "page" : undefined}
      >
        <span className="flex min-w-0 flex-1 flex-col items-start gap-0.5">
          <span className="flex w-full min-w-0 items-baseline gap-2">
            <span className="min-w-0 flex-1 truncate text-sm text-content-primary">
              {entry.title}
            </span>
            <RelativeTime
              iso={entry.updated_at}
              className="max-w-16 shrink-0 truncate text-xs text-content-tertiary tabular-nums"
            />
          </span>
          {/* The capability payload deliberately offers only a directory NAME,
              never a repo path. `ProjectLabel` says exactly that safely; the
              branch is the sibling's own coordinate, so the two typed entities
              retain the rail vocabulary without manufacturing a location. */}
          <span className="flex w-full min-w-0 items-center gap-2 text-xs text-content-tertiary">
            <ProjectLabel name={project} className="min-w-0" />
            <BranchLabel name={entry.branch} />
          </span>
        </span>
        <StatusBadge status={entry.status} />
      </Link>
    </Button>
  );
}

/** The one non-destination in the rail. It explains the product without adding a route to `NAV_ITEMS`. */
function AboutGrove({
  grove,
}: {
  grove: PublicWorkspaceView["grove"];
}): React.ReactNode {
  return (
    <Dialog>
      <DialogTrigger asChild>
        {/* `outline`, not `ghost`, and it carries a glyph. Every other item in
            this footer is a LINK that leaves the page; this one opens a dialog
            in place, and a borderless row of text was indistinguishable from
            them. The edge is what says "this is a control", which is also the
            design system's rule that a state or affordance never rests on one
            carrier — here the border and the icon carry it together. */}
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start px-2.5 font-normal"
        >
          <InfoIcon aria-hidden />
          About Grove
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>About Grove</DialogTitle>
          <DialogDescription>{grove.tagline}</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-3 text-xs text-content-tertiary">
          <span className="tabular-nums">Grove {grove.version}</span>
          <a
            href={grove.repo_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 underline-offset-2 hover:underline"
          >
            <GitHubIcon className="size-[1em]" />
            GitHub
          </a>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** The public transcript has the catalog page's read-only runtime, and no public-specific renderer. */
function PublicTranscript({
  pending,
  error,
  retry,
  runtime,
  isEmpty,
}: {
  pending: boolean;
  error: Error | null;
  retry: () => void;
  runtime: ReturnType<typeof useReadOnlyTranscript>["runtime"];
  isEmpty: boolean;
}): React.ReactNode {
  if (pending) return <TranscriptSkeleton />;
  if (error) {
    return (
      <ErrorState
        className="m-4"
        title="Couldn’t load this transcript"
        detail={error.message}
        retrying={false}
        onRetry={retry}
      />
    );
  }
  if (isEmpty) {
    return (
      <p className="m-auto text-sm text-content-tertiary">
        No messages recorded
      </p>
    );
  }

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <GroveDataParts />
      <div
        className="flex min-h-0 min-w-0 flex-1 flex-col bg-background"
        data-testid="public-transcript"
      >
        <Thread components={SUPPRESSED_WELCOME_COMPONENTS} />
      </div>
    </AssistantRuntimeProvider>
  );
}

/** The route segment's first paint mirrors the real rail, header and transcript rather than a bare spinner. */
export function PublicWorkspaceSkeleton(): React.ReactNode {
  return (
    // Same shell as the real page, `relative` included — see the note above.
    <div className="relative flex h-dvh w-full overflow-hidden bg-surface-sunken">
      {/* Same measure as the app shell's rail, from the same export — see
          `rail-width.ts` for why it is not a literal here. This rail never
          collapses: there is no session to hold a preference for, and a
          reader of one shared link has no fleet to make room for. */}
      <aside
        className={cn(
          "print-hide hidden h-full shrink-0 flex-col overflow-hidden md:flex",
          RAIL_WIDTH,
        )}
      >
        <div className="mt-2 flex h-12 shrink-0 items-center gap-2 px-3">
          <BrandMark className="size-8 shrink-0" />
          <span className="text-sm font-medium">Grove</span>
        </div>
        <div className="flex flex-1 flex-col gap-1.5 p-3" aria-hidden>
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="flex h-12 items-center gap-2 px-2.5">
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <Skeleton className="h-3.5 w-full" />
                <Skeleton className="h-2.5 w-2/3" />
              </div>
            </div>
          ))}
        </div>
      </aside>
      <PublicSurface>
        <PublicHeader title="Shared workspace" />
        <TranscriptSkeleton />
      </PublicSurface>
    </div>
  );
}

/**
 * The bounded page half of the public shell.
 *
 * Not `absolute inset-0`: the rail shares the outer flex row, and absolute
 * positioning would resolve against that whole row and paint over it. The flex
 * chain supplies the full height each transcript and work panel needs instead.
 */
function PublicSurface({
  children,
}: {
  children: React.ReactNode;
}): React.ReactNode {
  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-surface-base">
      {children}
    </main>
  );
}
