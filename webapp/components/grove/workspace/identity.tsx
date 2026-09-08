"use client";

import { FingerprintIcon, TriangleAlertIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { LangfuseMark } from "@/components/grove/icons/langfuse-mark";
import { CardField, CardFields, SectionCard } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";
import { Badge } from "@/components/ui/badge";
import { baseBranchOf } from "@/lib/grove/adapters";
import { useWhoami } from "@/lib/grove/hooks";
import {
  runtimeGlossaryTerm,
  runtimeGlyph,
  statusGlossaryTerm,
  statusTone,
} from "@/components/grove/fleet/tokens";

import type { WorkspaceStateView } from "@/lib/grove/api";

/**
 * One card for the workspace's durable identity: how and where it runs, who
 * runs it, and which branch it owns.
 *
 * This deliberately follows the Timeline card's labelled-field anatomy rather
 * than placing two sets of chips in adjacent cards. The old chips preserved the
 * facts but made the reader reconstruct their relationships from two headings.
 * A term/value list makes each fact answerable in one scan while retaining the
 * status mark and every conditionally visible detail from both old cards.
 *
 * Takes the FULL record on purpose: this card is owner-only (see `InfoTab`'s
 * `identity` prop), and `native` — which channel Grove's controls take — is a
 * fact the public payload deliberately does not carry, so widening the shared
 * `WorkspaceIdentity` pick for it would push it onto a surface that must not
 * have it.
 */
export function WorkspaceIdentityCard({ state }: { state: WorkspaceStateView }) {
  const RuntimeIcon = runtimeGlyph(state.runtime);
  const runtimeDefault =
    state.runtime === "container" && state.runtime_default_config;
  const statusTerm = statusGlossaryTerm(state.status);
  const base = baseBranchOf(state);
  const whoami = useWhoami();
  const langfuseHost = whoami.data?.langfuse_host;
  const langfuseProjectId = whoami.data?.langfuse_project_id;
  const telemetrySessionId = state.telemetry_session_id;
  const traceHref =
    langfuseHost && langfuseProjectId && telemetrySessionId
      ? `${langfuseHost}/project/${langfuseProjectId}/sessions/${telemetrySessionId}`
      : null;

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
        <CardField label="Session">
          {/* Which channel Grove's controls take is a fixed property of the
              workspace, decided at create — so it renders beside runtime and
              placement as a fact, not as a live state that could change. */}
          <Badge variant="outline" data-testid="workspace-session-mode">
            <Explain term={state.native ? "native_session" : "terminal_session"} />
          </Badge>
        </CardField>
        {traceHref && (
          <CardField label="Traces">
            <a
              href={traceHref}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 underline decoration-dashed underline-offset-2"
            >
              <LangfuseMark className="size-4" />
              Langfuse
            </a>
          </CardField>
        )}
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
