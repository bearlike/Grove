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
