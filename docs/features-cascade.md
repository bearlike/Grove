# Configuration cascade

## Share defaults and keep your overrides

Grove provides mechanism and leaves policy to you. A team pins a shared standard in one committed file, and you still own the last word on your setup, like a team `.editorconfig`.

## Seven layers

| # | Layer | Path or source | Purpose |
|---|---|---|---|
| 1 | **Built-in defaults** | shipped with Grove | A workable baseline. |
| 2 | **User**            | `${user_config_dir}/grove/config.json` | Per-user defaults across every repo. |
| 3 | **Project**         | `<repo>/.grove/config.json` | Team baseline. Commit this. |
| 4 | **Project-local**   | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| 5 | **Declared env vars** | a fixed name per field, e.g. `GROVE_GOTIFY_API_URL` | Deployment values for the few fields that name one. |
| 6 | **Env vars**        | `GROVE_<SECTION>__<FIELD>=value` | Quick overrides for a single shell session. |
| 7 | **CLI flags**       | (where applicable) | One-shot overrides. |

- Each later layer overrides the earlier one, and an absent layer is skipped.
- Lists replace wholesale, except `agents`, which merges by `name`.
- Every surface resolves each repo's cascade independently, so an agent declared in `.grove/config.json` is honored the same way from the TUI, CLI, daemon and web dashboard.
- Pydantic validates the merged result with `extra="forbid"`, so a typo in any layer raises loudly.

## Workspace defaults put the user first

`defaults` is the one exception to project last. A workspace form is a private workbench, and the repository may suggest its usual setup but must not replace a preference you made for this machine.

- For `defaults` only, user sits above both project layers. Environment and CLI overrides stay above user.
- The inversion is field by field, so a project value still applies when your user layer leaves it unset.
- **Save as defaults** in the new workspace modal writes these answers. See [new-workspace defaults](configure-project.md#new-workspace-defaults).

## Lists merge by name, only for `agents`

Grove ships `claude`, `codex` and `shell`. A project layer that adds one entry gets all four.

```json
{ "agents": [{ "name": "aider", "command": "aider --model sonnet" }] }
```

- New names append, matching names merge field by field, so overriding only `claude`'s `command` keeps `kind: "claude_code"` for the [Activity Dashboard](features-activity.md).
- A mutually exclusive pair such as `init_script.inline` and `path` overrides as a group. The highest layer to set either wins both. See [init scripts](configure-init-scripts.md#inline-and-path-are-mutually-exclusive).

## Placeholders and environment variables

- `${repo}` and `${repo_name}` expand at consume time to the repo root and its basename, so one config serves every repo.
- `GROVE_<SECTION>__<FIELD>=value` overrides a single field for one invocation, double underscore per nesting level.
- `${VAR}` in any string value pulls from a variable you name. An unset variable is an error, since writing the reference was deliberate. `$${VAR}` is the escape.
- A few fields declare a fixed variable, such as `GROVE_GOTIFY_API_URL`, listed in the [configuration reference](configure-reference.md#environment-variable-overrides). An unset one simply does not override.
- A secret never goes through any of these. It stays on a `*_env` field that holds only the variable's name.

```bash
GROVE_TMUX__HISTORY_LIMIT=100000 grove        # one-shot, this invocation only
export GROVE_GITEA_BASE_URL=https://gitea.example.com    # tickets.gitea.base_url
```

Precedence for one field, lowest to highest: the config file's value, the field's declared variable, then `GROVE_<SECTION>__<FIELD>`.

## Worked example

The team's committed file, a teammate's gitignored theme, and another's personal agent compose with no coordination and no list wiped.

```json title=".grove/config.json"
{
  "worktree": { "root_template": "${repo}/.worktrees" },
  "init_script": {
    "enabled": true,
    "inline": "uv sync",
    "timeout_seconds": 180
  }
}
```

```json title=".grove/config.local.json"
{ "ui": { "theme": "dark" } }
```

```json title="~/.config/grove/config.json"
{ "agents": [{ "name": "aider", "command": "aider --model sonnet" }] }
```

## See also

- [Project setup](configure-project.md): where each file goes.
- [Agents](configure-agents.md): the merge-by-name rule in action.
- [Configuration reference](configure-reference.md): every field, auto-generated from the model.
