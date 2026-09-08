import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SendKeysCard } from "@/components/grove/workspace/send-keys";

/**
 * The Send keys card is a row of BUTTONS that happen to contain keycaps, and
 * every assertion here defends that sentence against the shape it used to have.
 *
 * It shipped as `xs` `ghost` buttons: a 24px transparent hit area with a muted
 * keycap floating on it, so the only visible edge on the control belonged to a
 * label. Nothing was wrong with it functionally, which is exactly why it
 * survived review — the failure is that a reader cannot see where the target is.
 *
 * The other half is the chord. `Ctrl + C` is ONE simultaneous key and therefore
 * ONE button and ONE tab stop; two caps inside it are decoration, announced
 * once through the button's own worded name. A chord rendered as two controls
 * would be two sequential sends, which is a different key.
 */
function render(props?: { canInterrupt?: boolean; status?: "active" | "paused" }): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <SendKeysCard
        workspaceId="w1"
        status={props?.status ?? "active"}
        canInterrupt={props?.canInterrupt ?? false}
      />
    </QueryClientProvider>,
  );
}

/** Every `<button>` open tag, so a count is a count of CONTROLS. */
function buttons(html: string): string[] {
  return html.match(/<button[^>]*>/g) ?? [];
}

describe("Send keys buttons", () => {
  it("draws each key as a bounded button, never a keycap on a transparent hit area", () => {
    const html = render();
    const keys = buttons(html).filter((tag) => tag.includes("send-key-"));

    // Eight keys, no interrupt. Information affordances name their regions but
    // are not keys, so this census deliberately identifies controls by their
    // stable delivery test id.
    expect(keys).toHaveLength(8);
    // `outline` is the vendored variant that carries a border and a shadow;
    // `ghost` carries neither, which was the defect.
    expect(keys.every((tag) => tag.includes("border"))).toBe(true);
    expect(keys.every((tag) => tag.includes('data-size="xs"'))).toBe(true);
    expect(keys.every((tag) => tag.includes("min-h-[24px]"))).toBe(true);
  });

  it("puts a chord in ONE button with a separator that is not a third key", () => {
    const html = render();
    const chord = html.match(/<button[^>]*data-testid="send-key-C-c"[\s\S]*?<\/button>/)?.[0];

    expect(chord).toBeDefined();
    expect(chord).toContain("Ctrl");
    expect(chord).toContain(">C<");
    expect(chord).toContain(">+<");
    // Two caps and one separator — the `+` is a plain span, so a screen reader
    // and a hit test both see one control.
    expect(chord?.match(/data-slot="kbd"/g)).toHaveLength(2);
    expect(buttons(chord ?? "")).toHaveLength(1);
    expect(chord).toContain('aria-label="Send Ctrl+C"');
  });

  it("hides the caps behind one worded accessible name per button", () => {
    const html = render();

    for (const [key, name] of [
      ["Up", "Send Up"],
      ["Enter", "Send Enter"],
      ["Escape", "Send Escape"],
      ["C-c", "Send Ctrl+C"],
    ] as const) {
      const tag = buttons(html).find((b) => b.includes(`data-testid="send-key-${key}"`));
      expect(tag, key).toContain(`aria-label="${name}"`);
    }
    // The decorative caps are announced by nothing; the name above is the whole
    // announcement, so `Ctrl C` never reads as a second label after it.
    expect(html.match(/aria-hidden="true"[^>]*data-slot="kbd-group"|data-slot="kbd-group"[^>]*aria-hidden="true"/g))
      .toHaveLength(8);
  });

  it("inverts the keycap against the theme through the token pair, not a branch", () => {
    const html = render();

    // `bg-foreground`/`text-background` ARE the theme's maximum-contrast pair
    // and they swap with it, so a dark cap on light and a light cap on dark is
    // one class each rather than a `dark:` fork that could disagree with itself.
    expect(html).toContain("bg-foreground");
    expect(html).toContain("text-background");
    // Scoped to this card: the vendored default must still be overridden here
    // and left alone everywhere else.
    expect(html).not.toContain("bg-muted text-muted-foreground");
  });

  it("keeps the arrows as a directional cluster and the commands as a list", () => {
    const html = render();

    expect(html).toContain('data-testid="send-keys-arrows"');
    expect(html).toContain('data-testid="send-keys-commands"');
    expect(html).toContain('aria-label="Navigation"');
    // EVERY cell is placed, not just Up. Positioning one and letting the rest
    // auto-flow renders `↑ ←` over `↓ →` — four arrows in a shape that means
    // nothing — and it looks correct in the source, which is why it shipped
    // once and why the census below is per-key rather than per-cluster.
    for (const [key, cell] of [
      ["Up", "col-start-2 row-start-1"],
      ["Left", "col-start-1 row-start-2"],
      ["Down", "col-start-2 row-start-2"],
      ["Right", "col-start-3 row-start-2"],
    ] as const) {
      const tag = buttons(html).find((b) => b.includes(`data-testid="send-key-${key}"`));
      expect(tag, key).toContain(cell);
    }
  });

  it("keeps Cancel turn a separate provider action, never a ninth keycap", () => {
    const html = render({ canInterrupt: true });
    const cancel = html.match(/<button[^>]*data-testid="chat-interrupt"[\s\S]*?<\/button>/)?.[0];

    expect(cancel).toBeDefined();
    expect(cancel).toContain("Cancel turn");
    // No keycap inside it: Cancel turn reaches the provider's own cancellation
    // channel and is neither Ctrl+C nor Escape under another name.
    expect(cancel).not.toContain('data-slot="kbd"');
    expect(buttons(html).filter((tag) => tag.includes("send-key-") || tag.includes("chat-interrupt"))).toHaveLength(9);
  });

  it("disables every key when the workspace cannot take terminal input", () => {
    const html = render({ status: "paused", canInterrupt: true });

    // The card still mounts for the interrupt, and every KEY is disabled — the
    // capability gate is unchanged by the restyle.
    for (const tag of buttons(html)) {
      if (tag.includes("chat-interrupt")) continue;
      expect(tag).toContain("disabled");
    }
  });
});
