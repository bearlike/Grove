import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import { isToolIconSlug } from "./icon-slug";

function settingsPath(): string {
  const configRoot = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
  return join(configRoot, "grove", "tool-icons.json");
}

/** Reads the optional per-host MCP server icon map without retaining its contents. */
export async function readToolIcons(): Promise<Readonly<Record<string, string>>> {
  try {
    return parseToolIcons(JSON.parse(await readFile(settingsPath(), "utf8")));
  } catch {
    return {};
  }
}

function parseToolIcons(value: unknown): Readonly<Record<string, string>> {
  if (!isFlatStringMap(value)) return {};
  return Object.values(value).every(isToolIconSlug) ? value : {};
}

function isFlatStringMap(value: unknown): value is Record<string, string> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.values(value).every((item) => typeof item === "string");
}
