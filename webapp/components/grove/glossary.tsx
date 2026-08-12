"use client";

import type { ReactNode } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * Grove's vocabulary, defined once, explained where it is used.
 *
 * Grove is an open-source product whose surfaces are dense with terms a newcomer
 * cannot infer: a workspace is "root placement", a session reports two different
 * clocks, a quota window is a "facet". None of that is guessable, and a reader
 * who cannot name what they are looking at cannot adopt it. So the definition
 * travels WITH the term rather than living in documentation nobody opens
 * mid-task.
 *
 * ONE TABLE, and that is the point. Two surfaces explaining "compute time" in
 * two slightly different sentences is worse than neither explaining it — the
 * reader learns the term twice and trusts it less. A term is defined here or it
 * is not defined; there is no second home.
 *
 * RESTRAINT IS PART OF THE RULE. A tooltip on every noun is a page that argues
 * with itself, and it trains the reader to dismiss the affordance before
 * reaching the one term they actually needed. Add an entry when a term is
 * GROVE'S OWN or is a number whose derivation changes how it should be read.
 * Never for a word that means what it says.
 *
 * Each definition is one sentence, written for someone who has not read the
 * docs, and says what the thing IS before what it implies.
 */

/** A term a reader may not be able to infer. */
export type GlossaryTerm = keyof typeof GLOSSARY;

interface Definition {
  /** The term as a reader should see it named. */
  readonly label: string;
  /** One sentence. What it is, then what it implies. */
  readonly summary: string;
  /**
   * Where to read more, when a sentence genuinely cannot finish the job.
   *
   * Presence of this CHANGES THE PRIMITIVE the explanation is rendered with —
   * see `Explain`. Add it only when the extra hop earns itself.
   */
  readonly href?: string;
}

export const GLOSSARY = {
  clock_time: {
    label: "Clock time",
    summary:
      "Real elapsed time this session was actively working; work happening at the same time counts once.",
  },
  compute_time: {
    label: "Compute time",
    summary:
      "Total work across the main agent and every sub-agent it spawned, each counted separately — so it is routinely larger than clock time.",
  },
  root_placement: {
    label: "Root checkout",
    summary:
      "This workspace runs in the repository's own checkout rather than a private worktree, so its changes are visible to everything else using that folder.",
  },
  container_runtime: {
    label: "Container",
    summary:
      "The agent runs inside a dev container, so what it can reach is limited to that container rather than the whole machine.",
  },
  host_runtime: {
    label: "Host",
    summary:
      "The agent runs directly on this machine with the same access you have, rather than inside a container.",
  },
  runtime_fallback: {
    label: "Fell back to host",
    summary:
      "This workspace asked to run in a container and could not, so it is running on the host instead — only a respawn can clear it.",
  },
  cache_read_tokens: {
    label: "Cache read",
    summary:
      "Context the model re-read from cache rather than reprocessing; it usually dwarfs every other token class and is billed far cheaper.",
  },
  quota_window: {
    label: "Usage window",
    summary:
      "One independent reading of your plan's capacity — session, weekly, or per model — with its own 100%; it never adds up with any other window here.",
  },
  status_provisioning: {
    label: "Provisioning",
    summary:
      "The container this workspace runs in is still being built; other actions unlock once it finishes on its own.",
  },
  status_offline: {
    label: "Offline",
    summary:
      "The process running the agent has stopped, but the workspace's files are intact — respawn brings it back.",
  },
  status_orphaned: {
    label: "Orphaned",
    summary:
      "This workspace's own files are gone, so nothing can bring it back — only removing the record is left.",
  },
  agent_blocked: {
    label: "Blocked",
    summary:
      "The agent stopped at an explicit permission or input prompt and cannot continue until you answer it.",
  },
  compaction: {
    label: "Compaction",
    summary:
      "The agent's context filled up, so older turns were dropped and replaced with a summary to keep the session going.",
  },
  bash_call_attribution: {
    label: "Time attribution",
    summary:
      "Time in a Bash call goes to the command that started it, not every stage — a pipeline such as find piped into xargs pylint counts entirely toward find.",
  },
  cache_creation_tokens: {
    label: "Cache write",
    summary:
      "Context the model wrote into cache for a later turn to re-read; it is billed at a premium, once, rather than every turn that reuses it.",
  },
  model_latency: {
    label: "Avg model latency",
    summary:
      "The model's own average wait per call — just the time between a request and its response, never folded with tool time, so it is comparable model to model.",
  },
  model_wait: {
    label: "Model wait",
    summary:
      "The part of compute time spent waiting on the model rather than running tools — the same calls model latency averages, added up instead.",
  },
  tool_time: {
    label: "Tool time",
    summary:
      "The part of compute time spent running tools — commands, file edits, searches — rather than waiting on the model; the two together are compute time.",
  },
  delegated_tokens: {
    label: "Delegated",
    summary:
      "The share of this session's tokens spent by sub-agents it handed work to; the rest is the main agent's own, and the tokens column already counts both.",
  },
} as const satisfies Record<string, Definition>;

/**
 * A term, with its definition one hover or one tap away.
 *
 * The visible label is never replaced or hidden — the explanation is ADDITIVE.
 * A reader who never discovers the affordance must lose nothing, which is the
 * same rule the design system states for colour: an aid is never the sole
 * carrier of meaning.
 *
 * TWO PRIMITIVES, CHOSEN BY WHETHER THE CONTENT IS REACHABLE, and this is not a
 * style preference. A Radix tooltip closes when the pointer leaves its trigger,
 * so its content can never be clicked — a link inside one is unreachable by
 * mouse, and silently so. A definition that carries an `href` therefore renders
 * as a POPOVER, which is dismissible, focus-managed and stays open while you
 * move into it. Both are vendored; neither is restyled here.
 *
 * The trigger is a `button` in both cases rather than a bare span, because an
 * explanation nobody can reach with a keyboard is an explanation that excludes
 * the readers most likely to need it.
 */
export function Explain({
  term,
  children,
  className,
}: {
  term: GlossaryTerm;
  /** The visible text. Defaults to the term's own label. */
  children?: ReactNode;
  className?: string;
}) {
  const definition: Definition = GLOSSARY[term];
  const label = children ?? definition.label;
  // Dotted underline, not a colour or an icon: a help glyph beside every term
  // would out-weigh the terms themselves on a dense card, and a colour would
  // collide with the state palette. `decoration-dotted` is a text-decoration
  // utility, not one of the colour/radius/shadow classes `lint:styling` forbids
  // under this directory.
  const trigger = cn(
    "cursor-help underline decoration-dotted underline-offset-2",
    "focus-visible:outline-2 focus-visible:outline-offset-2",
    className,
  );

  if (definition.href) {
    return (
      <Popover>
        <PopoverTrigger className={trigger} data-testid={`explain-${term}`}>
          {label}
        </PopoverTrigger>
        <PopoverContent className="w-64 text-xs">
          <p>{definition.summary}</p>
          <a href={definition.href} className="underline underline-offset-2">
            Read more
          </a>
        </PopoverContent>
      </Popover>
    );
  }

  // SELF-PROVIDING, and deliberately so even though `providers.tsx` already
  // mounts a `TooltipProvider` at the app root. Radix throws outright without
  // an ancestor provider — "`Tooltip` must be used within `TooltipProvider`" —
  // so a component that relies on one being somewhere overhead is a component
  // that explodes the first time it is rendered outside the app tree. It
  // surfaced immediately here: the SSR render tests call
  // `renderToStaticMarkup` on a bare component and every one of them died.
  // Nesting providers is supported and cheap (it is context, not a portal or a
  // listener), and the inner one simply wins for its own subtree.
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className={trigger} data-testid={`explain-${term}`}>
          {label}
        </TooltipTrigger>
        <TooltipContent className="max-w-64">{definition.summary}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
