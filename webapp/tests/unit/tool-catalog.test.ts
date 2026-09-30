import { describe, expect, it } from "vitest";

import {
  compactToolTarget,
  toolPresentation,
  toolTimelineStats,
  toolTimelineSummary,
  UNKNOWN_SERVER_ICON,
  UNKNOWN_TOOL_ICON,
} from "@/lib/grove/adapters/tool-catalog";

describe("compact action targets", () => {
  it("shows only the command prefix while leaving full invocation data intact", () => {
    const command = "git add src/first.ts src/second.ts && git commit -m 'long message'";
    const step = toolPresentation("Bash", { command });
    expect(compactToolTarget(step)).toBe("git add …");
    expect(step.chip).toBe(command);
    expect(compactToolTarget(toolPresentation("Bash", { command: "pwd" }))).toBe("pwd");
    expect(compactToolTarget(toolPresentation("Bash", { command: "make lint\nprintf done" }))).toBe("make lint …");
  });

  it("bounds oversized keywords and templated inputs", () => {
    const step = toolPresentation("Bash", { command: `echo ${"${VERY_LONG_VARIABLE}".repeat(30)}` });
    expect(compactToolTarget(step).length).toBeLessThanOrEqual(48);
    expect(compactToolTarget(step)).toMatch(/^echo .*…$/);
    expect(compactToolTarget(toolPresentation("Skill", { skill: "a".repeat(300) })).length).toBeLessThanOrEqual(48);
  });

  it("summarizes argv commands and file basenames without changing distinct-path counts", () => {
    expect(compactToolTarget(toolPresentation("exec_command", { cmd: ["bash", "-lc", "long command"] }))).toBe("bash -lc …");
    const reads = ["/work/project/src/app.ts", "/work/project/test/app.ts"].map(file_path => toolPresentation("Read", { file_path }));
    expect(reads.map(compactToolTarget)).toEqual(["app.ts", "app.ts"]);
    expect(compactToolTarget(toolPresentation("Read", { file_path: "C:\\work\\src\\app.ts" }))).toBe("app.ts");
    expect(toolTimelineSummary(reads).label).toBe("2 steps · 2 files read");
  });
});

describe("zero-free timeline summaries", () => {
  it("omits file counts for command-only groups", () => {
    expect(toolTimelineSummary([toolPresentation("Bash", { command: "pwd" })]).label)
      .toBe("1 step · 1 command");
  });

  it("uses the compact action phrase for one non-command call", () => {
    expect(toolTimelineSummary([toolPresentation("Skill", { skill: "verification" })]).label)
      .toBe("Loaded skill verification");
    expect(toolTimelineSummary([toolPresentation("mcp__docs__lookup")]).label)
      .toBe("Called docs / lookup");
  });

  it("closes the phrase when a run has no category to report", () => {
    // Not `2 tool calls`: with every category empty the tally IS the summary,
    // so it has to read as a sentence rather than as a count missing its half.
    expect(toolTimelineSummary([toolPresentation("Skill"), toolPresentation("ListAgents")]).label)
      .toBe("2 steps executed");
    expect(toolTimelineSummary([])).toEqual({ label: "Tool activity", icons: [] });
  });
});

describe("per-file change summary", () => {
  const edit = (file: string, added: number, removed: number) => ({
    ...toolPresentation("Edit", { file_path: file }),
    fileStat: { file, added, removed },
  });

  it("reports each edited file once, summing every edit to it", () => {
    expect(toolTimelineStats([
      edit("/w/src/composer.tsx", 10, 2),
      edit("/w/src/composer.tsx", 4, 1),
      edit("/w/src/use-draft.ts", 42, 0),
    ])).toEqual([
      { file: "composer.tsx", added: 14, removed: 3 },
      { file: "use-draft.ts", added: 42, removed: 0 },
    ]);
  });

  it("counts changed files in the label and omits the chips for a run with none", () => {
    const steps = [toolPresentation("Bash", { command: "npm test" }), edit("/w/a.ts", 3, 1)];
    expect(toolTimelineSummary(steps).label).toBe("2 steps · 1 command · 1 file changed");
    expect(toolTimelineStats([toolPresentation("Bash", { command: "pwd" })])).toEqual([]);
  });
});

describe("tool presentation catalog", () => {
  it("preserves argv-shaped commands without pretending they are shell text", () => {
    expect(toolPresentation("exec_command", { cmd: ["bash", "-lc", "a && b"] }).chip)
      .toBe('["bash","-lc","a && b"]');
  });

  it("ignores inherited keys in supplementary server mappings", () => {
    expect(toolPresentation("mcp__constructor__run").icon).toBe(UNKNOWN_SERVER_ICON);
    expect(toolPresentation("mcp__toString__run").icon).toBe(UNKNOWN_SERVER_ICON);
  });
  it("labels real command arguments rather than a digest or tool name", () => {
    expect(toolPresentation("Bash", { command: "npm test", description: "Run tests" }, "digest"))
      .toMatchObject({ verb: "Ran", chip: "npm test", icon: "material-icon-theme:console", kind: "command" });
    expect(toolPresentation("functions.exec_command", { cmd: "pwd" }))
      .toMatchObject({ verb: "Ran", chip: "pwd", kind: "command" });
  });

  it("uses explicit file-read arguments, without interpreting shell commands", () => {
    expect(toolPresentation("Read", { file_path: "src/main.ts" }))
      .toMatchObject({ verb: "Read", chip: "src/main.ts", filePath: "src/main.ts", kind: "read" });
    expect(toolPresentation("Bash", { command: "cat src/main.ts" }).filePath).toBeNull();
  });

  it("gives a synthetic mailbox delivery its dedicated mark", () => {
    expect(toolPresentation("Mailbox")).toMatchObject({
      verb: "Received message",
      icon: "fluent-color:mail-24",
      kind: "tool",
    });
  });

  it("resolves a configured MCP server without confusing its function with a builtin", () => {
    expect(toolPresentation("mcp__docs__Read", { query: "API" }, "", { docs: "simple-icons:readthedocs" }))
      .toMatchObject({ verb: "Called", chip: "docs / Read", icon: "simple-icons:readthedocs", kind: "tool" });
    expect(toolPresentation("mcp__other__Read", null, "", { docs: "lucide:book" }).icon).toBe(UNKNOWN_SERVER_ICON);
  });

  it("retains unknown names and digest-only targets without inventing details", () => {
    expect(toolPresentation("FutureTool", null, "some target"))
      .toMatchObject({ verb: "Called", chip: "FutureTool · some target", icon: UNKNOWN_TOOL_ICON });
    expect(toolPresentation("Read", null, "src/main.ts"))
      .toMatchObject({ chip: "src/main.ts", filePath: null });
  });

  it("counts steps, distinct recorded read paths, and command invocations independently", () => {
    const steps = [
      toolPresentation("Read", { file_path: "a.ts" }),
      toolPresentation("Read", { file_path: "a.ts" }),
      toolPresentation("Read", { file_path: "b.ts" }),
      toolPresentation("Bash", { command: "pwd" }),
      toolPresentation("functions.write_stdin", { session_id: 1, chars: "" }),
    ];
    expect(toolTimelineSummary(steps)).toEqual({
      label: "5 steps · 1 command · 2 files read",
      icons: ["fluent-color:book-open-24", "material-icon-theme:console"],
    });
  });

  it("gives reading, writing and editing a file three different marks", () => {
    // Three verbs on one noun were three look-alike page glyphs, so a run of
    // reads and edits read as one repeated action. The shapes must differ, not
    // only the tints: a reader scanning a stack sees silhouette first.
    const marks = ["Read", "Write", "Edit"].map(name => toolPresentation(name, { file_path: "a.ts" }).icon);
    expect(new Set(marks).size).toBe(3);
  });
});

describe("MCP server marks", () => {
  const icon = (name: string, overrides: Record<string, string> = {}) => toolPresentation(name, null, "", overrides).icon;

  it("recognizes a well-known server by name, whatever its spelling", () => {
    expect(icon("mcp__github__create_issue")).toBe("simple-icons:github");
    expect(icon("mcp__google-calendar__list_events")).toBe(icon("mcp__GoogleCalendar__list_events"));
    expect(icon("mcp__deepwiki__ask_question")).toBe("logos:devin");
    expect(icon("mcp__grove__grove_list_workspaces")).toBe("grove:grove");
  });

  it("finds the service inside a server name the user chose", () => {
    // A plugin prefixes its server, and a user suffixes a database with the
    // instance it points at; both still name a service the catalog knows.
    expect(icon("mcp__plugin_playwright_playwright__browser_click")).toBe("logos:playwright");
    expect(icon("mcp__postgresql_mifflin__execute_sql")).toBe("logos:postgresql");
  });

  it("falls back to the tool's leading word for a gateway fronting many services", () => {
    expect(icon("mcp__assistant__Gmail-search")).toBe("logos:google-gmail");
    expect(icon("mcp__assistant__Calendar-CreateEvent")).toBe("fluent-color:calendar-24");
    expect(icon("mcp__assistant__archive_thread")).toBe(UNKNOWN_SERVER_ICON);
  });

  it("lets a known server keep its own mark over a tool name that names another", () => {
    expect(icon("mcp__github__slack_notify")).toBe("simple-icons:github");
  });

  it("lets the host's own mapping override the catalog", () => {
    expect(icon("mcp__github__create_issue", { github: "logos:github-icon" })).toBe("logos:github-icon");
  });
});

