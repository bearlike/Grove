"use client";

import { FolderGitIcon, TriangleAlertIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { Badge } from "@/components/ui/badge";
import { Explain } from "@/components/grove/glossary";
import type { WorkspaceStateView } from "@/lib/grove/api";
import { runtimeGlossaryTerm, runtimeGlyph, statusGlossaryTerm, statusTone } from "@/components/grove/fleet/tokens";

/**
 * What this workspace IS — every fact as one glyph-led chip, on one row.
 *
 * THIS REVERSES AN EARLIER DECISION, AND THE REASON IS WORTH KEEPING. The
 * previous shape put the lifecycle status alone on a badge row and demoted
 * runtime, placement and agent to a tertiary metadata LINE underneath, on the
 * reasoning that a badge is a state mark and those three are constants for the
 * life of the workspace. That reasoning is sound about *badges as signals* and
 * wrong about *this card as a layout*: a run of bare tertiary spans separated
 * only by a gap has no visible structure, so on review it read as jumbled prose
 * — four unrelated facts sharing a line with nothing to say where one ended and
 * the next began.
 *
 * The chip is doing a second job here that it does not do on a fleet wall: it is
 * a BOUNDARY. It gives each fact an edge, its own glyph, and a consistent
 * baseline, which is what turns a sentence-shaped line into a scannable set. So
 * the constants take the outline variant — present, uniform, deliberately
 * quieter — and only the lifecycle status carries a TONE. Rank is expressed by
 * colour rather than by demoting three facts out of the row, which preserves the
 * original point without paying for it in structure.
 *
 * THE RUNTIME FALLBACK still earns its own mark, because it is not a property —
 * it records that the isolation contract this workspace ASKED for was not
 * honoured, and only a respawn can clear it.
 */
export function IdentityBadges({ state }: { state: WorkspaceStateView }) {
  return (
    <div
      className="flex min-w-0 flex-wrap items-center gap-1.5"
      data-testid="workspace-identity"
    >
      <StatusBadge state={state} />
      <IdentityFacts state={state} />
      {state.runtime_fallback_reason && (
        // The badge's own title carries the SPECIFIC reason (instance
        // detail); `Explain` on the label carries the general concept from
        // the glossary. Deliberately not merged into one affordance — one
        // term has one fixed sentence, and this reason is per-workspace.
        <Badge variant="outline" title={state.runtime_fallback_reason} data-testid="runtime-fallback">
          <TriangleAlertIcon aria-hidden />
          <Explain term="runtime_fallback">Fell back to host</Explain>
        </Badge>
      )}
    </div>
  );
}

/**
 * The lifecycle axis, toned by the SAME table the rail and the fleet card read.
 * A status that renders one way on the wall and another way in the panel is two
 * vocabularies for one fact, which is the whole defect this collapses.
 *
 * `capitalize` rather than a label table: `status` is a lower-case wire enum
 * whose every member is a single ordinary word, so the CSS does the whole job.
 * A `Record<WorkspaceStatus, string>` would be eight entries each restating
 * their own key with one letter changed, kept in step with the wire union for
 * no gain.
 */
export function StatusBadge({ state }: { state: WorkspaceStateView }) {
  const term = statusGlossaryTerm(state.status);
  return (
    <Badge variant={statusTone(state.status)} className="capitalize" data-testid="workspace-status">
      {term ? <Explain term={term}>{state.status}</Explain> : state.status}
    </Badge>
  );
}

/**
 * The constants: how it is isolated, where it lives, and what runs in it.
 *
 * A fragment, not a wrapper — these chips are siblings of the status badge on
 * ONE row, and an intermediate flex container would have made them a group that
 * wraps as a block, which is the structure this card just stopped having.
 *
 * Every chip leads with its own glyph and carries a screen-reader label, because
 * `container` and `Root checkout` are only self-describing to someone who
 * already knows Grove's vocabulary. The glyphs are sized in `em` so they track
 * the badge's own type size, matching the entity vocabulary.
 *
 * THE AGENT'S NAME IS RENDERED EXACTLY AS CONFIGURED, with no capitalisation
 * applied. Measured on this host it reads `Claude Code (via …)`: it is already
 * prose, it is the user's own words from their config, and title-casing
 * somebody's proper nouns is the same class of mistake as stripping characters
 * out of a ticket title. Only the wire enums get `capitalize`.
 */
function IdentityFacts({ state }: { state: WorkspaceStateView }) {
  const RuntimeIcon = runtimeGlyph(state.runtime);
  const runtimeDefault = state.runtime === "container" && state.runtime_default_config;
  return (
    <>
      <Badge variant="outline" data-testid="workspace-runtime">
        <RuntimeIcon aria-hidden className="size-[1em] shrink-0" />
        <span className="sr-only">Runtime: </span>
        <Explain term={runtimeGlossaryTerm(state.runtime)} className="capitalize">
          {state.runtime}
        </Explain>
        {/* "default" stays plain text, not folded into the tooltip: it is
            already visible on the row, so a reader who never hovers loses
            nothing, per the module's own additive rule. */}
        {runtimeDefault && <span className="text-content-tertiary">default</span>}
      </Badge>

      {/* Only when it is TRUE. A worktree is the ordinary case, and a chip on
          every workspace saying so spends the row on the absence of news; a root
          checkout means edits land in the real repository, which is worth a mark
          every time. */}
      {state.placement === "root" && (
        <Badge variant="outline" data-testid="workspace-placement">
          <FolderGitIcon aria-hidden className="size-[1em] shrink-0" />
          <Explain term="root_placement" />
        </Badge>
      )}

      {/* `max-w-*` plus `truncate` rather than letting it size to content: an
          agent name is the one value here a user writes themselves, so it is the
          one that can be long enough to push the row past the card's edge. */}
      <Badge variant="outline" className="min-w-0" data-testid="workspace-agent" title={state.agent_name}>
        <AgentMark agentName={state.agent_name} className="size-[1em] shrink-0" />
        <span className="sr-only">Agent: </span>
        <span className="max-w-40 truncate">{state.agent_name}</span>
      </Badge>
    </>
  );
}
