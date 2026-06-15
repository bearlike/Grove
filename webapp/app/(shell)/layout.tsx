"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/layout/header";
import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useUiStore } from "@/lib/grove/ui-store";
import { cn } from "@/lib/utils";

/**
 * App shell for the list surface (`/` and the `/activity` redirect): the shared
 * `Header`, a persistent left `WorkspaceSidebar` (nav/scope only — #96), and the
 * route content in `<main>`. `/w/[id]` lives OUTSIDE this group (its own
 * full-bleed IDE shell). The global `StatusBar` (root layout) spans every route.
 *
 * Sidebar collapse is owned by the ONE client-state store (#96, replacing the
 * old `useSidebarState`): `sidebarCollapsed` is persisted, `toggleSidebar` flips
 * it, and `hydrated` gates the persisted value so SSR + first paint render
 * expanded (markup matches) and the width transition disguises the flip on
 * mount. The `[` shortcut (ignored while a field is focused) lives here too.
 */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const hydrated = useUiStore((s) => s.hydrated);
  const toggle = useUiStore((s) => s.toggleSidebar);
  const railCollapsed = hydrated && collapsed;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "[" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return;
      e.preventDefault();
      toggle();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggle]);

  return (
    <>
      <Header
        onOpenSidebar={() => setOpen(true)}
        onToggleSidebar={toggle}
        sidebarCollapsed={railCollapsed}
      />
      {/* Full-bleed shell: the rail hugs the viewport's left edge and runs the
          full height (no outer max-width centering it into the middle on wide
          screens). Content is re-centered INSIDE `<main>` at a generous cap, so
          ultrawide viewports read as "rail flush left · wide centered canvas",
          not "everything stranded in a narrow middle column". */}
      <div className="flex w-full">
        {/* The rail's width animates on a wrapper so the inner sidebar keeps a
            stable width (its content never reflows mid-animation); collapsing
            clips it to 0 and the `<main>` flexes to fill. */}
        <div
          className={cn(
            "sticky top-12 hidden h-[calc(100dvh-4.75rem)] shrink-0 overflow-hidden transition-[width] duration-200 ease-out lg:block",
            railCollapsed ? "w-0" : "w-64",
          )}
        >
          <WorkspaceSidebar className="h-full w-64 border-r border-sidebar-border bg-sidebar" />
        </div>
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="w-72 p-0">
            <SheetTitle className="sr-only">Workspace navigation</SheetTitle>
            <WorkspaceSidebar
              className="h-full w-full bg-sidebar"
              onClose={() => setOpen(false)}
              onNavigate={() => setOpen(false)}
            />
          </SheetContent>
        </Sheet>
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="mx-auto flex w-full max-w-[100rem] flex-1 flex-col">{children}</div>
        </main>
      </div>
    </>
  );
}
