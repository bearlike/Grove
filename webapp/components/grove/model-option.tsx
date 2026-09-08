"use client";

import type { ReactNode } from "react";

import type { ModelOption } from "@/components/assistant-ui/model-selector";
import type { ModelOptionView } from "@/lib/grove/api/types";
import { AppIcon } from "@/components/grove/app-icon";
import {
  contextWindowLabel,
  modelIconSlug,
  modelLabel,
  modelNamespace,
} from "@/lib/grove/adapters/model";

/** The brand mark for one model id; a key glyph when no family is recognised. */
export function ModelMark({
  id,
  className,
}: {
  readonly id: string;
  readonly className?: string;
}): ReactNode {
  return <AppIcon slug={modelIconSlug(id)} className={className} data-testid="model-mark" />;
}

/**
 * A catalog entry as the pickers take it: an enriched row, or a bare id.
 *
 * Both shapes are real and neither is a legacy of the other. `GET /models`
 * answers enriched rows for a create surface, while a LIVE session's switch
 * vocabulary is `SessionControlsView.models` — a tuple of strings the agent
 * itself reported, which no catalog endpoint can name or measure.
 */
export type ModelCatalogEntry = ModelOptionView | string;

function entryId(entry: ModelCatalogEntry): string {
  return typeof entry === "string" ? entry : entry.id;
}

/**
 * Enrich a list of bare ids with whatever the catalog knows about each.
 *
 * For a surface whose MEMBERSHIP comes from somewhere the catalog cannot
 * override — the live session's own `--model` vocabulary — while the display
 * facts still come from `GET /models`. The ids decide which rows exist; the
 * catalog decides only what they are called and how much they hold, and an id
 * the catalog has never heard of keeps its own spelling with no window.
 *
 * Joining rather than substituting is what keeps the two honest if they ever
 * disagree: a model the agent can switch to still appears, named or not.
 */
export function enrichedCatalog(
  ids: readonly string[],
  catalog: readonly ModelOptionView[] | undefined,
): readonly ModelCatalogEntry[] {
  if (!catalog?.length) return ids;
  const known = new Map(catalog.map((option) => [option.id, option]));
  return ids.map((id) => known.get(id) ?? id);
}

/**
 * One catalog, read the way every picker shows it.
 *
 * The three model controls used to each decide their own label, mark and
 * search terms; this is the single place a catalog becomes rows. The shared
 * namespace comes back beside the rows so the caller can print it once as a
 * heading rather than per row. Ids are the vendored `ModelOption` ids
 * verbatim — the label is display only, and `keywords` keeps the full id
 * searchable after the namespace has been folded out of the visible text.
 *
 * **Precedence on the name is declared-over-derived, and the fallback is the
 * point.** A canonical name is what an operator SAID this model is called, so
 * it wins; with none, `modelLabel` spells the id the way it always has. That
 * is what lets one component serve a gateway catalog nobody has named yet and
 * a fully named one, with no mode and no second component.
 *
 * `description` carries the context window and is simply absent when no source
 * published one — the vendored row renders a name-only item for those, so an
 * unmeasured model is a shorter row rather than a row claiming zero.
 */
export function modelOptions(catalog: readonly ModelCatalogEntry[]): {
  readonly namespace: string;
  readonly options: readonly ModelOption[];
} {
  const namespace = modelNamespace(catalog.map(entryId));
  return {
    namespace,
    options: catalog.map((entry) => {
      const id = entryId(entry);
      const declared = typeof entry === "string" ? null : entry.name;
      const window = typeof entry === "string" ? null : contextWindowLabel(entry.context_window);
      return {
        id,
        name: declared ?? modelLabel(id, namespace),
        icon: <ModelMark id={id} />,
        // The full id stays searchable whatever the row is called: a reader who
        // knows the provider's spelling must still find the row that now shows
        // a canonical name, and the namespace fold has hidden part of it too.
        keywords: [id],
        ...(window ? { description: window } : {}),
      };
    }),
  };
}
