import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ComposerModel } from "@/components/grove/workspace/composer-model";

const controls = vi.hoisted(() => ({
  data: {
    current_model: null as string | null,
    models: ["acme-sonnet", "acme-opus"],
    permission_mode: "acceptEdits",
  },
}));

// The catalog is the DISPLAY source only. It deliberately declares a name and a
// window for `acme-opus` and mentions `acme-ghost`, which the session cannot
// switch to — so a test that saw `acme-ghost` in the menu would be watching the
// catalog decide membership, which is the defect this fixture exists to catch.
const catalog = vi.hoisted(() => ({
  data: [
    { id: "acme-opus", name: "Acme Opus", context_window: 1_000_000 },
    { id: "acme-ghost", name: "Acme Ghost", context_window: 500_000 },
  ] as { id: string; name: string | null; context_window: number | null }[],
}));

vi.mock("@/lib/grove/hooks", () => ({
  useSessionControls: () => ({ data: controls.data, isPending: false }),
  useSwitchModel: () => ({ isPending: false, mutate: vi.fn() }),
  useWorkspace: () => ({ data: { repo_root: "/repo", agent_name: "acme" } }),
  useModels: () => ({ data: catalog.data }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/components/assistant-ui/model-selector", () => {
  function Root({ children }: { readonly children: ReactNode }) {
    return <div data-slot="model-selector-root">{children}</div>;
  }

  function Trigger({
    children,
    variant: _variant,
    size: _size,
    ...props
  }: {
    readonly children: ReactNode;
    readonly variant?: string;
    readonly size?: string;
  }) {
    return <button {...props}>{children}</button>;
  }

  function Content({
    children,
    align: _align,
    searchable: _searchable,
    ...props
  }: {
    readonly children: ReactNode;
    readonly align?: string;
    readonly searchable?: boolean;
  }) {
    return <section data-slot="model-selector-content" {...props}>{children}</section>;
  }

  function Search() {
    return <input data-slot="model-selector-search" aria-label="Search models" />;
  }

  function List({ children }: { readonly children: ReactNode }) {
    return <div data-slot="model-selector-list">{children}</div>;
  }

  function Empty() {
    return <div>No models found.</div>;
  }

  function Group({
    children,
    heading,
  }: {
    readonly children: ReactNode;
    readonly heading?: string;
  }) {
    return <div data-heading={heading}>{children}</div>;
  }

  function Item({
    model,
    children,
    ...props
  }: {
    readonly model: { id: string; name: string };
    readonly children?: ReactNode;
  }) {
    return <button data-model-id={model.id} {...props}>{children ?? model.name}</button>;
  }

  return {
    ModelSelector: { Root, Trigger, Content, Search, List, Empty, Group, Item },
  };
});

describe("ComposerModel", () => {
  it("keeps the fallback on the trigger and renders only provider options in its menu", () => {
    const html = renderToStaticMarkup(<ComposerModel workspaceId="ws-1" />);
    const menuStart = html.indexOf('data-testid="composer-model-menu"');
    const menuEnd = html.indexOf("</section>", menuStart);
    expect(menuStart).toBeGreaterThan(0);
    expect(menuEnd).toBeGreaterThan(menuStart);
    const menu = html.slice(menuStart, menuEnd);

    // The trigger's fallback names the real omitted-model value. The menu must
    // not repeat it as informational pseudo-option beside choices that act.
    expect(html).toMatch(/data-testid="composer-model-trigger"[^>]*>.*Agent default/);
    expect(menu).not.toContain('data-testid="composer-model-default"');
    expect(menu).not.toContain("Agent default");
    expect(menu).not.toContain("New sessions");

    expect(menu).toContain('data-slot="model-selector-search"');
    expect(menu).toContain('data-heading="acme-"');
    expect(menu).toContain('data-model-id="acme-sonnet"');
    expect(menu).toContain('data-model-id="acme-opus"');
    expect(menu).toContain("Permission mode");

    // The heading carries the namespace once, so a row prints only what is
    // left of its id — the full id stays hoverable and searchable.
    expect(menu).toMatch(/data-model-id="acme-sonnet"[^>]*>Sonnet</);
    expect(menu).toContain('title="acme-sonnet"');
  });

  it("marks the trigger with the reported model's brand and its declared name", () => {
    controls.data.current_model = "acme-opus";
    try {
      const html = renderToStaticMarkup(<ComposerModel workspaceId="ws-1" />);
      const start = html.indexOf('data-testid="composer-model-trigger"');
      const trigger = html.slice(start, html.indexOf("</button>", start));
      // A reported model is a real id, so it reads as a mark plus a name rather
      // than the raw string the fallback branch prints — and where the catalog
      // declares one for that exact id, the declared name is what shows.
      expect(trigger).toContain('data-testid="model-mark"');
      expect(trigger).toContain(">Acme Opus<");
      expect(trigger).not.toContain("acme-opus<");
    } finally {
      controls.data.current_model = null;
    }
  });

  it("falls back to the folded id when the catalog declares no name for it", () => {
    // `current_model` is a PROVIDER report and routinely names something the
    // switch catalog never lists (measured: 9 matches in ~10,900 real assistant
    // messages). That is the ordinary case, and it must read as the id the
    // agent actually said rather than as a blank or a neighbouring model's name.
    controls.data.current_model = "acme-sonnet";
    try {
      const html = renderToStaticMarkup(<ComposerModel workspaceId="ws-1" />);
      const start = html.indexOf('data-testid="composer-model-trigger"');
      const trigger = html.slice(start, html.indexOf("</button>", start));
      expect(trigger).toContain(">Sonnet<");
      expect(trigger).not.toContain("Acme Opus");
    } finally {
      controls.data.current_model = null;
    }
  });

  it("never lets the catalog add a model the session cannot switch to", () => {
    // `acme-ghost` is in the catalog fixture and NOT in the session's
    // vocabulary. A menu offering it would be the catalog deciding membership.
    const html = renderToStaticMarkup(<ComposerModel workspaceId="ws-1" />);
    expect(html).not.toContain("acme-ghost");
    expect(html).not.toContain("Acme Ghost");
  });
});
