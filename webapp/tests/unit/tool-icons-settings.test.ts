import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ToolIconsProvider, useToolIcons } from "@/lib/grove/tool-icons";
import { readToolIcons } from "@/lib/grove/tool-icons.server";

let configRoot: string;
let previousConfigRoot: string | undefined;

beforeEach(async () => {
  configRoot = await mkdtemp(join(tmpdir(), "grove-tool-icons-"));
  previousConfigRoot = process.env.XDG_CONFIG_HOME;
  process.env.XDG_CONFIG_HOME = configRoot;
});

afterEach(async () => {
  if (previousConfigRoot === undefined) delete process.env.XDG_CONFIG_HOME;
  else process.env.XDG_CONFIG_HOME = previousConfigRoot;
  await rm(configRoot, { force: true, recursive: true });
});

async function writeSettings(value: string): Promise<void> {
  const path = join(configRoot, "grove", "tool-icons.json");
  await mkdir(join(configRoot, "grove"), { recursive: true });
  await writeFile(path, value, "utf8");
}

function ToolIconsValue(): React.ReactNode {
  return createElement("output", null, JSON.stringify(useToolIcons()));
}

describe("tool icon settings", () => {
  it("uses a stable empty context value outside the authenticated shell", () => {
    expect(renderToStaticMarkup(createElement(ToolIconsValue))).toBe("<output>{}</output>");
  });

  it("makes the shell mapping available through its provider", () => {
    const markup = renderToStaticMarkup(
      createElement(
        ToolIconsProvider,
        { value: { "github-server": "simple-icons:github" } },
        createElement(ToolIconsValue),
      ),
    );
    expect(markup).toBe('<output>{&quot;github-server&quot;:&quot;simple-icons:github&quot;}</output>');
  });

  it("reads a flat server-name to Iconify-slug map on every call", async () => {
    await writeSettings('{"github-server":"simple-icons:github"}');
    expect(await readToolIcons()).toEqual({ "github-server": "simple-icons:github" });

    await writeSettings('{"github-server":"simple-icons:gitlab"}');
    expect(await readToolIcons()).toEqual({ "github-server": "simple-icons:gitlab" });
  });

  it.each([
    "[\"simple-icons:github\"]",
    '{"github-server":{"slug":"simple-icons:github"}}',
    '{"github-server":42}',
    '{"github-server":"simple_icons:github"}',
    '{"github-server":"simple-icons:github","nested":{"server":"simple-icons:gitlab"}}',
    "not json",
  ])("fails closed for a malformed mapping: %s", async (contents) => {
    await writeSettings(contents);
    expect(await readToolIcons()).toEqual({});
  });

  it("returns an empty map when the optional settings file is absent", async () => {
    expect(await readToolIcons()).toEqual({});
  });
});
