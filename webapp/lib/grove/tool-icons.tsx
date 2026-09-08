"use client";

import { createContext, useContext, type ReactNode } from "react";

const EMPTY_TOOL_ICONS: Readonly<Record<string, string>> = {};
const ToolIconsContext = createContext<Readonly<Record<string, string>>>(EMPTY_TOOL_ICONS);

/** The authenticated shell's configured MCP server marks, or a stable empty map elsewhere. */
export function useToolIcons(): Readonly<Record<string, string>> {
  return useContext(ToolIconsContext);
}

export function ToolIconsProvider({
  children,
  value,
}: {
  readonly children?: ReactNode;
  readonly value: Readonly<Record<string, string>>;
}): ReactNode {
  return <ToolIconsContext.Provider value={value}>{children}</ToolIconsContext.Provider>;
}
