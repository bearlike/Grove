import "@testing-library/jest-dom/vitest";

// jsdom doesn't ship `ResizeObserver`; radix ScrollArea creates one when its
// scrollbar/thumb mounts (e.g. a userEvent click hover inside a ScrollArea).
if (typeof window !== "undefined" && !window.ResizeObserver) {
  Object.defineProperty(window, "ResizeObserver", {
    writable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}

// jsdom doesn't implement element scrolling; assistant-ui's thread viewport
// (the chat transcript) calls `element.scrollTo` in its autoscroll rAF on every
// mount + message change. Stub it (and its `scrollIntoView` sibling) so the
// transcript renders in component tests instead of throwing an uncaught
// `scrollTo is not a function`.
if (typeof Element !== "undefined") {
  Element.prototype.scrollTo = Element.prototype.scrollTo || (() => {});
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => {});
}

// jsdom doesn't ship `matchMedia`; next-themes calls it during mount.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
