"use client";

import { FingerprintIcon, TriangleAlertIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { CardField, CardFields, SectionCard } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";
import { Badge } from "@/components/ui/badge";
import { baseBranchOf } from "@/lib/grove/adapters";
import {
  runtimeGlossaryTerm,
  runtimeGlyph,
  statusGlossaryTerm,
  statusTone,
} from "@/components/grove/fleet/tokens";

import type { WorkspaceIdentity } from "./selectors";

/**
 * One card for the workspace's durable identity: how and where it runs, who
 * runs it, and which branch it owns.
 *
 * This deliberately follows the Timeline card's labelled-field anatomy rather
 * than placing two sets of chips in adjacent cards. The old chips preserved the
 * facts but made the reader reconstruct their relationships from two headings.
 * A term/value list makes each fact answerable in one scan while retaining the
 * status mark and every conditionally visible detail from both old cards.
 */
export function WorkspaceIdentityCard({ state }: { state: WorkspaceIdentity }) {
  const RuntimeIcon = runtimeGlyph(state.runtime);
  const runtimeDefault =
    state.runtime === "container" && state.runtime_default_config;
  const statusTerm = statusGlossaryTerm(state.status);
  const base = baseBranchOf(state);

  return (
    <SectionCard
      icon={<FingerprintIcon />}
      title="Identity"
      data-testid="workspace-identity-card"
    >
      <CardFields>
        <CardField label="Status">
          <Badge
            variant={statusTone(state.status)}
            className="capitalize"
            data-testid="workspace-status"
          >
            {statusTerm ? (
              <Explain term={statusTerm}>{state.status}</Explain>
            ) : (
              state.status
            )}
          </Badge>
        </CardField>
        <CardField label="Runtime">
          <span className="flex min-w-0 items-center gap-1.5">
            <RuntimeIcon aria-hidden className="size-[1em] shrink-0" />
            <Explain
              term={runtimeGlossaryTerm(state.runtime)}
              className="capitalize"
            >
              {state.runtime}
            </Explain>
            {runtimeDefault && (
              <span className="text-content-tertiary">default</span>
            )}
          </span>
        </CardField>
        {state.placement === "root" && (
          <CardField label="Placement">
            <Explain term="root_placement" />
          </CardField>
        )}
        {state.runtime_fallback_reason && (
          <CardField label={<Explain term="runtime_fallback" />}>
            <span
              className="flex min-w-0 items-center gap-1.5"
              title={state.runtime_fallback_reason}
            >
              <TriangleAlertIcon aria-hidden className="size-[1em] shrink-0" />
              Fell back to host
            </span>
          </CardField>
        )}
        <CardField label="Agent">
          <span
            className="flex min-w-0 items-center gap-1.5"
            title={state.agent_name}
          >
            <AgentMark
              agentName={state.agent_name}
              className="size-[1em] shrink-0"
            />
            <span className="truncate">{state.agent_name}</span>
          </span>
        </CardField>
        <CardField label="Branch" mono>
          {state.branch}
        </CardField>
        <CardField label="Base branch" mono={base !== null}>
          {base ?? "no separate base branch"}
        </CardField>
      </CardFields>
    </SectionCard>
  );
}
