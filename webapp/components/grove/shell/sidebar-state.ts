"use client";

import { useEffect } from "react";
import { create } from "zustand";

/** Where the collapse preference is remembered across navigations and reloads. */
const STORAGE_KEY = "grove.sidebar.collapsed";

interface SidebarUi {
  readonly collapsed: boolean;
  readonly mobileOpen: boolean;
  setCollapsed(collapsed: boolean): void;
  toggleCollapsed(): void;
  setMobileOpen(open: boolean): void;
}

/**
 * Whether the rail is collapsed, and whether the mobile sheet is open.
 *
 * A store rather than props because the toggle and the rail have no common
 * parent below the shell: the header is rendered by each PAGE (so a page can
 * supply its own title and actions) while the rail is rendered by the layout.
 *
 * The default is EXPANDED, unlike assistant-ui's own demo. That demo is a
 * single thread, so its list is nearly always noise; a Grove fleet is the
 * thing you navigate all day.
 */
export const useSidebarUi = create<SidebarUi>((set) => ({
  collapsed: false,
  mobileOpen: false,
  setCollapsed: (collapsed) => set({ collapsed: remember(collapsed) }),
  toggleCollapsed: () => set((state) => ({ collapsed: remember(!state.collapsed) })),
  setMobileOpen: (mobileOpen) => set({ mobileOpen }),
}));

function remember(collapsed: boolean): boolean {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, String(collapsed));
  }
  return collapsed;
}

/**
 * Restore the remembered collapse state and bind ⌘B / Ctrl+B. Call ONCE, from
 * the shell.
 *
 * The restore is an effect rather than the store's initial value on purpose:
 * reading `localStorage` during render would make the server's HTML and the
 * client's first paint disagree, which React reports as a hydration mismatch.
 * The rail therefore renders expanded for one frame before flipping — the
 * width transition makes that read as the rail opening, not as a glitch.
 */
export function useSidebarShortcuts(): void {
  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored !== null) useSidebarUi.setState({ collapsed: stored === "true" });

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== "b" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      useSidebarUi.getState().toggleCollapsed();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}
