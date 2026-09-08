"use client";

import { useLayoutEffect, useRef, useState, type ComponentProps } from "react";
import type { LucideIcon } from "lucide-react";

import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

type LabelMode = "all" | "active" | "icons";
type TabWidth = { full: number; icon: number };

/** Reserve the longest selected label so choosing a tab cannot change density. */
export function tabLabelMode(width: number, tabs: readonly TabWidth[], gap: number): LabelMode {
  const gaps = Math.max(0, tabs.length - 1) * gap;
  if (tabs.reduce((sum, tab) => sum + tab.full, gaps) <= width) return "all";
  const icons = tabs.reduce((sum, tab) => sum + tab.icon, gaps);
  const label = Math.max(0, ...tabs.map((tab) => tab.full - tab.icon));
  return icons + label <= width ? "active" : "icons";
}

/** Fit labels to this row, including configured panels and the reader's font size. */
export function AdaptiveTabsList({ className, children, ...props }: ComponentProps<typeof TabsList>) {
  const ref = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<LabelMode>("icons");

  useLayoutEffect(() => {
    const row = ref.current;
    if (!row) return;
    const list = row.querySelector<HTMLElement>('[role="tablist"]');
    if (!list) return;
    const triggers = [...list.querySelectorAll<HTMLElement>('[role="tab"]')];
    const measure = () => {
      if (!list.clientWidth) return;
      const style = getComputedStyle(list);
      const width = list.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const tabs = triggers.map((trigger) => {
        const css = getComputedStyle(trigger);
        const label = trigger.querySelector<HTMLElement>("[data-tab-label] > span");
        const icon = trigger.querySelector("svg");
        const chrome = parseFloat(css.paddingLeft) + parseFloat(css.paddingRight)
          + parseFloat(css.borderLeftWidth) + parseFloat(css.borderRightWidth);
        const glyph = icon?.getBoundingClientRect().width ?? 0;
        const min = parseFloat(css.minWidth) || 0;
        return {
          icon: Math.max(min, chrome + glyph),
          full: Math.max(min, chrome + glyph + parseFloat(css.columnGap) + (label?.getBoundingClientRect().width ?? 0)),
        };
      });
      setMode(tabLabelMode(width, tabs, parseFloat(style.columnGap) || 0));
    };
    // Labels remain measurable while clipped. Observing them covers font loading
    // and reader scaling without making the selected underline depend on JS.
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    for (const trigger of triggers) {
      const label = trigger.querySelector("[data-tab-label] > span");
      const icon = trigger.querySelector("svg");
      if (label) observer.observe(label);
      if (icon) observer.observe(icon);
    }
    measure();
    return () => observer.disconnect();
  }, [children]);

  return (
    <div ref={ref} className={cn("workspace-tab-strip min-w-0", className)}>
      <TabsList {...props} variant="line" className="workspace-tab-list" data-label-mode={mode}>
        {children}
      </TabsList>
    </div>
  );
}

export function AdaptiveTabsTrigger({
  label, icon: Icon, ...props
}: Omit<ComponentProps<typeof TabsTrigger>, "children"> & { label: string; icon: LucideIcon }) {
  return (
    <Tooltip>
      <TabsTrigger {...props} asChild aria-label={label}>
        <TooltipTrigger>
          <Icon aria-hidden />
          <span data-tab-label aria-hidden><span>{label}</span></span>
        </TooltipTrigger>
      </TabsTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
