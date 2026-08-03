import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Response } from "@/components/ai-elements/response";

// The transcript (and the composer's fullscreen preview) render agent prose
// through this ONE `Response` = streamdown mount. Fenced code flattens
// streamdown's stock card-within-a-card into the assistant-ui single well (no
// download affordance, no line-number gutters, no floating pill — just a slim
// header + one bordered body); markdown TABLES flatten the same way (no
// nested bordered shell, no copy/download/fullscreen controls row — one framed
// table with a muted header cap). streamdown's own `controls`/`lineNumbers`
// props carry the semantic removals; these tests pin that contract at the DOM
// so a version bump (or a prop drop) that resurrects the chrome fails the build.

const FENCE = ["```ts", "const x: number = 1;", "console.log(x);", "```"].join("\n");

/** Render a fenced block and wait for streamdown's lazy body to settle. */
async function renderFence(markdown = FENCE) {
  const { container } = render(<Response>{markdown}</Response>);
  // The copy control is synchronous (it lives in the header actions, not the
  // Suspense-lazy body) — awaiting it settles the render without racing shiki.
  await screen.findByTitle("Copy Code");
  return container;
}

describe("Response fenced code — assistant-ui single-well shape (#160)", () => {
  it("renders the native code block but drops the download affordance", async () => {
    const container = await renderFence();

    // The block still renders through streamdown's native CodeBlock.
    expect(container.querySelector('[data-streamdown="code-block"]')).not.toBeNull();
    expect(container.querySelector('[data-streamdown="code-block-body"]')).not.toBeNull();

    // Download is gone (controls.code.download:false) — by data-attr AND title.
    expect(container.querySelector('[data-streamdown="code-block-download-button"]')).toBeNull();
    expect(screen.queryByTitle("Download file")).toBeNull();
  });

  it("keeps ONE quiet copy control", async () => {
    const container = await renderFence();

    const copy = container.querySelector('[data-streamdown="code-block-copy-button"]');
    expect(copy).not.toBeNull();
    // Exactly one — not a two-button pill.
    expect(container.querySelectorAll('[data-streamdown="code-block-copy-button"]')).toHaveLength(1);
    // Quiet by tone: muted at rest, foreground on hover (streamdown's own quiet
    // default, which the flatten deliberately preserves).
    expect(copy!.className).toContain("text-muted-foreground");
  });

  it("emits NO line-number gutters (no ::before counter markers)", async () => {
    const container = await renderFence();

    // `lineNumbers={false}` means the <code> and its line <span>s never receive
    // the `[counter-*]` / `before:content-[counter(line)]` classes that draw the
    // gutter. Absence of any counter-bearing class IS the contract.
    const body = container.querySelector('[data-streamdown="code-block-body"]')!;
    expect(body.querySelectorAll('[class*="counter"]')).toHaveLength(0);
    expect(container.querySelectorAll('[class*="counter"]')).toHaveLength(0);
  });

  it("wires the single-well flatten onto the streamdown mount (class shape)", async () => {
    const container = await renderFence();

    // The overrides live on the streamdown root (the rendered outermost node);
    // pin the load-bearing ones so a regression that unflattens the block —
    // reintroducing the bg-sidebar shell or the nested body border — is caught.
    const root = container.firstElementChild!;
    const cls = root.className as string;
    expect(cls).toContain("[&_[data-streamdown=code-block]]:bg-transparent");
    expect(cls).toContain("[&_[data-streamdown=code-block]]:border-0");
    expect(cls).toContain("[&_[data-streamdown=code-block-header]]:bg-muted/50");
    expect(cls).toContain("[&_[data-streamdown=code-block-body]]:bg-muted/30");
    expect(cls).toContain("[&_[data-streamdown=code-block-body]]:border-border/50");
  });
});

const TABLE = ["| Col A | Col B |", "| --- | --- |", "| 1 | 2 |", "| 3 | 4 |"].join("\n");

/** Render a markdown table (synchronous — no Suspense-lazy body like code). */
async function renderTable(markdown = TABLE) {
  const { container } = render(<Response>{markdown}</Response>);
  await screen.findByText("Col A");
  return container;
}

describe("Response markdown tables — single-frame grammar (#162)", () => {
  it("renders the native table but drops the whole controls row", async () => {
    const container = await renderTable();

    // Still streamdown's native table (wrapper + table + header cells).
    const wrapper = container.querySelector('[data-streamdown="table-wrapper"]');
    expect(wrapper).not.toBeNull();
    expect(container.querySelector('[data-streamdown="table"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-streamdown="table-header-cell"]')).toHaveLength(2);

    // controls.table:false ⇒ NO copy/download/fullscreen row. The only chrome
    // streamdown puts in the wrapper is that button row, so zero buttons inside
    // the wrapper IS the "controls gone" contract.
    expect(wrapper!.querySelectorAll("button")).toHaveLength(0);
  });

  it("wires the single-frame flatten onto the streamdown mount (class shape)", async () => {
    const container = await renderTable();

    // Pin the load-bearing overrides so a regression that unflattens the table —
    // reviving the bg-sidebar shell, the nested bg-background scroll card, or the
    // full-strength dividers — is caught.
    const cls = container.firstElementChild!.className as string;
    // Wrapper chrome shell stripped.
    expect(cls).toContain("[&_[data-streamdown=table-wrapper]]:bg-transparent");
    expect(cls).toContain("[&_[data-streamdown=table-wrapper]]:border-0");
    // The scroll div that parents the <table> is the ONE hairline frame.
    expect(cls).toContain("[&_div:has(>[data-streamdown=table])]:border-border/50");
    expect(cls).toContain("[&_div:has(>[data-streamdown=table])]:bg-transparent");
    // Muted header cap + hairline internal dividers.
    expect(cls).toContain("[&_[data-streamdown=table-header]]:bg-muted");
    expect(cls).toContain("[&_[data-streamdown=table]]:divide-border/50");
    expect(cls).toContain("[&_[data-streamdown=table-body]]:divide-border/50");
  });
});
