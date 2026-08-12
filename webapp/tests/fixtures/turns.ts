import type { AgentQuestionView, SessionTurnView, TodoListView } from "@/lib/grove/api";

/**
 * Wire payloads captured from a live daemon (`GET /workspaces/{id}/sessions/
 * {sid}/turns`, 2026-08-10) and scrubbed of host detail.
 *
 * They are typed as the generated wire views on purpose: `codegen:check` keeps
 * `types.gen.ts` honest against the daemon's OpenAPI, so a daemon-side field
 * rename breaks this file at TYPECHECK time. Hand-written object literals with
 * an `as` cast would sail straight past that and the fixtures would rot.
 *
 * Only the VALUES are invented — the shapes are exactly what the daemon sent,
 * including the flat `role` + all-nullable-payload entry shape (every entry
 * carries `question`, `file_edit` and `todo` keys, null but present) which is
 * the detail a hand-written fixture reliably gets wrong.
 */

/** The five entry roles a real session produced, in the order they occurred. */
export const TRANSCRIPT_TURNS: SessionTurnView[] = [
  {
    user_text: "Add a health endpoint to the API.",
    started_at: "2026-08-10T06:12:03.481000Z",
    entries: [
      { role: "assistant", text: "I'll orient on the codebase first.", question: null, file_edit: null, todo: null },
      { role: "tool", text: "Bash ls -la", question: null, file_edit: null, todo: null },
      { role: "tool", text: "Read src/api/routes.py", question: null, file_edit: null, todo: null },
      {
        role: "todo",
        text: "0/2 done",
        question: null,
        file_edit: null,
        todo: {
          items: [
            { content: "Map the router module", status: "in_progress", active_form: "Mapping the router module" },
            { content: "Add the endpoint", status: "pending", active_form: "Adding the endpoint" },
          ],
        },
      },
      {
        role: "file_edit",
        text: "Write src/api/health.py",
        question: null,
        todo: null,
        file_edit: {
          path: "/workspaces/demo/src/api/health.py",
          display_path: "src/api/health.py",
          old_text: "",
          new_text: "def health() -> dict[str, str]:\n    return {'status': 'ok'}\n",
        },
      },
      { role: "assistant", text: "Endpoint added.", question: null, file_edit: null, todo: null },
    ],
  },
  {
    // A resumed session's head. The wire types `user_text` as a non-nullable
    // string, so "no fresh prompt" arrives as EMPTY, not null — which is what
    // `messagesFromTurns` reads to emit its `continuation` row.
    user_text: "",
    started_at: "2026-08-10T06:41:57.002000Z",
    entries: [
      {
        role: "todo",
        text: "2/2 done",
        question: null,
        file_edit: null,
        todo: {
          items: [
            { content: "Map the router module", status: "completed", active_form: "Mapping the router module" },
            { content: "Add the endpoint", status: "completed", active_form: "Adding the endpoint" },
          ],
        },
      },
      { role: "assistant", text: "", question: null, file_edit: null, todo: null },
      {
        role: "notification",
        text: "Subagent finished\nIt reviewed 4 files and found no issues.",
        question: null,
        file_edit: null,
        todo: null,
      },
    ],
  },
];

/** One `AskUserQuestion` batch: two questions sharing a `group_id`. */
export const QUESTION_BATCH: AgentQuestionView[] = [
  {
    id: "toolu_batch#0",
    group_id: "toolu_batch",
    kind: "single_select",
    prompt: "Which database should the endpoint report on?",
    header: "Database",
    source_tool: "AskUserQuestion",
    multiselect: false,
    answered: false,
    answer: null,
    options: [
      { label: "Primary only", description: "Report only the primary connection." },
      { label: "All replicas", description: "Fan out to every configured replica." },
    ],
  },
  {
    id: "toolu_batch#1",
    group_id: "toolu_batch",
    kind: "free_text",
    prompt: "Any path prefix to mount it under?",
    header: null,
    source_tool: "AskUserQuestion",
    multiselect: false,
    answered: false,
    answer: null,
    options: [],
  },
];

/** An `ExitPlanMode` confirm: a lone question, no options, the plan as prompt. */
export const PLAN_CONFIRM: AgentQuestionView[] = [
  {
    id: "toolu_plan#0",
    group_id: "toolu_plan",
    kind: "confirm",
    prompt: "1. Add health.py\n2. Register the route\n3. Add a test",
    header: null,
    source_tool: "ExitPlanMode",
    multiselect: false,
    answered: false,
    answer: null,
    options: [],
  },
];

/** Completed steps that are NOT a prefix — the case `activeIndex` must not lie about. */
export const NON_PREFIX_TODO: TodoListView = {
  items: [
    { content: "Map the router module", status: "pending", active_form: "Mapping the router module" },
    { content: "Add the endpoint", status: "completed", active_form: "Adding the endpoint" },
    { content: "Add a test", status: "in_progress", active_form: "Adding a test" },
  ],
};
