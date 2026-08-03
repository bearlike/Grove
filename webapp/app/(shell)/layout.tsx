"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Header } from "@/components/layout/header";
import { HeaderSlotContext } from "@/components/layout/header-slot";
import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useUiStore } from "@/lib/grove/ui-store";
import { cn } from "@/lib/utils";

/**
 * The ONE app shell. Every non-login route lives in this group — the list
 * surface (`/`, the `/activity` redirect) AND the session page (`/w/[id]`) —
 * so they share ONE header + ONE persistent left rail. The session page is
 * the three-zone layout `rail | transcript | work-panel`, where the rail is
 * this shared container.
 *
 * VIEWPORT MATH (single source of truth): the only fixed chrome is the h-13
 * header = 52px = `3.25rem`. A fixed-height page (the session page) fills
 * `calc(100dvh - 3.25rem)`; the sticky rail is the same height and pins below
 * the `top-13` header. Change `3.25rem`/`top-13` together if the header
 * height ever moves — they are the ONLY two references.
 *
 * The header lives here (not in the pages), so a page fills the header's middle
 * by portaling into the slot the header exposes via `contextSlotRef`; this layout
 * captures that node and publishes it through `HeaderSlotContext`. `back` is
 * route-derived (only the session page needs it) — no page has to pass it.
 *
 * Sidebar collapse is owned by the ONE client-state store: `sidebarCollapsed`
 * is persisted, `toggleSidebar` flips it, and `hydrated` gates the persisted value
 * so SSR + first paint render expanded (markup matches) and the width transition
 * disguises the flip on mount. The `[` shortcut (ignored while a field is focused)
 * lives here too.
 */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [slotEl, setSlotEl] = useState<HTMLElement | null>(null);
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const hydrated = useUiStore((s) => s.hydrated);
  const toggle = useUiStore((s) => s.toggleSidebar);
  const railCollapsed = hydrated && collapsed;

  // Only the session page wants an explicit back arrow; derive it from the route
  // so no page has to thread the prop up into this shared header.
  const pathname = usePathname();
  const back = pathname?.startsWith("/w/") ?? false;

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
        contextSlotRef={setSlotEl}
        back={back}
      />
      {/* Full-bleed shell: the rail hugs the viewport's left edge and runs the
          full height below the header. Content fills `<main>`; each page decides
          its own inner width/scroll (the list surface centers + scrolls; the
          session page is a fixed-height three-zone that scrolls inside its panes). */}
      <div className="flex w-full">
        {/* The rail's width animates on a wrapper; collapsing narrows it to
            `w-0` so the rail hides ENTIRELY (modern-chat behavior) — the
            header toggle + `[` reopen it. The inner aside keeps a stable `w-70`
            (never re-flowing to match the wrapper) so its content doesn't
            re-wrap mid-animation; `overflow-hidden` clips it as the wrapper
            closes. The `<main>` flexes to fill the rest. */}
        <div
          // `w-0 overflow-hidden` clips the rail visually but leaves its
          // controls in the tab order + a11y tree — `inert` removes both, so
          // a collapsed rail can't swallow keyboard focus (WCAG 2.4.3/2.4.7).
          inert={railCollapsed || undefined}
          className={cn(
            "sticky top-13 hidden h-[calc(100dvh-3.25rem)] shrink-0 overflow-hidden transition-[width] duration-200 ease-out lg:block",
            railCollapsed ? "w-0" : "w-70",
          )}
        >
          <WorkspaceSidebar className="h-full w-70 border-r border-sidebar-border bg-sidebar" />
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
        <HeaderSlotContext.Provider value={slotEl}>
          <main className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
        </HeaderSlotContext.Provider>
      </div>
    </>
  );
}
