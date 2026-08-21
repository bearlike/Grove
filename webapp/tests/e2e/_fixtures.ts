import type {
  AgentSummaryView,
  BranchInfo,
  DashboardSnapshotView,
  PhaseView,
  ProvisionProgressView,
  SessionControlsView,
  TodoListView,
  WorkspacePeekView,
  SessionSummaryView,
  UsageActivityView,
  UsageBreakdownView,
  UsageFindingsView,
  UsageQuotasView,
  UsageSessionPageView,
  UsageSummaryView,
  WorkspaceStateView,
} from "@/lib/grove/api";

/**
 * Wire payloads captured from a live daemon on 2026-08-10, then trimmed and
 * scrubbed of host detail (paths, repo and profile names are fictional).
 *
 * Typed as the generated wire views so `codegen:check`'s contract reaches the
 * e2e harness too: a daemon-side field change fails `typecheck` here instead of
 * quietly leaving the fake daemon serving a shape the real one stopped sending.
 */

export const FIXTURE_ACTIVITY: DashboardSnapshotView = {
    "projects": [
      {
        "repo_root": "/home/demo/acme/widget",
        "repo_name": "widget",
        "cwd": "/home/demo/acme/widget",
        "workspaces": [
          {
            "state": {
              "id": "59d472a0b0ef47339ad61d1cf190edb9",
              "title": "Frontend UI migration",
              "repo_root": "/home/demo/acme/widget",
              "branch": "main",
              "base_branch": "HEAD",
              "worktree_path": "/home/demo/acme/widget",
              "tmux_session": "grove-frontend-ui-migration-20260810-063036",
              "agent_name": "Claude Code (via KK Gateway)",
              "status": "active",
              "created_at": "2026-08-10T06:30:36.499611Z",
              "updated_at": "2026-08-10T06:30:36.802387Z",
              "paused_at": null,
              "error_detail": null,
              "description": null,
              "init_status": "skipped",
              "init_duration_ms": null,
              "branch_provenance": "attached",
              "placement": "root",
              "ticket_refs": [],
              "runtime": "host",
              "runtime_fallback_reason": null,
              "provision_status": "skipped",
              "provision_duration_ms": null,
              "provision_started_at": null,
              "container": null,
              "runtime_default_config": false,
              "runtime_no_tmux": false
            },
            "sessions": [
              {
                "session": {
                  "session_id": "b3b5108d-1cbf-4ccf-91f9-e1e7d515542c",
                  "adapter_kind": "claude_code",
                  "provenance": "grove_launched",
                  "tmux_window": "agent",
                  "parent_session_id": null
                },
                "activity": {
                  "state": "working",
                  "title": "Plan Assistant UI frontend migration",
                  "current_task": "I do not have a rough wireframe of a target layout. I was pointing to our existing front end to guide us in canonically using Assistant UI components and deciding and organizing the layout. That was…",
                  "human_turns": 2,
                  "assistant_replies": 122,
                  "replies_per_turn": [
                    10,
                    112
                  ],
                  "tool_calls": 157,
                  "active_subagents": 9,
                  "model": "claude-opus-5",
                  "tokens_in": 19892333,
                  "tokens_out": 91641,
                  "last_event_at": "2026-08-10T07:21:22.689000Z",
                  "needs_attention": false,
                  "error_detail": null,
                  "interpreted_status": null,
                  "questions": [],
                  "live": null
                }
              },
              {
                "session": {
                  "session_id": "awebapp-inventory-c1d189365078b3be",
                  "adapter_kind": "claude_code",
                  "provenance": "fs_discovered",
                  "tmux_window": null,
                  "parent_session_id": "b3b5108d-1cbf-4ccf-91f9-e1e7d515542c"
                },
                "activity": {
                  "state": "waiting",
                  "title": "webapp-inventory",
                  "current_task": "Inventory current webapp surface",
                  "human_turns": 3,
                  "assistant_replies": 16,
                  "replies_per_turn": [],
                  "tool_calls": 57,
                  "active_subagents": 0,
                  "model": "gpt-5.6-terra",
                  "tokens_in": 765675,
                  "tokens_out": 7953,
                  "last_event_at": "2026-08-10T06:48:20.605000Z",
                  "needs_attention": true,
                  "error_detail": null,
                  "interpreted_status": null,
                  "questions": [],
                  "live": null
                }
              }
            ],
            "base_ahead": 0,
            "base_behind": 0,
            "diff_added": 0,
            "diff_removed": 0,
            "dirty_files": 6,
            "pane_target": "grove-frontend-ui-migration-20260810-063036:agent",
            "needs_attention": false,
            "recent_commits": [
              {
                "sha": "35e315a",
                "subject": "🔧 chore(webapp): accept Next's tsconfig reconciliation",
                "committed_at": "2026-08-10T00:21:03-07:00"
              },
              {
                "sha": "4b16c5b",
                "subject": "✨ feat(webapp): render read-only session transcripts",
                "committed_at": "2026-08-10T00:19:53-07:00"
              },
              {
                "sha": "75047c4",
                "subject": "🔧 chore(webapp): restore elements/range.ts to upstream verbatim",
                "committed_at": "2026-08-10T00:19:44-07:00"
              }
            ],
            "observed_at": "2026-08-10T07:21:26.273444Z",
            "phase": {
              "phase": "delivering",
              "note": "committing pairing, account menu, and session catalog",
              "updated_at": "2026-08-10T07:03:48.328778Z",
              "index": 4,
              "total": 6,
              "blocked": false,
              "tickets": []
            },
            "todo": {
              "total": 11,
              "completed": 5,
              "in_progress": 5,
              "pending": 1
            }
          }
        ],
        "error": null
      },
      {
        "repo_root": "/home/demo/scratch",
        "repo_name": "Agents",
        "cwd": "/home/demo/scratch",
        "workspaces": [],
        "error": null
      }
    ],
    "generated_at": "2026-08-10T07:21:26.273940Z",
    "total_workspaces": 1,
    "needs_attention": 0
  };

export const FIXTURE_WORKSPACES: WorkspaceStateView[] = [
    {
      "id": "59d472a0b0ef47339ad61d1cf190edb9",
      "title": "Frontend UI migration",
      "repo_root": "/home/demo/acme/widget",
      "branch": "main",
      "base_branch": "HEAD",
      "worktree_path": "/home/demo/acme/widget",
      "tmux_session": "grove-frontend-ui-migration-20260810-063036",
      "agent_name": "Claude Code (via KK Gateway)",
      "status": "active",
      "created_at": "2026-08-10T06:30:36.499611Z",
      "updated_at": "2026-08-10T06:30:36.802387Z",
      "paused_at": null,
      "error_detail": null,
      "description": null,
      "init_status": "skipped",
      "init_duration_ms": null,
      "branch_provenance": "attached",
      "placement": "root",
      "ticket_refs": [],
      "runtime": "host",
      "runtime_fallback_reason": null,
      "provision_status": "skipped",
      "provision_duration_ms": null,
      "provision_started_at": null,
      "container": null,
      "runtime_default_config": false,
      "runtime_no_tmux": false
    }
  ];

export const FIXTURE_SESSIONS: SessionSummaryView[] = [
    {
      "session_id": "b3b5108d-1cbf-4ccf-91f9-e1e7d515542c",
      "adapter_kind": "claude_code",
      "provenance": "grove_launched",
      "primary": true,
      "workspace_id": "59d472a0b0ef47339ad61d1cf190edb9",
      "workspace_title": "Frontend UI migration",
      "workspace_branch": null,
      "git_branch": "main",
      "created_at": "2026-08-10T06:30:39.680000Z",
      "modified_at": "2026-08-10T07:21:19.947820Z",
      "size_bytes": null,
      "title": null,
      "first_prompt": null,
      "last_prompt": null,
      "activity": null,
      "cwd": "/home/demo/acme/widget",
      "project": {
        "repo_root": "/home/demo/acme/widget",
        "repo_name": "widget",
        "is_worktree": false,
        "is_grove_managed": true
      },
      "live": true
    },
    {
      "session_id": "c2fd3779-317d-496f-a1c8-f61797fe9825",
      "adapter_kind": "claude_code",
      "provenance": "fs_discovered",
      "primary": false,
      "workspace_id": "59d472a0b0ef47339ad61d1cf190edb9",
      "workspace_title": "Frontend UI migration",
      "workspace_branch": null,
      "git_branch": "main",
      "created_at": "2026-08-10T06:28:40.429000Z",
      "modified_at": "2026-08-10T06:29:27.023860Z",
      "size_bytes": null,
      "title": null,
      "first_prompt": null,
      "last_prompt": null,
      "activity": null,
      "cwd": "/home/demo/acme/widget",
      "project": {
        "repo_root": "/home/demo/acme/widget",
        "repo_name": "widget",
        "is_worktree": false,
        "is_grove_managed": true
      },
      "live": false
    },
    {
      "session_id": "f07191e7-593a-4b76-a181-5df1a009b124",
      "adapter_kind": "claude_code",
      "provenance": "fs_discovered",
      "primary": false,
      "workspace_id": "59d472a0b0ef47339ad61d1cf190edb9",
      "workspace_title": "Frontend UI migration",
      "workspace_branch": null,
      "git_branch": "main",
      "created_at": "2026-08-10T00:23:38.678000Z",
      "modified_at": "2026-08-10T00:26:28.161328Z",
      "size_bytes": null,
      "title": null,
      "first_prompt": null,
      "last_prompt": null,
      "activity": null,
      "cwd": "/home/demo/acme/widget",
      "project": {
        "repo_root": "/home/demo/acme/widget",
        "repo_name": "widget",
        "is_worktree": false,
        "is_grove_managed": true
      },
      "live": false
    }
  ];

/** The six usage routes, each keyed by the client method that calls it. */
export const FIXTURE_USAGE: {
  summary: UsageSummaryView;
  activity: UsageActivityView;
  quotas: UsageQuotasView;
  sessions: UsageSessionPageView;
  breakdowns: UsageBreakdownView;
  findings: UsageFindingsView;
} = {
    "summary": {
      "since": null,
      "until": null,
      "tz": "UTC",
      "sessions": 513,
      "turns": 12591,
      "tokens": {
        "fresh_input": null,
        "cache_read": null,
        "cache_creation": null,
        "reasoning": null,
        "output": null,
        "provider_total": null
      },
      "duration": {
        "active_ms": null,
        "elapsed_span_ms": 9829595686,
        "confidence": "derived"
      },
      "tools": {
        "calls": 204643,
        "failures": 3641,
        "distinct_tools": 349
      },
      "files_changed": 8635,
      "cost": null,
      "accounts": 2,
      "projects": 13,
      "coverage": {
        "sources": [
          {
            "source_id": "claude_code-e4c72aa405bb",
            "provider": "claude_code",
            "label": ".claude",
            "health": "degraded",
            "detail": "transcript cwd was not measured",
            "session_count": 320,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          },
          {
            "source_id": "codex-7d8bc84c9eaa",
            "provider": "codex",
            "label": ".codex",
            "health": "ok",
            "detail": null,
            "session_count": 193,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          }
        ],
        "degraded_source_count": 1,
        "earliest_event_at": "2026-02-02T15:38:02Z",
        "latest_event_at": "2026-08-10T06:13:21Z",
        "last_refresh_at": "2026-08-10T06:15:44Z",
        "cost_available": false,
        "quota_available": true
      }
    },
    "activity": {
      "metric": "tokens",
      "tz": "UTC",
      "buckets": [
        {
          "day": "2026-07-28",
          "value": 719160063,
          "sessions": 7
        },
        {
          "day": "2026-07-29",
          "value": 704888786,
          "sessions": 11
        },
        {
          "day": "2026-07-30",
          "value": 1560726527,
          "sessions": 12
        },
        {
          "day": "2026-07-31",
          "value": 2490753050,
          "sessions": 13
        },
        {
          "day": "2026-08-01",
          "value": 1072045373,
          "sessions": 9
        },
        {
          "day": "2026-08-02",
          "value": 2947008366,
          "sessions": 16
        },
        {
          "day": "2026-08-03",
          "value": 496347868,
          "sessions": 9
        },
        {
          "day": "2026-08-04",
          "value": 2415039551,
          "sessions": 13
        },
        {
          "day": "2026-08-05",
          "value": 472017330,
          "sessions": 6
        },
        {
          "day": "2026-08-06",
          "value": 166749541,
          "sessions": 5
        },
        {
          "day": "2026-08-07",
          "value": 1369015031,
          "sessions": 14
        },
        {
          "day": "2026-08-08",
          "value": 2854612871,
          "sessions": 8
        },
        {
          "day": "2026-08-09",
          "value": 1082153194,
          "sessions": 28
        },
        {
          "day": "2026-08-10",
          "value": 516826976,
          "sessions": 16
        }
      ],
      "total": 35915836276,
      "max_value": 2947008366,
      "coverage": {
        "sources": [
          {
            "source_id": "claude_code-e4c72aa405bb",
            "provider": "claude_code",
            "label": ".claude",
            "health": "degraded",
            "detail": "transcript cwd was not measured",
            "session_count": 320,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          },
          {
            "source_id": "codex-7d8bc84c9eaa",
            "provider": "codex",
            "label": ".codex",
            "health": "ok",
            "detail": null,
            "session_count": 193,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          }
        ],
        "degraded_source_count": 1,
        "earliest_event_at": "2026-02-02T15:38:02Z",
        "latest_event_at": "2026-08-10T06:13:21Z",
        "last_refresh_at": "2026-08-10T06:15:44Z",
        "cost_available": false,
        "quota_available": true
      }
    },
    "quotas": {
      "accounts": [
        {
          "account_id": "claude_code-e4c72aa405bb",
          "provider": "claude_code",
          "label": ".claude",
          "billing_mode": "subscription",
          // Read from the credential file the provider opens anyway, so it
          // survives a refusal of the metered endpoint.
          "subscription": { "plan": "max", "label": "max", "detail": "20x" },
          "status": "ok",
          "detail": null,
          // Every Claude window projects `unknown`, and that is the ORDINARY
          // case rather than a degraded one: the endpoint reports no duration,
          // so a window has no computable start to extrapolate from.
          "windows": [
            {
              "scope": "session",
              "label": "session",
              "window_seconds": null,
              "used_percent": 9,
              "remaining_percent": 91,
              "resets_at": "2026-08-10T09:10:00.128332Z",
              "limit": null,
              "used": null,
              "unit": null,
              "observed_at": "2026-08-10T07:17:28.186677Z",
              "evidence": "provider_endpoint",
              "projection": {
                "elapsed_percent": null,
                "burn_rate": null,
                "projected_percent": null,
                "verdict": "unknown",
                "exhausts_at": null,
                "tokens_used": null,
                "tokens_available_estimate": null
              }
            },
            {
              "scope": "weekly",
              "label": "weekly_all",
              "window_seconds": null,
              "used_percent": 33,
              "remaining_percent": 67,
              "resets_at": "2026-08-15T11:00:00.128356Z",
              "limit": null,
              "used": null,
              "unit": null,
              "observed_at": "2026-08-10T07:17:28.186677Z",
              "evidence": "provider_endpoint",
              "projection": {
                "elapsed_percent": null,
                "burn_rate": null,
                "projected_percent": null,
                "verdict": "unknown",
                "exhausts_at": null,
                "tokens_used": null,
                "tokens_available_estimate": null
              }
            },
            {
              "scope": "model",
              "label": "weekly_scoped",
              "window_seconds": null,
              "used_percent": 0,
              "remaining_percent": 100,
              "resets_at": "2026-08-15T11:00:00.128564Z",
              "limit": null,
              "used": null,
              "unit": null,
              "observed_at": "2026-08-10T07:17:28.186677Z",
              "evidence": "provider_endpoint",
              "projection": {
                "elapsed_percent": null,
                "burn_rate": null,
                "projected_percent": null,
                "verdict": "unknown",
                "exhausts_at": null,
                "tokens_used": null,
                "tokens_available_estimate": null
              }
            }
          ],
          "spend": null,
          "observed_at": "2026-08-10T07:17:28.186677Z",
          "stale_seconds": null
        },
        {
          "account_id": "codex-7d8bc84c9eaa",
          "provider": "codex",
          "label": ".codex",
          "billing_mode": "subscription",
          "subscription": { "plan": "plus", "label": "plus", "detail": null },
          "status": "ok",
          "detail": null,
          // Codex sends `window_seconds`, so this one CAN be judged — the exact
          // live reading measured on the reference host on 2026-08-11.
          "windows": [
            {
              "scope": "weekly",
              "label": "primary",
              "window_seconds": 604800,
              "used_percent": 25,
              "remaining_percent": 75,
              "resets_at": "2026-08-16T07:17:21Z",
              "limit": null,
              "used": null,
              "unit": null,
              "observed_at": "2026-08-10T06:13:21.998000Z",
              "evidence": "rollout",
              "projection": {
                "elapsed_percent": 28.5,
                "burn_rate": 0.877,
                "projected_percent": 87.6,
                "verdict": "tight",
                "exhausts_at": null,
                "tokens_used": 12648213,
                "tokens_available_estimate": 50592852
              }
            }
          ],
          "spend": null,
          "observed_at": "2026-08-10T06:13:21.998000Z",
          "stale_seconds": null
        }
      ],
      "coverage": {
        "sources": [
          {
            "source_id": "claude_code-e4c72aa405bb",
            "provider": "claude_code",
            "label": ".claude",
            "health": "degraded",
            "detail": "transcript cwd was not measured",
            "session_count": 320,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          },
          {
            "source_id": "codex-7d8bc84c9eaa",
            "provider": "codex",
            "label": ".codex",
            "health": "ok",
            "detail": null,
            "session_count": 193,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          }
        ],
        "degraded_source_count": 1,
        "earliest_event_at": "2026-02-02T15:38:02Z",
        "latest_event_at": "2026-08-10T06:13:21Z",
        "last_refresh_at": "2026-08-10T06:15:44Z",
        "cost_available": false,
        "quota_available": true
      }
    },
    "sessions": {
      "rows": [
        {
          "session_id": "019fe7d6-1d1c-76a3-825a-f31cdb338cf3",
          "provider": "codex",
          "cwd": "/home/demo/acme/widget",
          "project": "/home/demo/acme/widget",
          "account_id": "codex-7d8bc84c9eaa",
          "account_label": ".codex",
          "source_id": "codex-7d8bc84c9eaa",
          "models": [
            "gpt-5.6-luna",
            "gpt-5.6-sol"
          ],
          "started_at": "2026-08-09T18:46:40Z",
          "last_event_at": "2026-08-10T06:13:21Z",
          "duration": {
            "active_ms": 16922063,
            "elapsed_span_ms": 41201263,
            "confidence": "derived"
          },
          "turns": 40,
          "tool_calls": 1709,
          "tool_failures": 0,
          "files_changed": 0,
          "tokens": {
            "fresh_input": null,
            "cache_read": 233484160,
            "cache_creation": 0,
            "reasoning": 152981,
            "output": 193830,
            "provider_total": 97834653
          },
          "cost": null,
          "parser_health": "ok",
          "parser_detail": null
        },
        {
          "session_id": "019fea43-c119-75a3-858f-feba49b4ea90",
          "provider": "codex",
          "cwd": "/home/demo/acme/widget",
          "project": "/home/demo/acme/widget",
          "account_id": "codex-7d8bc84c9eaa",
          "account_label": ".codex",
          "source_id": "codex-7d8bc84c9eaa",
          "models": [
            "gpt-5.6-terra",
            "gpt-5.6-sol"
          ],
          "started_at": "2026-08-10T06:02:16Z",
          "last_event_at": "2026-08-10T06:05:13Z",
          "duration": {
            "active_ms": 59388,
            "elapsed_span_ms": 176726,
            "confidence": "derived"
          },
          "turns": 6,
          "tool_calls": 8,
          "tool_failures": 0,
          "files_changed": 0,
          "tokens": {
            "fresh_input": null,
            "cache_read": 478976,
            "cache_creation": 0,
            "reasoning": 4255,
            "output": 7842,
            "provider_total": 570643
          },
          "cost": null,
          "parser_health": "ok",
          "parser_detail": null
        },
        {
          "session_id": "019fea30-5ed6-7c00-ba0a-d4ca7f41a4cf",
          "provider": "codex",
          "cwd": "/home/demo/acme/widget",
          "project": "/home/demo/acme/widget",
          "account_id": "codex-7d8bc84c9eaa",
          "account_label": ".codex",
          "source_id": "codex-7d8bc84c9eaa",
          "models": [
            "gpt-5.6-terra"
          ],
          "started_at": "2026-08-10T05:41:09Z",
          "last_event_at": "2026-08-10T05:42:48Z",
          "duration": {
            "active_ms": 48189,
            "elapsed_span_ms": 98975,
            "confidence": "derived"
          },
          "turns": 1,
          "tool_calls": 12,
          "tool_failures": 0,
          "files_changed": 0,
          "tokens": {
            "fresh_input": null,
            "cache_read": 712448,
            "cache_creation": 0,
            "reasoning": 1167,
            "output": 4306,
            "provider_total": 815518
          },
          "cost": null,
          "parser_health": "ok",
          "parser_detail": null
        },
        {
          "session_id": "616566d6-f477-42d0-a821-fe31b5ef99d4",
          "provider": "claude_code",
          "cwd": "/home/demo/scratch/Assistant",
          "project": "/home/demo/scratch/Assistant",
          "account_id": "claude_code-e4c72aa405bb",
          "account_label": ".claude",
          "source_id": "claude_code-e4c72aa405bb",
          "models": [
            "claude-opus-5",
            "claude-sonnet-5"
          ],
          "started_at": "2026-08-09T22:53:15Z",
          "last_event_at": "2026-08-10T03:10:21Z",
          "duration": {
            "active_ms": 29505509,
            "elapsed_span_ms": 15426208,
            "confidence": "derived"
          },
          "turns": 44,
          "tool_calls": 1094,
          "tool_failures": 24,
          "files_changed": 63,
          "tokens": {
            "fresh_input": 28784,
            "cache_read": 245423077,
            "cache_creation": 7203020,
            "reasoning": null,
            "output": 456572,
            "provider_total": null
          },
          "cost": null,
          "parser_health": "ok",
          "parser_detail": null
        },
        {
          "session_id": "cff62dc7-401f-444a-bcd8-bf3b9ebbcc47",
          "provider": "claude_code",
          "cwd": "/home/demo/scratch/Assistant/.worktrees/langfuse-traces-bug-20260810-005704",
          "project": null,
          "account_id": "claude_code-e4c72aa405bb",
          "account_label": ".claude",
          "source_id": "claude_code-e4c72aa405bb",
          "models": [
            "claude-opus-5",
            "claude-sonnet-5"
          ],
          "started_at": "2026-08-10T01:05:08Z",
          "last_event_at": "2026-08-10T03:05:38Z",
          "duration": {
            "active_ms": 6763433,
            "elapsed_span_ms": 7229203,
            "confidence": "derived"
          },
          "turns": 30,
          "tool_calls": 538,
          "tool_failures": 21,
          "files_changed": 11,
          "tokens": {
            "fresh_input": 4680,
            "cache_read": 92887783,
            "cache_creation": 3757172,
            "reasoning": null,
            "output": 171319,
            "provider_total": null
          },
          "cost": null,
          "parser_health": "ok",
          "parser_detail": null
        }
      ],
      "next_cursor": null,
      "sort": "recent",
      "coverage": {
        "sources": [
          {
            "source_id": "claude_code-e4c72aa405bb",
            "provider": "claude_code",
            "label": ".claude",
            "health": "degraded",
            "detail": "transcript cwd was not measured",
            "session_count": 320,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          },
          {
            "source_id": "codex-7d8bc84c9eaa",
            "provider": "codex",
            "label": ".codex",
            "health": "ok",
            "detail": null,
            "session_count": 193,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          }
        ],
        "degraded_source_count": 1,
        "earliest_event_at": "2026-02-02T15:38:02Z",
        "latest_event_at": "2026-08-10T06:13:21Z",
        "last_refresh_at": "2026-08-10T06:15:44Z",
        "cost_available": false,
        "quota_available": true
      }
    },
    "breakdowns": {
      "dimension": "model",
      "rows": [
        {
          "key": "gpt-5.3-codex",
          "label": "gpt-5.3-codex",
          "sessions": 72,
          "tokens": {
            "fresh_input": null,
            "cache_read": null,
            "cache_creation": null,
            "reasoning": null,
            "output": null,
            "provider_total": null
          },
          "active_ms": null,
          "tool_calls": 4629,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "claude-opus-5",
          "label": "claude-opus-5",
          "sessions": 60,
          "tokens": {
            "fresh_input": 38049,
            "cache_read": 778431249,
            "cache_creation": 25838827,
            "reasoning": null,
            "output": 3000107,
            "provider_total": null
          },
          "active_ms": 148753428,
          "tool_calls": 5265,
          "tool_failures": 164,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-5.2-codex",
          "label": "gpt-5.2-codex",
          "sessions": 31,
          "tokens": {
            "fresh_input": null,
            "cache_read": null,
            "cache_creation": null,
            "reasoning": null,
            "output": null,
            "provider_total": null
          },
          "active_ms": null,
          "tool_calls": 14623,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-5.4",
          "label": "gpt-5.4",
          "sessions": 27,
          "tokens": {
            "fresh_input": null,
            "cache_read": null,
            "cache_creation": null,
            "reasoning": null,
            "output": null,
            "provider_total": null
          },
          "active_ms": null,
          "tool_calls": 4186,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "claude-opus-4-8",
          "label": "claude-opus-4-8",
          "sessions": 26,
          "tokens": {
            "fresh_input": 112811,
            "cache_read": 255757897,
            "cache_creation": 15728730,
            "reasoning": null,
            "output": 1280707,
            "provider_total": null
          },
          "active_ms": 59486126,
          "tool_calls": 2632,
          "tool_failures": 81,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "claude-sonnet-5",
          "label": "claude-sonnet-5",
          "sessions": 21,
          "tokens": {
            "fresh_input": 217,
            "cache_read": 8964429,
            "cache_creation": 2961458,
            "reasoning": null,
            "output": 67064,
            "provider_total": null
          },
          "active_ms": null,
          "tool_calls": 97,
          "tool_failures": 9,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-5.6-sol",
          "label": "gpt-5.6-sol",
          "sessions": 8,
          "tokens": {
            "fresh_input": null,
            "cache_read": 16452352,
            "cache_creation": 0,
            "reasoning": 61203,
            "output": 118037,
            "provider_total": 17509786
          },
          "active_ms": 1446141,
          "tool_calls": 163,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-5.1-codex-mini",
          "label": "gpt-5.1-codex-mini",
          "sessions": 6,
          "tokens": {
            "fresh_input": null,
            "cache_read": 6270720,
            "cache_creation": null,
            "reasoning": 33088,
            "output": 50388,
            "provider_total": 7097269
          },
          "active_ms": 800677,
          "tool_calls": 199,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gemini-3.1-flash-lite-preview",
          "label": "gemini-3.1-flash-lite-preview",
          "sessions": 6,
          "tokens": {
            "fresh_input": null,
            "cache_read": null,
            "cache_creation": null,
            "reasoning": null,
            "output": null,
            "provider_total": null
          },
          "active_ms": null,
          "tool_calls": 0,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-5.6-luna",
          "label": "gpt-5.6-luna",
          "sessions": 5,
          "tokens": {
            "fresh_input": null,
            "cache_read": 12781824,
            "cache_creation": 0,
            "reasoning": null,
            "output": 75186,
            "provider_total": null
          },
          "active_ms": 1710587,
          "tool_calls": 157,
          "tool_failures": 5,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "claude-fable-5",
          "label": "claude-fable-5",
          "sessions": 5,
          "tokens": {
            "fresh_input": 115,
            "cache_read": 5854953,
            "cache_creation": 998932,
            "reasoning": null,
            "output": 60998,
            "provider_total": null
          },
          "active_ms": 1278489,
          "tool_calls": 75,
          "tool_failures": 5,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "minimax/minimax-m2.5",
          "label": "minimax/minimax-m2.5",
          "sessions": 3,
          "tokens": {
            "fresh_input": null,
            "cache_read": 0,
            "cache_creation": null,
            "reasoning": 232,
            "output": 308,
            "provider_total": 308
          },
          "active_ms": 25204,
          "tool_calls": 0,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-5.6-terra",
          "label": "gpt-5.6-terra",
          "sessions": 3,
          "tokens": {
            "fresh_input": null,
            "cache_read": 3425024,
            "cache_creation": 0,
            "reasoning": 13598,
            "output": 25308,
            "provider_total": 3817785
          },
          "active_ms": 237860,
          "tool_calls": 46,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-5.5",
          "label": "gpt-5.5",
          "sessions": 3,
          "tokens": {
            "fresh_input": null,
            "cache_read": 24498688,
            "cache_creation": null,
            "reasoning": 38866,
            "output": 88800,
            "provider_total": 26949925
          },
          "active_ms": 1818962,
          "tool_calls": 339,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "claude-haiku-4-5-20251001",
          "label": "claude-haiku-4-5-20251001",
          "sessions": 3,
          "tokens": {
            "fresh_input": 171,
            "cache_read": 1319966,
            "cache_creation": 192230,
            "reasoning": null,
            "output": 8961,
            "provider_total": null
          },
          "active_ms": 173494,
          "tool_calls": 19,
          "tool_failures": 3,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "z-ai/glm-5",
          "label": "z-ai/glm-5",
          "sessions": 1,
          "tokens": {
            "fresh_input": null,
            "cache_read": 0,
            "cache_creation": null,
            "reasoning": 113,
            "output": 116,
            "provider_total": 116
          },
          "active_ms": 19143,
          "tool_calls": 0,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "qwen3.8-max-preview",
          "label": "qwen3.8-max-preview",
          "sessions": 1,
          "tokens": {
            "fresh_input": 566435,
            "cache_read": 2930681,
            "cache_creation": 253500,
            "reasoning": null,
            "output": 16526,
            "provider_total": null
          },
          "active_ms": 675120,
          "tool_calls": 31,
          "tool_failures": 1,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "gpt-oss-120b",
          "label": "gpt-oss-120b",
          "sessions": 1,
          "tokens": {
            "fresh_input": null,
            "cache_read": null,
            "cache_creation": null,
            "reasoning": null,
            "output": null,
            "provider_total": null
          },
          "active_ms": null,
          "tool_calls": 0,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "glm-5.2",
          "label": "glm-5.2",
          "sessions": 1,
          "tokens": {
            "fresh_input": 33300,
            "cache_read": 33152,
            "cache_creation": 0,
            "reasoning": null,
            "output": 132,
            "provider_total": null
          },
          "active_ms": 18749,
          "tool_calls": 0,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        },
        {
          "key": "deepseek-v4-pro",
          "label": "deepseek-v4-pro",
          "sessions": 1,
          "tokens": {
            "fresh_input": 37552,
            "cache_read": 101376,
            "cache_creation": 0,
            "reasoning": null,
            "output": 551,
            "provider_total": null
          },
          "active_ms": 16456,
          "tool_calls": 2,
          "tool_failures": 0,
          "latency": { "avg_ms": null, "calls": 0 },
          "cost": null
        }
      ],
      "truncated": false,
      "coverage": {
        "sources": [
          {
            "source_id": "claude_code-e4c72aa405bb",
            "provider": "claude_code",
            "label": ".claude",
            "health": "degraded",
            "detail": "transcript cwd was not measured",
            "session_count": 320,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          },
          {
            "source_id": "codex-7d8bc84c9eaa",
            "provider": "codex",
            "label": ".codex",
            "health": "ok",
            "detail": null,
            "session_count": 193,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          }
        ],
        "degraded_source_count": 1,
        "earliest_event_at": "2026-02-02T15:38:02Z",
        "latest_event_at": "2026-08-10T06:13:21Z",
        "last_refresh_at": "2026-08-10T06:15:44Z",
        "cost_available": false,
        "quota_available": true
      }
    },
    "findings": {
      "total": 4,
      "findings": [
        {
          "kind": "slow_operation",
          "title": "Slow operation in Agent",
          "detail": "Measured p95 was 367169 ms across 1724 operations.",
          "count": 424,
          "impact": 1,
          "confidence": 1,
          "first_seen_at": "2026-07-02T15:53:56Z",
          "last_seen_at": "2026-08-10T01:54:39Z",
          "evidence_filters": {
            "since": null,
            "until": null,
            "tz": "UTC",
            "provider": "claude_code",
            "account": "claude_code-e4c72aa405bb",
            "project": "/home/demo/scratch/Assistant",
            "model": null,
            "day": null
          },
          "session_ids": [
            "f3b6ac1a-8d9c-4661-8857-cdad1b8269c4",
            "e8de5fa5-b3bf-424d-8607-299d079f64b3",
            "f8a94157-28cd-42e7-afb7-92af90fc0027",
            "f5195d93-9dea-44c0-975e-dcba4fa604d1",
            "da6a22b8-e33b-4e76-8cda-2168423132f8",
            "e34b2bd2-dc97-4b9d-a93c-4595b8f925d6",
            "6b6674cf-4073-4106-8001-f54b6dd80071",
            "45353947-da05-4e85-9c4e-6742a263fdd6",
            "90267ceb-3d8a-4767-ba1b-de9cc395d303",
            "cb223709-c981-425b-b486-ca7fb0e6505c"
          ],
          "subject": "Agent"
        },
        {
          "kind": "edit_churn",
          "title": "Repeated edits to /home/demo/acme/widget/docs/use-tui.md",
          "detail": null,
          "count": 362,
          "impact": 1,
          "confidence": 1,
          "first_seen_at": "2026-08-08T04:30:38Z",
          "last_seen_at": "2026-08-08T07:22:06Z",
          "evidence_filters": {
            "since": null,
            "until": null,
            "tz": "UTC",
            "provider": "claude_code",
            "account": "claude_code-e4c72aa405bb",
            "project": "/home/demo/acme/widget",
            "model": null,
            "day": null
          },
          "session_ids": [
            "3156fdc6-73fc-4a0d-ad02-fe413795f07e"
          ],
          "subject": "/home/demo/acme/widget/docs/use-tui.md"
        },
        {
          "kind": "edit_churn",
          "title": "Repeated edits to /home/demo/scratch/acme/Assistant-Presentation/deck/slides/01-main.md",
          "detail": null,
          "count": 209,
          "impact": 1,
          "confidence": 1,
          "first_seen_at": "2026-07-27T23:32:26Z",
          "last_seen_at": "2026-07-28T02:26:39Z",
          "evidence_filters": {
            "since": null,
            "until": null,
            "tz": "UTC",
            "provider": "claude_code",
            "account": "claude_code-e4c72aa405bb",
            "project": "/home/demo/scratch/Assistant",
            "model": null,
            "day": null
          },
          "session_ids": [
            "817339b7-4a1f-45ea-99e4-4cea23c4e22b"
          ],
          "subject": "/home/demo/scratch/acme/Assistant-Presentation/deck/slides/01-main.md"
        },
        {
          "kind": "slow_operation",
          "title": "Slow operation in Write",
          "detail": "Measured p95 was 79242 ms across 2175 operations.",
          "count": 201,
          "impact": 1,
          "confidence": 1,
          "first_seen_at": "2026-07-02T18:48:24Z",
          "last_seen_at": "2026-08-10T02:01:56Z",
          "evidence_filters": {
            "since": null,
            "until": null,
            "tz": "UTC",
            "provider": "claude_code",
            "account": "claude_code-e4c72aa405bb",
            "project": "/home/demo/scratch/Assistant",
            "model": null,
            "day": null
          },
          "session_ids": [
            "f3b6ac1a-8d9c-4661-8857-cdad1b8269c4",
            "e8de5fa5-b3bf-424d-8607-299d079f64b3",
            "f8a94157-28cd-42e7-afb7-92af90fc0027",
            "f5195d93-9dea-44c0-975e-dcba4fa604d1",
            "65eab624-dbed-4099-a9ac-4b4d0e457e56",
            "6b6674cf-4073-4106-8001-f54b6dd80071",
            "45353947-da05-4e85-9c4e-6742a263fdd6",
            "90267ceb-3d8a-4767-ba1b-de9cc395d303",
            "3e2575d8-99c9-42db-bd93-5d4a742691fe",
            "66b20718-20ad-411e-afe4-236b5907fd28"
          ],
          "subject": "Write"
        }
      ],
      "coverage": {
        "sources": [
          {
            "source_id": "claude_code-e4c72aa405bb",
            "provider": "claude_code",
            "label": ".claude",
            "health": "degraded",
            "detail": "transcript cwd was not measured",
            "session_count": 320,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          },
          {
            "source_id": "codex-7d8bc84c9eaa",
            "provider": "codex",
            "label": ".codex",
            "health": "ok",
            "detail": null,
            "session_count": 193,
            "last_indexed_at": "2026-08-10T06:15:44Z"
          }
        ],
        "degraded_source_count": 1,
        "earliest_event_at": "2026-02-02T15:38:02Z",
        "latest_event_at": "2026-08-10T06:13:21Z",
        "last_refresh_at": "2026-08-10T06:15:44Z",
        "cost_available": false,
        "quota_available": true
      }
    }
  };


/** The per-workspace routes the workspace surface calls on mount. */
export const FIXTURE_PEEK: WorkspacePeekView = {
    "state": {
      "id": "59d472a0b0ef47339ad61d1cf190edb9",
      "title": "Frontend UI migration",
      "repo_root": "/home/demo/acme/widget",
      "branch": "main",
      "base_branch": "HEAD",
      "worktree_path": "/home/demo/acme/widget",
      "tmux_session": "grove-frontend-ui-migration-20260810-063036",
      "agent_name": "Claude Code (via KK Gateway)",
      "status": "active",
      "created_at": "2026-08-10T06:30:36.499611Z",
      "updated_at": "2026-08-10T06:30:36.802387Z",
      "paused_at": null,
      "error_detail": null,
      "description": null,
      "init_status": "skipped",
      "init_duration_ms": null,
      "branch_provenance": "attached",
      "placement": "root",
      "ticket_refs": [],
      "runtime": "host",
      "runtime_fallback_reason": null,
      "provision_status": "skipped",
      "provision_duration_ms": null,
      "provision_started_at": null,
      "container": null,
      "runtime_default_config": false,
      "runtime_no_tmux": false
    },
    "base_ahead": 0,
    "base_behind": 0,
    "diff_added": 0,
    "diff_removed": 0,
    "dirty_files": 8,
    "recent_commits": [
      {
        "sha": "e3f69ed",
        "subject": "🎨 style(webapp): let Avatar supply the sidebar brand shape",
        "committed_at": "2026-08-10T00:27:56-07:00"
      },
      {
        "sha": "a0cda22",
        "subject": "✨ feat(webapp): usage audit from registry elements",
        "committed_at": "2026-08-10T00:24:27-07:00"
      }
    ],
    "agent_snapshot": "demo@widget:~/acme/widget$ grove status\nworkspace active\n",
    "snapshot_taken_at": "2026-08-10T07:27:59.870422Z"
  };

export const FIXTURE_TODO: TodoListView = {
    "items": [
      {
        "content": "Recon: inventory current webapp feature surface",
        "status": "completed",
        "active_form": "Inventorying current webapp"
      },
      {
        "content": "Recon: pick the assistant-ui starter base",
        "status": "completed",
        "active_form": "Researching assistant-ui starters"
      },
      {
        "content": "Decide: greenfield app vs in-place migration",
        "status": "completed",
        "active_form": "Deciding migration strategy"
      }
    ]
  };

export const FIXTURE_PHASE: PhaseView = {
    "phase": "delivering",
    "note": "committing pairing, account menu, and session catalog",
    "updated_at": "2026-08-10T07:03:48.328778Z",
    "index": 4,
    "total": 6,
    "blocked": false,
    "tickets": []
  };

export const FIXTURE_CONTROLS: SessionControlsView = {
    "commands": [
      {
        "name": "quota",
        "scope": "user",
        "detail": "Anthropic subscription quota — 5h and weekly windows, per account"
      }
    ],
    "skills": [
      {
        "name": "playwright-cli",
        "scope": "user",
        "detail": "Automate browser interactions, test web pages and work with Playwright tests."
      }
    ],
    "mcp_servers": [
      {
        "name": "grove",
        "scope": "project",
        "detail": null
      },
      {
        "name": "grove-network",
        "scope": "project",
        "detail": null
      }
    ],
    "models": [
      "sonnet",
      "opus"
    ],
    "current_model": "claude-opus-5",
    "permission_mode": null
  };

export const FIXTURE_PROVISION: ProvisionProgressView = {
    "elapsed_ms": null,
    "headline": "",
    "lines": []
  };

/** `GET /agents` and `GET /branches` are ARRAYS, not envelopes. */
export const FIXTURE_AGENTS: AgentSummaryView[] = [
  { name: "shell", kind: "generic", description: "Plain shell — a workspace with no LLM", models: [] },
  {
    name: "Claude Code (default)",
    kind: "claude_code",
    description: "Claude Code, default config dir, skip-permissions",
    models: ["fable", "opus", "sonnet", "haiku"],
  },
];

export const FIXTURE_BRANCHES: BranchInfo[] = [
  { name: "main", kind: "local", is_current: true, upstream: "origin/main", checked_out_in: null },
  { name: "feat/health-endpoint", kind: "local", is_current: false, upstream: null, checked_out_in: null },
];
