"use client";

/** A same-origin embedded workspace panel. */
export function PanelTab({ url, title }: { url: string; title: string }) {
  return (
    <iframe
      src={url}
      title={title}
      allow="clipboard-read; clipboard-write"
      className="min-h-0 w-full flex-1 border-0"
      data-testid="workspace-panel-frame"
    />
  );
}
