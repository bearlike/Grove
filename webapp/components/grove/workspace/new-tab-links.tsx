"use client";

import { useEffect, useRef } from "react";

/**
 * Every navigational link inside RESPONSE CONTENT opens in a new tab, so
 * following one never costs the reader the session they were reading.
 *
 * A Grove workspace page is a long-lived thing: a streaming transcript, a
 * scroll position twenty thousand pixels down, a terminal attached, a work
 * panel on the tab you chose. A link in an agent's answer that replaces all of
 * that is not a navigation, it is a loss — and the agent citing a doc has no
 * way to know what it costs you to read it.
 *
 * **WHY THIS IS A CONTAINER AND NOT A LINK COMPONENT.** Response content is not
 * one renderer. It is the vendored Markdown renderer, the vendored reasoning
 * renderer, tool-call cards, data-part cards and mailbox bodies — and the
 * Markdown one hard-codes `components={defaultComponents}` with no prop to
 * reach its `a`. Fixing the anchor per renderer means porting a vendored file
 * for one attribute AND still missing every card, which is the overfit: the
 * requirement is about a REGION of the page, so the region is what owns it.
 * One mount covers everything inside, including renderers nobody has written
 * yet.
 *
 * **IT SETS ATTRIBUTES; IT DOES NOT INTERCEPT CLICKS.** `target`/`rel` on a
 * real anchor is what preserves middle-click, ⌘-click, Copy link address, the
 * keyboard's own activation and a screen reader's link list — all of which a
 * `preventDefault` plus `window.open` would quietly take away while looking
 * identical in a demo. Nothing here parses, rewrites or sanitizes an `href`:
 * the renderer above still decides what a link may be, and this only decides
 * where an already-permitted one lands.
 *
 * SCOPE, stated because the exclusions are deliberate:
 *   - In-page fragments (`#section`) stay in place — that is not a navigation.
 *   - `mailto:`, `tel:` and any other scheme are left alone. A handler outside
 *     the browser has no tab to open, and adding `target` to a scheme this
 *     does not recognise is a guess.
 *   - Anything already marked `data-new-tab="off"` opts out, which is how an
 *     in-place control inside response content says so at its own call site.
 *   - App chrome — the rail, the pane switcher, the account menu — is not
 *     response content and is not wrapped. Router navigation there is the
 *     feature.
 */
export function NewTabLinks({ children, ...props }: React.ComponentProps<"div">) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;

    const apply = () => {
      for (const anchor of root.querySelectorAll<HTMLAnchorElement>("a[href]")) {
        if (anchor.dataset.newTab) continue;
        if (!opensInNewTab(anchor.getAttribute("href"))) continue;
        anchor.target = "_blank";
        // `noopener` is the security half — a `_blank` target hands the opened
        // page a live `window.opener` on the one it came from — and `noreferrer`
        // is the privacy half. Browsers imply the first for `_blank` now; it is
        // written out because a rule you can read beats a default you have to
        // remember, and because `rel` is what a reviewer greps for.
        anchor.rel = "noopener noreferrer";
        anchor.dataset.newTab = "on";
        // The accessible indication, as ATTRIBUTES only. Appending a visually
        // hidden span would be the conventional answer and is not available
        // here: these anchors are React-owned and the Markdown one re-renders
        // on every streamed token, so an extra child is a reconciliation fault
        // waiting for the next chunk. `title` is left alone when the renderer
        // already set one — a ticket row's title is its own text and is worth
        // more than this hint.
        if (!anchor.title) anchor.title = OPENS_IN_NEW_TAB;
        if (!anchor.getAttribute("aria-description")) {
          anchor.setAttribute("aria-description", OPENS_IN_NEW_TAB);
        }
      }
    };

    apply();
    // Response content STREAMS, so the anchors that matter mostly do not exist
    // at mount. Observing the subtree is what makes this a property of the
    // region rather than of whatever happened to have rendered by first paint.
    const observer = new MutationObserver(apply);
    observer.observe(root, { childList: true, subtree: true, attributeFilter: ["href"] });
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} {...props}>
      {children}
    </div>
  );
}

const OPENS_IN_NEW_TAB = "Opens in a new tab";

/**
 * Whether an `href` is a navigation this region should send to a new tab.
 *
 * ALLOWLISTED, NOT DENYLISTED, and that is the whole safety argument: a
 * denylist of `javascript:` and `data:` is a list somebody has to keep
 * complete, while "http, https, or a path" is a rule that cannot silently
 * admit a scheme nobody thought of. Sanitization still belongs to the renderer
 * above — this only declines to decorate what it does not recognise.
 *
 * Relative and same-origin links are INCLUDED. A response citing another Grove
 * page is exactly the case the reader loses their session to, and it is the one
 * a naive "external links only" rule misses.
 */
export function opensInNewTab(href: string | null | undefined): boolean {
  if (!href) return false;
  const trimmed = href.trim();
  // An in-page fragment is a scroll, not a navigation.
  if (trimmed === "" || trimmed.startsWith("#")) return false;
  if (trimmed.startsWith("//")) return true;
  if (trimmed.startsWith("/") || trimmed.startsWith("./") || trimmed.startsWith("../")) {
    return true;
  }
  // A bare `word:` prefix is a scheme; only the two that name a page qualify.
  const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(trimmed)?.[1]?.toLowerCase();
  if (scheme) return scheme === "http" || scheme === "https";
  // No scheme and no leading slash — a relative path such as `docs/setup`.
  return true;
}
